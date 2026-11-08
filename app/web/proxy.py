"""Tinyproxy management routes; legacy Flask authentication remains shared."""
import os
import secrets
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, session
from ..core.files import file_lock

from ..core.tinyproxy import ConfigError, DIRECTIVES, RevisionConflict, TinyproxyConfigStore, summary, validate

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


def register_proxy_api(app, requires_auth, authenticated, manager, audit):
    routes = Blueprint('proxy', __name__)

    def csrf_token():
        if 'proxy_csrf' not in session:
            session['proxy_csrf'] = secrets.token_urlsafe(32)
        return session['proxy_csrf']

    @routes.before_request
    def protect_changes():
        if request.method in ('POST', 'PUT', 'DELETE'):
            token = session.get('proxy_csrf', '')
            supplied = request.headers.get('X-CSRF-Token', '')
            if not token or not secrets.compare_digest(token, supplied):
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
                           canEdit=not ha_enabled() or managed_ha(),
                           canActivate=activation_supported() and (not ha_enabled() or managed_ha()),
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
