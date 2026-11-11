"""Tinyproxy management routes; legacy Flask authentication remains shared."""
import os
import secrets
import ipaddress
import json
import time
from stat import S_IMODE
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, session
from ..core.files import file_lock
from ..core.ha_policy import MAX_TRANSFER, baseline, issue_invite, merge_policy, parse_invite, sign_policy, verify_policy

from ..core.tinyproxy import ConfigError, DIRECTIVES, RevisionConflict, TinyproxyConfigStore, atomic_write, summary, validate
from . import ha_replication as replication

PENDING_CHANGES = 'Configuration changes are pending. Activate them through Proxy configuration first.'


def config_store():
    return TinyproxyConfigStore(os.getenv('TINYPROXY_CONFIG_PATH', '/etc/tinyproxy/tinyproxy.conf'))


def is_staged(store):
    return store.pending_path.exists() or store.draft_path.exists()


def has_pending_changes(filter_path):
    return is_staged(config_store()) or is_staged(TinyproxyConfigStore(filter_path))


def validate_filter(content, pattern_type):
    """Check native regex on Linux using the same POSIX family; never log rules."""
    import subprocess
    import tempfile
    if not isinstance(content, str) or len(content.encode('utf-8')) > 1024 * 1024 or '\x00' in content:
        return dict(valid=False, errors=['The filter must be text without NUL characters and no larger than 1 MiB.'], warnings=[])
    errors, warnings = [], []
    if pattern_type not in ('ere', 'bre', 'fnmatch'):
        errors.append('Unknown filter type.')
    elif pattern_type != 'fnmatch' and os.name != 'nt':
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8') as patterns:
                patterns.write('\n'.join(line for line in content.splitlines() if line.strip() and not line.lstrip().startswith('#')))
                patterns.flush()
                result = subprocess.run(['grep', '-E' if pattern_type == 'ere' else '-G', '-f', patterns.name],
                                        input='', text=True, capture_output=True, timeout=5, env={**os.environ, 'LC_ALL': 'C'})
            if result.returncode == 2:
                errors.append('One or more filter rules contain an invalid POSIX pattern.')
        except (OSError, subprocess.TimeoutExpired):
            errors.append('POSIX validation is unavailable on this server.')
    elif pattern_type != 'fnmatch':
        warnings.append('POSIX regex validation is only available on Linux; check before activation.')
    return dict(valid=not errors, errors=errors, warnings=warnings)


def ha_enabled():
    mode = os.getenv('DEPLOYMENT_MODE', '').lower()
    if mode:
        return mode == 'ha'
    # Preserve the topology of installations predating DEPLOYMENT_MODE.
    return bool(os.getenv('NODE0_ADDRESS') and os.getenv('NODE1_ADDRESS'))


def managed_ha():
    return ha_enabled() and os.getenv('PROXYPERMIT_MANAGED_HA', '').lower() == 'true'


def activation_supported():
    return os.name != 'nt' and os.getenv('DEMO_MODE', 'false').lower() != 'true'


def paired_standby():
    return managed_ha() and os.getenv('PROXYPERMIT_HA_NODE') == 'b' and replication.state('peer').is_file()


def record_published_policy(filter_path, logger):
    if not managed_ha() or os.getenv('PROXYPERMIT_HA_NODE') != 'a':
        return
    try:
        config = config_store().path.read_text(encoding='utf-8-sig')
        filters = Path(filter_path).read_text(encoding='utf-8-sig')
        replication.snapshot(config, filters)
        replication.state('publication-error').unlink(missing_ok=True)
    except (OSError, ValueError, KeyError):
        logger.error('Could not record the active HA policy for replication.')
        try:
            replication.write_state('publication-error', {'checked': int(time.time())})
        except OSError:
            logger.error('Could not record HA publication failure status.')


def register_proxy_api(app, requires_auth, authenticated, manager, audit):
    routes = Blueprint('proxy', __name__)

    def csrf_token():
        if 'proxy_csrf' not in session:
            session['proxy_csrf'] = secrets.token_urlsafe(32)
        return session['proxy_csrf']

    @routes.before_request
    def protect_changes():
        if request.endpoint == 'proxy.ha_peer':
            return None
        if request.method in ('POST', 'PUT', 'DELETE'):
            token = session.get('proxy_csrf', '')
            supplied = request.headers.get('X-CSRF-Token', '')
            if not token or not supplied.isascii() or not secrets.compare_digest(token, supplied):
                return jsonify(error='Session expired. Reload the page.'), 403
        return None

    @routes.route('/proxy')
    def page():
        headers = {'Cache-Control': 'no-store'}
        if not authenticated():
            return render_template('signin.html', destination='/proxy'), 401, headers
        return render_template('proxy.html', csrf_token=csrf_token(), ha_enabled=ha_enabled(), managed_ha=managed_ha()), 200, headers

    @routes.route('/api/proxy/config')
    @requires_auth
    def read_config():
        try:
            result = config_store().read()
            return jsonify(**result, csrfToken=csrf_token(), schema=list(DIRECTIVES.values()),
                           deploymentMode='ha' if ha_enabled() else 'standalone',
                           canEdit=(not ha_enabled() or managed_ha()) and not paired_standby(),
                           canActivate=activation_supported() and (not ha_enabled() or managed_ha()) and not paired_standby(),
                           filterStaged=is_staged(TinyproxyConfigStore(manager.whitelist_path)),
                           summary=summary(result['content']))
        except (OSError, ConfigError):
            return jsonify(error='Cannot read configuration. Check the path and file permissions.'), 500

    @routes.route('/api/proxy/config/validate', methods=['POST'])
    @requires_auth
    def validate_config():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('content'), str):
            return jsonify(error='content must be text.'), 400
        return jsonify(**validate(data['content']))

    @routes.route('/api/proxy/config', methods=['PUT'])
    @requires_auth
    def save_config():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('content'), str) or not isinstance(data.get('revision'), str):
            return jsonify(error='content and revision are required.'), 400
        if type(data.get('activate', False)) is not bool:
            return jsonify(error='activate must be true or false.'), 400
        activating = data.get('activate', False)
        if activating and not activation_supported():
            return jsonify(error='Activation requires a Linux installation with Tinyproxy.'), 400
        if ha_enabled() and not managed_ha():
            return jsonify(error='Manage HA proxy configuration on each node during maintenance.'), 409
        try:
            # Keep the managed filter stable through restart and recovery too.
            with file_lock(str(manager.whitelist_path) + '.lock'):
                filters = TinyproxyConfigStore(manager.whitelist_path, mode=0o644)
                if activating and filters.draft_path.exists():
                    filters, _ = filter_store(data['content'])
                attempts = 0

                def activate():
                    nonlocal attempts
                    attempts += 1
                    if attempts > 1:
                        filters.restore_pending()
                    else:
                        filters.publish_draft()
                    return manager.restart_tinyproxy()

                result = config_store().save(data['content'], data['revision'], activate if activating else None)
                if activating:
                    filters.finish_activation()
                    filters.draft_path.unlink(missing_ok=True)
                    record_published_policy(manager.whitelist_path, app.logger)
            audit('tinyproxy_config_saved', revision=result['revision'], activated=activating)
            return jsonify(success=True, **result)
        except RevisionConflict as exc:
            return jsonify(error=str(exc)), 409
        except ConfigError as exc:
            return jsonify(error=str(exc)), 400
        except (OSError, TimeoutError):
            return jsonify(error='Save failed. Check file permissions and available disk space.'), 500

    def filter_store(content=None):
        config = summary(config_store().read()['content'] if content is None else content)
        if config['filterMode'] == 'disabled':
            raise ConfigError('Configure Filter in the proxy settings first.')
        if Path(config['filterPath']).resolve() != manager.whitelist_path.resolve():
            raise ConfigError('Filter differs from WHITELIST_PATH. Update the installation configuration before managing this file.')
        return TinyproxyConfigStore(manager.whitelist_path,
                                    validator=lambda text: validate_filter(text, config['filterType'].lower()), mode=0o644), config

    def activate_shared_policy(store, filters, config, content, updated, rules):
        current = baseline(config, content)
        if current == baseline(updated, rules):
            return dict(success=True, revision=current[0])
        store.mode = S_IMODE(store.path.stat().st_mode)
        filters.mode = S_IMODE(filters.path.stat().st_mode)
        backup_name = 'ha-policy-' + secrets.token_hex(12) + '.json'
        store.backup_dir.mkdir(exist_ok=True, mode=0o700)
        atomic_write(store.backup_dir / backup_name, json.dumps(dict(config=config, filters=content)))
        attempts = 0

        def apply_and_recover():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                filters.publish_content(rules, content)
            else:
                filters.restore_pending()
            return manager.restart_tinyproxy()

        result = store.save(updated, current[0], apply_and_recover)
        filters.finish_activation()
        audit('ha_policy_activated', revision=result['revision'], policy_backup=backup_name)
        return dict(success=True, policyBackup=backup_name, **result)

    def reconcile_policy():
        if (not managed_ha() or not activation_supported() or os.getenv('PROXYPERMIT_HA_NODE') != 'b'
                or not Path('/etc/proxypermit/ha-firewall.json').is_file()):
            return
        with file_lock(replication.state('pull-lock')):
            peer = replication.read_state('peer')
            if not peer:
                return
            previous = replication.read_state('applied-policy')
            last_status = replication.read_state('replication-status') or {}
            primary = {key: last_status[key] for key in ('primaryRevision', 'primaryChecked') if key in last_status}
            try:
                policy = replication.fetch_primary(peer['pin'], peer['key'], 'policy',
                                                   previous['revision'] if previous else '')
                if (not isinstance(policy, dict) or policy.get('cluster') != replication.cluster_id()
                        or any(not isinstance(policy.get(key), str) for key in ('config', 'filters', 'revision'))):
                    raise ConfigError('Invalid published HA policy.')
                primary = dict(primaryRevision=policy['revision'], primaryChecked=int(time.time()))
                with file_lock(str(manager.whitelist_path) + '.lock'):
                    store = config_store()
                    if has_pending_changes(manager.whitelist_path):
                        raise RevisionConflict('Publish or discard local drafts before transferring HA policy.')
                    config = store.path.read_text(encoding='utf-8-sig')
                    filters, _ = filter_store(config)
                    content = filters.path.read_text(encoding='utf-8-sig')
                    current = baseline(config, content)
                    updated = merge_policy(config, policy['config'])
                    desired = baseline(updated, policy['filters'])
                    if previous and current != previous['baseline'] and current != desired:
                        raise RevisionConflict('The standby policy has local changes. Review the active files.')
                    if not previous or previous['revision'] != policy['revision']:
                        filters, _ = filter_store(updated)
                        activate_shared_policy(store, filters, config, content, updated, policy['filters'])
                        replication.write_state('applied-policy', dict(revision=policy['revision'],
                                                                        baseline=desired))
                replication.write_state('replication-status', dict(state='synchronized', revision=policy['revision'],
                    checked=int(time.time()), **primary))
            except (OSError, ValueError, KeyError, TimeoutError, EOFError):
                replication.write_state('replication-status', dict(state='pending',
                    revision=previous['revision'] if previous else None, checked=int(time.time()), **primary))
                raise

    app.extensions['ha_policy_reconcile'] = reconcile_policy

    @routes.route('/internal/ha/<action>', methods=['GET', 'POST'])
    def ha_peer(action):
        if (not managed_ha() or not activation_supported() or os.getenv('PROXYPERMIT_HA_NODE') != 'a'
                or (not request.is_secure and request.remote_addr not in ('127.0.0.1', '::1'))):
            return '', 404
        token = request.headers.get('Authorization', '').removeprefix('Bearer ')
        if len(token) != 64 or not token.isascii():
            return '', 403
        with file_lock(replication.state('pair-lock')):
            if action == 'enroll' and request.method == 'POST':
                invite = replication.read_state('enrollment')
                if invite and invite['expires'] > time.time() and secrets.compare_digest(invite['key'], token):
                    key = secrets.token_hex(32)
                    replication.write_state('peer', dict(key=key))
                    replication.state('enrollment').unlink()
                    return jsonify(key=key, cluster=invite['cluster'], topology=invite.get('topology')), 200, {'Cache-Control': 'no-store'}
            if action == 'policy' and request.method == 'GET':
                peer = replication.read_state('peer')
                if peer and secrets.compare_digest(peer['key'], token):
                    acknowledged = request.headers.get('X-HA-Applied-Revision', '')
                    if len(acknowledged) == 64 and all(char in '0123456789abcdef' for char in acknowledged):
                        replication.write_state('replication-status', dict(state='reported', revision=acknowledged, checked=int(time.time())))
                    policy = replication.read_state('published-policy')
                    if policy:
                        return jsonify(policy), 200, {'Cache-Control': 'no-store'}
                    return '', 409
        return '', 403

    @routes.post('/api/proxy/ha-replication/<action>')
    @requires_auth
    def manage_replication(action):
        if (not managed_ha() or not activation_supported() or not session.get('user')
                or (not request.is_secure and request.remote_addr not in ('127.0.0.1', '::1'))):
            return '', 403
        role = os.getenv('PROXYPERMIT_HA_NODE')
        try:
            if role == 'a' and action == 'invite':
                with file_lock(str(manager.whitelist_path) + '.lock'):
                    if has_pending_changes(manager.whitelist_path):
                        raise RevisionConflict('Publish or discard local drafts before transferring HA policy.')
                    if not replication.read_state('published-policy'):
                        record_published_policy(manager.whitelist_path, app.logger)
                    if replication.state('publication-error').exists() or not replication.read_state('published-policy'):
                        raise ConfigError('The primary does not have a published HA policy.')
                with file_lock(replication.state('pair-lock')):
                    if replication.read_state('peer'):
                        raise ConfigError('Revoke the current pairing before adding another node.')
                    code = replication.new_pairing_code()
                audit('ha_replication_invited')
                return jsonify(code=code)
            if role == 'b' and action == 'join':
                data = request.get_json(silent=True)
                invite = replication.parse_pairing_code(data.get('code') if isinstance(data, dict) else None)
                with file_lock(replication.state('pull-lock')):
                    if replication.read_state('peer'):
                        raise ConfigError('This node already has a replication peer.')
                    enrolled = replication.fetch_primary(invite['pin'], invite['key'], 'enroll')
                    key = enrolled.get('key', '') if isinstance(enrolled, dict) else ''
                    if not isinstance(key, str) or len(key) != 64 or any(char not in '0123456789abcdef' for char in key):
                        raise ConfigError('The primary did not issue a valid peer credential.')
                    replication.write_state('peer', dict(pin=invite['pin'], key=key))
                audit('ha_replication_joined')
                return jsonify(success=True)
            if action == 'disconnect' and role in ('a', 'b'):
                with file_lock(replication.state('pair-lock' if role == 'a' else 'pull-lock')):
                    for name in ('peer', 'enrollment', 'applied-policy', 'replication-status'):
                        replication.state(name).unlink(missing_ok=True)
                audit('ha_replication_disconnected')
                return jsonify(success=True)
            if action == 'status':
                policy = replication.read_state('published-policy') or {}
                status = replication.read_state('replication-status')
                if status:
                    fresh = time.time() - status['checked'] < 90
                    matching = (status.get('revision') == policy.get('revision') if role == 'a'
                                else status['state'] == 'synchronized')
                    if role == 'a' and replication.state('publication-error').exists():
                        matching = False
                    status['state'] = 'synchronized' if fresh and matching else 'pending'
                return jsonify(paired=bool(replication.read_state('peer')),
                               status=status, revision=policy.get('revision'))
            return '', 403
        except (ConfigError, KeyError, OSError, TimeoutError, ValueError):
            return jsonify(error='HA replication request failed. Check the peer and TLS configuration.'), 409

    @routes.post('/api/proxy/ha-policy/<action>')
    @requires_auth
    def ha_policy(action):
        if not managed_ha() or not activation_supported() or not session.get('user'):
            return jsonify(error='HA policy transfer requires a managed HA installation.'), 403
        if not request.is_secure and request.remote_addr not in ('127.0.0.1', '::1'):
            return jsonify(error='Use HTTPS or a local SSH tunnel for HA policy transfer.'), 403
        role = os.getenv('PROXYPERMIT_HA_NODE')
        if (role, action) not in {('a', 'export'), ('b', 'invite'), ('b', 'preview'), ('b', 'apply')}:
            return jsonify(error='This action is not available on this HA node.'), 403
        limit = min(MAX_TRANSFER, request.max_content_length or MAX_TRANSFER)
        if request.content_length is None or request.content_length > limit:
            return jsonify(error='The HA transfer is too large.'), 413
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='Invalid HA policy transfer.'), 400
        state = Path(os.getenv('SESSION_STATE_FILE', '/var/lib/proxypermit/session-state.json')).with_name('ha-policy-invite.json')
        try:
            addresses = [str(ipaddress.IPv4Address(os.environ[key])) for key in ('NODE0_ADDRESS', 'NODE1_ADDRESS', 'VIP_ADDRESS')]
            if len(set(addresses)) != 3:
                raise ConfigError('Invalid installed HA topology.')
            cluster = '/'.join(addresses)
            with file_lock(str(manager.whitelist_path) + '.lock'):
                store = config_store()
                if has_pending_changes(manager.whitelist_path):
                    raise RevisionConflict('Publish or discard local drafts before transferring HA policy.')
                config = store.path.read_text(encoding='utf-8-sig')
                filters, _ = filter_store(config)
                content = filters.path.read_text(encoding='utf-8-sig')
                current = baseline(config, content)
                if action == 'invite':
                    invite = json.dumps(issue_invite(cluster, current), separators=(',', ':'))
                    atomic_write(state, invite)
                    audit('ha_policy_invitation_created')
                    return jsonify(code=invite)
                if action == 'export':
                    invite = parse_invite(data.get('code'), cluster)
                    code = sign_policy(invite, config, content)
                    if len(json.dumps({'code': code}, ensure_ascii=False).encode('utf-8')) + 256 > limit:
                        raise ConfigError('The HA transfer is too large.')
                    audit('ha_policy_exported')
                    return jsonify(code=code)
                if not state.is_file():
                    raise ConfigError('Create a new invitation on node B first.')
                invite = parse_invite(state.read_text(encoding='utf-8'), cluster)
                policy = verify_policy(data.get('code'), invite)
                if current != invite['baseline']:
                    raise RevisionConflict('The active policy changed after the invitation was created.')
                updated = merge_policy(config, policy['config'])
                filters, _ = filter_store(updated)
                for checks in (validate(updated), filters.validate(policy['filters'])):
                    if not checks['valid']:
                        raise ConfigError('\n'.join(checks['errors']))
                next_revision = baseline(updated, policy['filters'])
                if action == 'preview':
                    return jsonify(configChanged=config != updated, filtersChanged=content != policy['filters'],
                                   revision=next_revision)
                if data.get('confirmed') is not True or data.get('revision') != next_revision:
                    raise ConfigError('Review and confirm the HA policy activation first.')
                state.unlink()
                return jsonify(activate_shared_policy(store, filters, config, content, updated, policy['filters']))
        except RevisionConflict as exc:
            return jsonify(error=str(exc)), 409
        except ConfigError as exc:
            return jsonify(error=str(exc)), 400
        except (OSError, TimeoutError, KeyError, ipaddress.AddressValueError):
            return jsonify(error='HA policy transfer failed. Check the installation and service status.'), 500

    @routes.route('/api/proxy/<kind>/draft', methods=['DELETE'])
    @requires_auth
    def discard_draft(kind):
        if kind not in ('config', 'filter'):
            return jsonify(error='Unknown filter type.'), 404
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('revision'), str):
            return jsonify(error='revision is required.'), 400
        try:
            with file_lock(str(manager.whitelist_path) + '.lock'):
                store = config_store() if kind == 'config' else TinyproxyConfigStore(manager.whitelist_path)
                store.discard_draft(data['revision'])
            return jsonify(success=True)
        except RevisionConflict as exc:
            return jsonify(error=str(exc)), 409
        except (OSError, TimeoutError):
            return jsonify(error='Save failed. Check file permissions and available disk space.'), 500

    @routes.route('/api/proxy/filter', methods=['GET', 'PUT'])
    @requires_auth
    def native_filter():
        try:
            filters, config = filter_store()
            if request.method == 'GET':
                return jsonify(**filters.read(), filterType=config['filterType'], filterMode=config['filterMode'])
            if ha_enabled() and not managed_ha():
                return jsonify(error='Direct filter editing requires a standalone deployment. Use the existing ERE domain editor and sync on this HA cluster.'), 409
            data = request.get_json(silent=True)
            if not isinstance(data, dict) or not isinstance(data.get('content'), str) or not isinstance(data.get('revision'), str):
                return jsonify(error='content and revision are required.'), 400
            with file_lock(str(manager.whitelist_path) + '.lock'):
                filters, _ = filter_store()
                result = filters.save(data['content'], data['revision'])
            audit('tinyproxy_filter_saved', revision=result['revision'])
            return jsonify(success=True, **result)
        except RevisionConflict as exc:
            return jsonify(error=str(exc)), 409
        except ConfigError as exc:
            return jsonify(error=str(exc)), 400
        except (OSError, TimeoutError):
            return jsonify(error='Cannot read or save the filter file.'), 500

    app.register_blueprint(routes)

    @app.before_request
    def guard_legacy_filter_editor():
        if request.path.startswith('/api/sync/') and managed_ha():
            return jsonify(error='This cluster manages proxy policy per node. Legacy synchronization is disabled.'), 409
        if request.path.startswith('/api/sync/') and not ha_enabled():
            return jsonify(error='Synchronization is disabled in standalone mode.'), 409
        # Domain -> ERE conversion must never silently corrupt BRE/fnmatch/URL files.
        if request.method not in ('POST', 'PUT', 'DELETE'):
            return None
        managed_write = (request.path.startswith('/api/entries') or request.path == '/api/import'
                         or request.path.startswith('/api/proxy/ha-policy/')
                         or (request.method == 'PUT' and request.path in ('/api/proxy/config', '/api/proxy/filter')))
        if managed_write and paired_standby():
            return jsonify(error='Publish shared proxy policy on the primary HA node.'), 409
        if not (request.path.startswith('/api/entries') or request.path == '/api/import'):
            return None
        path = config_store().path
        if not path.exists():
            return None  # Old installs/demo may only supply a whitelist path.
        try:
            if has_pending_changes(manager.whitelist_path):
                return jsonify(error=PENDING_CHANGES), 409
            config = summary(path.read_text(encoding='utf-8-sig'))
            compatible = (config['filterMode'] != 'disabled' and config['filterType'].lower() == 'ere'
                          and not config['filterURLs'] and
                          Path(config['filterPath']).resolve() == manager.whitelist_path.resolve())
        except (OSError, ConfigError, ValueError):
            compatible = False
        if not compatible:
            return jsonify(error='The domain editor requires ERE domain filtering on WHITELIST_PATH. Manage the filter in Proxy configuration.'), 409
        return None
