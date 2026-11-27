"""Tinyproxy management routes; legacy Flask authentication remains shared."""
import os
import secrets
import ipaddress
import json
import time
from stat import S_IMODE
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, session, redirect
from ..core.activation import service_activation_action
from ..core import ha_credentials as credentials
from ..core.ha_policy_order import PolicyOrderError
from ..core import ha_certificates as certificates
from ..core import cluster
from ..core.native_filter import validate_filter
from ..core.files import LockTimeout, file_lock
from ..core.access_safety import ensure_access, read_profile
from ..core.ha_policy import MAX_TRANSFER, baseline, issue_invite, merge_policy, parse_invite, sign_policy, verify_policy

from ..core.tinyproxy import (ConfigError, DIRECTIVES, PolicyCheckpointError, RevisionConflict,
                             TinyproxyConfigStore, atomic_write, summary, validate, revision as config_revision)
from . import ha_replication as replication
from .permissions import POLICY_DIRECTIVES, PolicyPermissionError, ensure_policy_only

PENDING_CHANGES = 'Configuration changes are pending. Activate them through Proxy configuration first.'
RECOVERY_REQUIRED = 'A policy publication was interrupted. Ask a system administrator to recover the previous policy before continuing.'


def config_store():
    return TinyproxyConfigStore(os.getenv('TINYPROXY_CONFIG_PATH', '/etc/tinyproxy/tinyproxy.conf'))


def is_staged(store):
    return store.pending_path.exists() or store.draft_path.exists()


def policy_recovery_required(config, filters):
    return config.pending_path.exists() or filters.pending_path.exists()


def has_pending_changes(filter_path):
    return is_staged(config_store()) or is_staged(TinyproxyConfigStore(filter_path))


def domain_editor_error(filter_path):
    """Return why the domain editor cannot safely modify the current native file."""
    store = config_store()
    path = store.path
    if policy_recovery_required(store, TinyproxyConfigStore(filter_path)):
        return RECOVERY_REQUIRED
    if not path.exists():
        return None  # Older installations may supply only a filter path.
    try:
        if has_pending_changes(filter_path):
            return PENDING_CHANGES
        config = summary(path.read_text(encoding='utf-8-sig'))
        compatible = (config['filterMode'] != 'disabled' and config['filterType'].lower() == 'ere'
                      and not config['filterURLs'] and
                      Path(config['filterPath']).resolve() == Path(filter_path).resolve())
    except (OSError, ConfigError, ValueError):
        compatible = False
    if not compatible:
        return 'The domain editor requires ERE domain filtering on WHITELIST_PATH. Manage the filter in Proxy configuration.'
    return None




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
        if not app.extensions['has_permission']('proxy'):
            return redirect('/settings')
        return render_template('proxy.html', csrf_token=csrf_token(), ha_enabled=ha_enabled(), managed_ha=managed_ha()), 200, headers

    @routes.route('/api/proxy/config')
    @requires_auth
    def read_config():
        try:
            with file_lock(str(manager.whitelist_path) + '.lock'):
                store = config_store()
                filters = TinyproxyConfigStore(manager.whitelist_path)
                recovery_required = policy_recovery_required(store, filters)
                editable = not recovery_required and (not ha_enabled() or managed_ha()) and not paired_standby()
                result = store.read()
                active_content = store.path.read_text(encoding='utf-8-sig') if store.path.exists() else ''
                access_safety = read_profile()
                access_valid = True
                try:
                    ensure_access(active_content)
                    ensure_access(result['content'])
                except ConfigError as exc:
                    result['errors'].append(str(exc)); result['valid'] = False; access_valid = False
                return jsonify(**result, csrfToken=csrf_token(), schema=[spec for key, spec in DIRECTIVES.items()
                               if app.extensions['has_permission']('admin') or key in POLICY_DIRECTIVES],
                               accessSafety=access_safety, accessSafetyValid=access_valid, activeContent=active_content,
                               deploymentMode='ha' if ha_enabled() else 'standalone',
                               canEdit=editable, canActivate=activation_supported() and editable,
                               recoveryRequired=recovery_required, filterStaged=is_staged(filters),
                               summary=summary(result['content']))
        except (OSError, ConfigError):
            return jsonify(error='Cannot read configuration. Check the path and file permissions.'), 500

    @routes.route('/api/proxy/config/validate', methods=['POST'])
    @requires_auth
    def validate_config():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('content'), str):
            return jsonify(error='content must be text.'), 400
        result = validate(data['content'])
        try:
            result['activationAction'] = service_activation_action(config_store().path.read_text(encoding='utf-8-sig'), data['content'])
        except (OSError, ConfigError):
            result['activationAction'] = 'restart'
        try:
            ensure_access(data['content'])
            if not app.extensions['has_permission']('admin'):
                with file_lock(str(manager.whitelist_path) + '.lock'):
                    ensure_policy_only(config_store().path.read_text(encoding='utf-8-sig'), data['content'])
        except (ConfigError, ValueError) as exc:
            result['errors'].append(str(exc)); result['valid'] = False
        return jsonify(**result)

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
            ensure_access(data['content'])
            # Keep the managed filter stable through restart and recovery too.
            with file_lock(str(manager.whitelist_path) + '.lock'):
                store = config_store()
                filters = TinyproxyConfigStore(manager.whitelist_path, mode=0o644)
                if policy_recovery_required(store, filters):
                    return jsonify(error=RECOVERY_REQUIRED), 409
                if not app.extensions['has_permission']('admin'):
                    ensure_policy_only(store.path.read_text(encoding='utf-8-sig'), data['content'])
                if activating and filters.draft_path.exists():
                    filters, _ = filter_store(data['content'])
                action = service_activation_action(config_store().path.read_text(encoding='utf-8-sig'), data['content'])
                if activating and data.get('activationAction') == 'reload' and action != 'reload':
                    return jsonify(error='Activation requirements changed. Review the configuration again.'), 409
                attempts = 0
                service_attempted = False

                def activate():
                    nonlocal attempts, service_attempted
                    attempts += 1
                    if attempts > 1:
                        filters.restore_pending()
                        if not service_attempted:
                            return None, None
                    else:
                        filters.publish_draft()
                    service_attempted = True
                    with file_lock(store.activation_lock):
                        return manager.reload_tinyproxy() if action == 'reload' else manager.restart_tinyproxy()

                result = store.save(data['content'], data['revision'], activate if activating else None)
                if activating:
                    filters.finish_activation()
                    filters.draft_path.unlink(missing_ok=True)
                    record_published_policy(manager.whitelist_path, app.logger)
            if activating:
                result['activationAction'] = action
            audit('tinyproxy_config_saved', revision=result['revision'], activated=activating)
            return jsonify(success=True, **result)
        except RevisionConflict as exc:
            return jsonify(error=str(exc)), 409
        except PolicyPermissionError as exc:
            return jsonify(error=str(exc)), 403
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
        ensure_access(updated)
        current = baseline(config, content)
        if current == baseline(updated, rules):
            return dict(success=True, revision=config_revision(config))
        store.mode = S_IMODE(store.path.stat().st_mode)
        filters.mode = S_IMODE(filters.path.stat().st_mode)
        backup_name = 'ha-policy-' + secrets.token_hex(12) + '.json'
        store.backup_dir.mkdir(exist_ok=True, mode=0o700)
        atomic_write(store.backup_dir / backup_name, json.dumps(dict(config=config, filters=content)))
        action = service_activation_action(config, updated)
        attempts = 0
        service_attempted = False

        def apply_and_recover():
            nonlocal attempts, service_attempted
            attempts += 1
            if attempts == 1:
                filters.publish_content(rules, content)
            else:
                filters.restore_pending()
                if not service_attempted:
                    return None, None
            service_attempted = True
            with file_lock(store.activation_lock):
                return manager.reload_tinyproxy() if action == 'reload' else manager.restart_tinyproxy()

        result = store.save(updated, config_revision(config), apply_and_recover)
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
                if peer.get('rotation'):
                    peer = replication.finish_key_rotation(peer)
                pruned = certificates.prune(peer)
                if pruned != peer:
                    replication.write_state('peer', pruned)
                    peer = pruned
                def fetch_policy():
                    return replication.fetch_primary(certificates.allowed_pins(peer), peer['key'], 'policy',
                                                   previous['revision'] if previous else '',
                        credential_version=peer.get('credentialVersion', ''),
                        certificate_prepared=(peer.get('certificatePending') or {}).get('id', ''),
                        certificate_version=peer.get('certificateVersion', ''))
                policy = fetch_policy()
                replication.validate_published(policy, previous)
                if policy.get('certificateTransition') is not None:
                    offer = policy['certificateTransition']
                    updated = certificates.stage(peer, offer, getattr(policy, 'verified_pin', None))
                    updated = certificates.promote(updated, offer, getattr(policy, 'verified_pin', None))
                    if updated != peer:
                        replication.write_state('peer', updated)
                        peer = updated
                        audit('ha_certificate_pin_updated', operation=offer['id'],
                              phase='confirmed' if peer.get('certificateVersion') == offer['id'] else 'staged')
                if policy.get('credentialRotation') is not None:
                    if type(policy.get('credentialProtocol')) is not int or policy['credentialProtocol'] != 1:
                        raise ConfigError('Unsupported peer-key transition protocol.')
                    offer = credentials.validate_offer(policy['credentialRotation'])
                    peer = {**peer, 'rotation': offer}
                    replication.write_state('peer', peer)
                    peer = replication.finish_key_rotation(peer)
                    policy = fetch_policy()
                    replication.validate_published(policy, previous)
                    audit('ha_peer_key_received')
                if (peer.get('credentialState') == 'committed'
                        and policy.get('credentialVersion') == peer.get('credentialVersion')):
                    peer = {**peer, 'credentialState': 'complete'}
                    replication.write_state('peer', peer)
                    audit('ha_peer_key_confirmed')
                primary = dict(primaryRevision=policy['revision'], primaryChecked=int(time.time()))
                with file_lock(str(manager.whitelist_path) + '.lock'):
                    store = config_store()
                    if policy_recovery_required(store, TinyproxyConfigStore(manager.whitelist_path)):
                        raise PolicyCheckpointError(RECOVERY_REQUIRED)
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
                    if (not previous or previous['revision'] != policy['revision']
                            or previous.get('generation') != policy.get('generation')):
                        applied = dict(revision=policy['revision'], baseline=desired)
                        if policy.get('generation') is not None:
                            applied['generation'] = policy['generation']
                        replication.write_state('applied-policy', applied)
                replication.write_state('replication-status', dict(state='synchronized', revision=policy['revision'],
                    checked=int(time.time()), **primary))
            except (OSError, ValueError, KeyError, TimeoutError, EOFError) as error:
                replication.write_state('replication-status', dict(state='pending',
                    revision=previous['revision'] if previous else None, checked=int(time.time()),
                    reason='stale-primary' if isinstance(error, PolicyOrderError) else 'request-failed', **primary))
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
                if (invite and not replication.read_state('peer') and invite['expires'] > time.time()
                        and secrets.compare_digest(invite['key'], token)):
                    key = secrets.token_hex(32)
                    replication.write_state('peer', dict(key=key))
                    replication.delete_state('enrollment')
                    return jsonify(key=key, cluster=invite['cluster'], topology=invite.get('topology')), 200, {'Cache-Control': 'no-store'}
            if action == 'policy' and request.method == 'GET':
                peer = replication.read_state('peer')
                if credentials.authorized(peer, token):
                    if request.headers.get('X-HA-Credential-Protocol') == '1':
                        peer = {**peer, 'credentialProtocol': 1, 'credentialChecked': int(time.time())}
                        replication.write_state('peer', peer)
                    certificate = replication.certificate_transition()
                    if request.headers.get('X-HA-Certificate-Protocol') == '1':
                        peer = {**peer, 'certificateProtocol': 1}
                        replication.write_state('peer', peer)
                    if certificate:
                        updated = dict(peer)
                        if request.headers.get('X-HA-Certificate-Prepared') == certificate['id']:
                            updated['certificatePrepared'] = certificate['id']
                        if (certificate['phase'] == 'committed'
                                and request.headers.get('X-HA-Certificate-Version') == certificate['id']):
                            updated['certificateVersion'] = certificate['id']
                        if updated != peer:
                            replication.write_state('peer', updated)
                            peer = updated
                    confirmed = credentials.confirm_rotation(peer, token, request.headers.get('X-HA-Credential-Version', ''))
                    if confirmed != peer:
                        replication.write_state('peer', confirmed)
                        peer = confirmed
                        audit('ha_peer_key_rotation_completed')
                    acknowledged = request.headers.get('X-HA-Applied-Revision', '')
                    if len(acknowledged) == 64 and all(char in '0123456789abcdef' for char in acknowledged):
                        replication.write_state('replication-status', dict(state='reported', revision=acknowledged, checked=int(time.time())))
                    policy = replication.read_state('published-policy')
                    if policy:
                        return jsonify(**policy, credentialProtocol=1, credentialRotation=credentials.rotation_offer(peer),
                                       credentialVersion=peer.get('credentialVersion'), certificateTransition=certificate), 200, {'Cache-Control': 'no-store'}
                    return '', 409
            if action == 'credential-commit' and request.method == 'POST':
                data = request.get_json(silent=True)
                peer = replication.read_state('peer')
                try:
                    committed = credentials.commit_rotation(peer, token, data.get('id') if isinstance(data, dict) else None)
                except ConfigError:
                    return '', 403
                replication.write_state('peer', committed)
                if committed != peer:
                    audit('ha_peer_key_rotation_committed', operation=committed['credentialVersion'])
                return jsonify(committed=True, id=committed['credentialVersion'], cluster=replication.cluster_id()), 200, {'Cache-Control': 'no-store'}
        return '', 403

    @routes.post('/api/proxy/ha-replication/<action>')
    @requires_auth
    def manage_replication(action):
        if (not managed_ha() or not activation_supported() or not session.get('user')
                or (not request.is_secure and request.remote_addr not in ('127.0.0.1', '::1'))):
            return '', 403
        role = os.getenv('PROXYPERMIT_HA_NODE')
        try:
            if role == 'a' and action == 'rotate-key':
                data = request.get_json(silent=True)
                error = app.extensions['reauthenticate_admin_change'](data)
                if error is not None:
                    return error
                with file_lock(replication.state('pair-lock')):
                    if not replication.read_state('published-policy'):
                        raise ConfigError('Publish a policy before rotating the peer key.')
                    peer = replication.read_state('peer') or {}
                    certificate = replication.certificate_transition()
                    if certificate and (certificate['phase'] == 'offered' and time.time() < certificate['expires']
                                        or certificate['phase'] == 'committed' and peer.get('certificateVersion') != certificate['id']):
                        return jsonify(error='Complete the certificate transition before rotating the peer key.'), 409
                    if peer.get('credentialProtocol') != 1 or not 0 <= time.time() - peer.get('credentialChecked', 0) < 90:
                        return jsonify(error='Upgrade both nodes and wait for a successful synchronization before rotating the peer key.'), 409
                    peer = credentials.begin_rotation(peer)
                    replication.write_state('peer', peer)
                audit('ha_peer_key_rotation_started', operation=peer['rotation']['id'])
                return jsonify(success=True, credential=credentials.public_status(peer))
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
                data = request.get_json(silent=True)
                if not isinstance(data, dict) or data.get('confirmed') is not True:
                    return jsonify(error='Confirm HA pairing revocation before continuing.'), 400
                error = app.extensions['reauthenticate_admin_change'](data)
                if error is not None:
                    return error
                with file_lock(replication.state('pair-lock' if role == 'a' else 'pull-lock')):
                    for name in ('peer', 'enrollment', 'applied-policy', 'replication-status'):
                        replication.delete_state(name)
                audit('ha_replication_disconnected')
                return jsonify(success=True)
            if action == 'status':
                policy = replication.read_state('published-policy') or {}
                status = replication.read_state('replication-status')
                peer = replication.read_state('peer')
                credential = credentials.public_status(peer)
                if status:
                    fresh = time.time() - status['checked'] < 90
                    matching = (status.get('revision') == policy.get('revision') if role == 'a'
                                else status['state'] == 'synchronized')
                    if role == 'a' and replication.state('publication-error').exists():
                        matching = False
                    if credential['state'] in ('expired', 'invalid'):
                        matching = False
                    status['state'] = 'synchronized' if fresh and matching else 'pending'
                store = config_store()
                filters = TinyproxyConfigStore(manager.whitelist_path)
                if peer and policy_recovery_required(store, filters):
                    try:
                        # A normal publication holds the policy lock until its checkpoints are cleared.
                        with file_lock(str(filters.path) + '.lock', timeout=0):
                            if policy_recovery_required(store, filters):
                                status = {**(status or {}), 'state': 'pending', 'reason': 'local-recovery'}
                    except LockTimeout:
                        pass
                return jsonify(paired=bool(peer), credential=credential,
                               status=status, revision=policy.get('revision'), protection=cluster.read_status())
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
                config = config_store()
                filters = TinyproxyConfigStore(manager.whitelist_path)
                if policy_recovery_required(config, filters):
                    return jsonify(error=RECOVERY_REQUIRED), 409
                store = config if kind == 'config' else filters
                if kind == 'config' and not app.extensions['has_permission']('admin'):
                    ensure_policy_only(store.path.read_text(encoding='utf-8-sig'), store.read()['content'])
                store.discard_draft(data['revision'])
            return jsonify(success=True)
        except RevisionConflict as exc:
            return jsonify(error=str(exc)), 409
        except PolicyPermissionError as exc:
            return jsonify(error=str(exc)), 403
        except (OSError, TimeoutError):
            return jsonify(error='Save failed. Check file permissions and available disk space.'), 500

    @routes.route('/api/proxy/filter', methods=['GET', 'PUT'])
    @requires_auth
    def native_filter():
        try:
            if request.method == 'GET':
                filters, config = filter_store()
                return jsonify(**filters.read(), filterType=config['filterType'], filterMode=config['filterMode'])
            if ha_enabled() and not managed_ha():
                return jsonify(error='Direct filter editing requires a standalone deployment. Use the existing ERE domain editor and sync on this HA cluster.'), 409
            data = request.get_json(silent=True)
            if not isinstance(data, dict) or not isinstance(data.get('content'), str) or not isinstance(data.get('revision'), str):
                return jsonify(error='content and revision are required.'), 400
            with file_lock(str(manager.whitelist_path) + '.lock'):
                if policy_recovery_required(config_store(), TinyproxyConfigStore(manager.whitelist_path)):
                    return jsonify(error=RECOVERY_REQUIRED), 409
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
        error = domain_editor_error(manager.whitelist_path)
        if error:
            return jsonify(error=error), 409
        return None
