"""System-administrator-only HTTPS jobs with fresh authentication and fixed IPC."""
import json
import secrets
import subprocess
import time
import ipaddress
from urllib.parse import urlsplit

from flask import Blueprint, Response, jsonify, request, session
from ..core import management_https as control, management_tls
from ..core.accounts import account_for, account_role
from ..core.files import file_lock
from ..core.tinyproxy import atomic_write
from .settings import secure_access


def register_https_settings(app, requires_auth, paths, audit):
    routes = Blueprint('https_settings', __name__)

    @routes.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    def allowed():
        return bool(session.get('user') and secure_access() and control.available())

    @routes.get('/api/settings/https')
    @requires_auth
    def state():
        if not allowed():
            return jsonify(error='HTTPS web management is unavailable. Use HTTPS or a local SSH tunnel.'), 403
        try:
            value = control.snapshot()
            job = control.result()
            if job and job['phase'] == 'running' and time.time() - job['created'] > 210:
                job = {**job, 'phase': 'failed', 'error': 'HTTPS maintenance did not finish. Review OS recovery before retrying.'}
            if request.is_secure:
                session.modified = True  # Reissue the cookie with Secure after HTTP migration.
            return jsonify(**value, job=job)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            return jsonify(error='Cannot read HTTPS maintenance state. Review the node configuration.'), 503

    @routes.get('/api/settings/https/ca')
    @requires_auth
    def ca():
        if not allowed():
            return jsonify(error='HTTPS web management is unavailable. Use HTTPS or a local SSH tunnel.'), 403
        try:
            content = control.root_text(management_tls.FOLDER / 'ca.pem')
            if not content.startswith('-----BEGIN CERTIFICATE-----') or 'PRIVATE KEY' in content:
                raise ValueError('Invalid public CA.')
            return Response(content, content_type='application/x-pem-file',
                            headers={'Content-Disposition': 'attachment; filename="proxypermit-node-ca.pem"'})
        except (OSError, ValueError):
            return jsonify(error='Prepare a management certificate before downloading its CA.'), 409

    @routes.post('/api/settings/https/jobs')
    @requires_auth
    def queue():
        if not allowed():
            return jsonify(error='HTTPS web management is unavailable. Use HTTPS or a local SSH tunnel.'), 403
        token = session.get('settings_csrf', '')
        supplied = request.headers.get('X-CSRF-Token', '')
        if not token or not supplied.isascii() or not secrets.compare_digest(token, supplied):
            return jsonify(error='Session expired. Reload the page.'), 403
        data = request.get_json(silent=True)
        if (not isinstance(data, dict) or data.get('action') not in ('prepare', 'activate', 'renew')
                or data.get('confirmed') is not True or not isinstance(data.get('revision'), str)
                or (data['action'] == 'activate' and data.get('trusted') is not True)):
            return jsonify(error='Review and confirm the HTTPS change first.'), 400
        error = app.extensions['reauthenticate_admin_change'](data)
        if error is not None:
            return error
        path, _ = paths()
        account = account_for(path, session['user']) if path else None
        if not account or account_role(account) != 'admin':
            return jsonify(error='Authentication required'), 403
        try:
            with file_lock(str(control.REQUEST) + '.lock'):
                state = control.snapshot()
                if data['revision'] != state['revision']:
                    return jsonify(error='HTTPS settings changed. Review the current node again.'), 409
                if not state['canRenew' if data['action'] == 'renew' else 'canEnable']:
                    return jsonify(error='This HTTPS certificate is managed outside ProxyPermit. Use its issuer\'s renewal process.'), 409
                if data['action'] == 'activate':
                    host = (urlsplit(request.url).hostname or '').rstrip('.').lower()
                    try:
                        identity = 'IP:' + str(ipaddress.ip_address(host))
                    except ValueError:
                        identity = 'DNS:' + host
                    if identity not in state['names']:
                        return jsonify(error='This address is not covered by the prepared certificate. Open an OS-approved management address first.'), 400
                if control.REQUEST.exists() or control.REQUEST.is_symlink():
                    return jsonify(error='HTTPS maintenance is already queued or running.'), 409
                job = control.result()
                if job and job['phase'] == 'running':
                    return jsonify(error='HTTPS maintenance is already queued or running.'), 409
                if control.unit_busy():
                    return jsonify(error='HTTPS maintenance is already queued or running.'), 409
                plan = data.get('plan', '')
                if not isinstance(plan, str) or (plan and (len(plan) != 32 or any(c not in '0123456789abcdef' for c in plan))):
                    return jsonify(error='Invalid input.'), 400
                identifier = secrets.token_hex(16)
                queued = dict(id=identifier, action=data['action'], revision=state['revision'], plan=plan,
                              username=session['user'], accountRevision=account['revision'], created=int(time.time()))
                atomic_write(control.REQUEST, json.dumps(queued), 0o600)
                audit('management_https_queued', request_id=identifier, action=data['action'], node=state['node'])
                try:
                    subprocess.run(control.COMMAND, check=True, capture_output=True, timeout=5)
                except subprocess.TimeoutExpired:
                    pass  # The job may already have started; reconcile its root-owned status.
                except (OSError, subprocess.SubprocessError):
                    control.REQUEST.unlink(missing_ok=True)
                    raise
            return jsonify(id=identifier, queued=True), 202
        except (OSError, ValueError, TimeoutError, subprocess.SubprocessError):
            return jsonify(error='HTTPS maintenance could not be queued. Review the fixed system service.'), 409

    app.register_blueprint(routes)
