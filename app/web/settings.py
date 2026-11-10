"""Authenticated account administration and local authenticator enrollment."""
import hashlib
import base64
import io
import ipaddress
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

import pyotp
import segno
from flask import Blueprint, jsonify, render_template, request, session
from werkzeug.security import generate_password_hash

from ..core.accounts import consume_totp, load_accounts, matching_totp_step
from ..core.files import file_lock
from ..core.ha import ha_firewall_rules, ha_topology
from ..core.tinyproxy import atomic_write
from .proxy import ha_enabled, managed_ha

HA_RETIRE_COMMAND = ('/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'start', 'proxypermit-ha-retire.service')


def ha_retirement_available():
    return (managed_ha() and Path('/etc/proxypermit/ha-firewall.json').is_file()
            and Path('/etc/systemd/system/proxypermit-ha-retire.service').is_file())


def secure_access():
    if request.is_secure:
        return True
    try:
        return ipaddress.ip_address(request.remote_addr or '').is_loopback
    except ValueError:
        return False


def register_settings(app, requires_auth, authenticated, paths, reauthenticate, audit):
    settings = Blueprint('settings', __name__)

    @settings.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @settings.get('/settings')
    def page():
        path, _ = paths()
        actor = session.get('user')
        if not authenticated():
            return render_template('signin.html', destination='/settings'), 403
        if not actor:
            return render_template('settings.html', sign_in=True), 403
        accounts = load_accounts(path) if path else {}
        return render_template('settings.html', actor=actor, accounts=accounts,
                               editable=bool(path and secure_access() and os.access(path.parent, os.W_OK)),
                               ha_enabled=ha_enabled(), managed_ha=managed_ha(),
                               ha_retire_available=bool(path and secure_access() and ha_retirement_available()),
                               csrf=session.setdefault('settings_csrf', secrets.token_urlsafe(32)))

    @settings.post('/api/settings/ha/preview')
    @requires_auth
    def preview_ha():
        if not session.get('user'):
            return jsonify(error='Authentication required'), 403
        csrf = session.get('settings_csrf', '')
        supplied = request.headers.get('X-CSRF-Token', '')
        if not csrf or not supplied.isascii() or not secrets.compare_digest(csrf, supplied):
            return jsonify(error='Session expired. Reload the page.'), 403
        data = request.get_json(silent=True)
        keys = ('node', 'interface', 'nodeA', 'nodeB', 'vip', 'clients', 'vrid')
        if not isinstance(data, dict) or any(not isinstance(data.get(key), str) or len(data[key]) > 64 for key in keys):
            return jsonify(error='Invalid input.'), 400
        try:
            vrid = data['vrid']
            if not vrid.isascii() or not vrid.isdecimal() or not 1 <= int(vrid) <= 255:
                raise ValueError('Choose a restricted client network and a VRRP ID between 1 and 255.')
            topology = ha_topology(*(data[key].strip() for key in keys[:-1]), int(vrid))
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError):
            return jsonify(error='Enter valid IPv4 addresses and network prefixes.'), 400
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(topology=topology, firewallRules=ha_firewall_rules(topology))

    @settings.post('/api/settings/ha/retire')
    @requires_auth
    def retire_ha():
        path, _ = paths()
        actor = session.get('user')
        if not path or not actor or not secure_access() or not ha_retirement_available():
            return jsonify(error='HA retirement is unavailable on this node.'), 403
        csrf = session.get('settings_csrf', '')
        supplied = request.headers.get('X-CSRF-Token', '')
        if not csrf or not supplied.isascii() or not secrets.compare_digest(csrf, supplied):
            return jsonify(error='Session expired. Reload the page.'), 403
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get('confirmed') is not True:
            return jsonify(error='Confirm that the peer is serving the virtual IP.'), 400
        password, otp = data.get('password'), data.get('otp', '')
        if not isinstance(password, str) or len(password) > 256 or not isinstance(otp, str) or len(otp) > 6:
            return jsonify(error='Invalid input.'), 400
        with file_lock(str(path) + '.lock'):
            account = load_accounts(path).get(actor)
            if not account or not authenticated():
                return jsonify(error='Authentication required'), 403
            error, _ = reauthenticate(actor, account, password, otp)
        if error is not None:
            return error
        try:
            subprocess.run(HA_RETIRE_COMMAND, check=True, capture_output=True, timeout=90)
        except (OSError, subprocess.SubprocessError):
            audit('ha_retire_failed', level='warning')
            return jsonify(error='HA retirement failed. Check the retirement service log on the host.'), 409
        audit('ha_node_retired')
        return jsonify(success=True)

    @settings.post('/api/settings/accounts')
    @requires_auth
    def change():
        path, state = paths()
        actor = session.get('user')
        if not path or not actor or not secure_access() or not os.access(path.parent, os.W_OK):
            return jsonify(error='Browser-based account management is unavailable here.'), 403
        csrf = session.get('settings_csrf', '')
        supplied = request.headers.get('X-CSRF-Token', '')
        if not csrf or not supplied.isascii() or not secrets.compare_digest(csrf, supplied):
            return jsonify(error='Session expired. Reload the page.'), 403
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='Invalid input.'), 400
        action = data.get('action')
        if action not in ('create', 'remove', 'password', 'totp-start', 'totp-confirm', 'totp-disable'):
            return jsonify(error='Invalid input.'), 400
        pending = state.with_name('enroll-' + hashlib.sha256(actor.encode()).hexdigest() + '.json')
        target = data.get('username') if action in ('create', 'remove') else actor
        if not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', target):
            return jsonify(error='Enter a valid username.'), 400
        new_password = data.get('newPassword')
        if action in ('create', 'password') and (
            not isinstance(new_password, str) or not 12 <= len(new_password) <= 256
        ):
            return jsonify(error='Use a password of 12–256 characters.'), 400
        try:
            with file_lock(str(path) + '.lock'):
                accounts = load_accounts(path)
                current = accounts.get(actor)
                if not current or current['revision'] != session.get('account_revision'):
                    return jsonify(error='Session expired. Reload the page.'), 401
                step = None
                if action != 'totp-confirm':
                    error, step = reauthenticate(actor, current, data.get('password'), data.get('otp'))
                    if error:
                        return error
                if action == 'totp-start':
                    if 'totp_secret' in current:
                        return jsonify(error='2FA is already enabled.'), 409
                    secret = pyotp.random_base32()
                    token = secrets.token_urlsafe(24)
                    atomic_write(pending, json.dumps(dict(secret=secret, token=token, revision=current['revision'],
                                                          expires=time.time() + 300)))
                    session['settings_enrollment'] = token
                    uri = pyotp.TOTP(secret).provisioning_uri(name=actor, issuer_name='ProxyPermit')
                    image = io.BytesIO()
                    segno.make_qr(uri).save(image, kind='png', scale=5)
                    return jsonify(secret=secret, qr='data:image/png;base64,' + base64.b64encode(image.getvalue()).decode('ascii'))
                if action == 'totp-confirm':
                    if not pending.is_file() or not session.get('settings_enrollment'):
                        return jsonify(error='Start 2FA setup again.'), 409
                    setup = json.loads(pending.read_text(encoding='utf-8'))
                    if (setup['token'] != session['settings_enrollment'] or setup['revision'] != current['revision']
                            or setup['expires'] < time.time() or setup.get('attempts', 0) >= 5 or 'totp_secret' in current):
                        return jsonify(error='Start 2FA setup again.'), 409
                    step = matching_totp_step(setup['secret'], data.get('code'))
                    if step is None:
                        setup['attempts'] = setup.get('attempts', 0) + 1
                        atomic_write(pending, json.dumps(setup))
                        return jsonify(error='The authenticator code is incorrect.'), 400
                    current['totp_secret'] = setup['secret']
                elif action == 'totp-disable':
                    if 'totp_secret' not in current:
                        return jsonify(error='2FA is already disabled.'), 409
                    del current['totp_secret']
                elif action == 'create':
                    if target in accounts:
                        return jsonify(error='This account already exists.'), 409
                    accounts[target] = {'password_hash': generate_password_hash(new_password),
                                        'revision': secrets.token_hex(16)}
                elif action == 'remove':
                    if target == actor or target not in accounts:
                        return jsonify(error='This account cannot be removed.'), 409
                    del accounts[target]
                else:
                    current['password_hash'] = generate_password_hash(new_password)
                if target == actor:
                    current['revision'] = secrets.token_hex(16)
                    if step is not None and 'totp_secret' in current:
                        consume_totp(state.with_name('totp-steps.json'), actor, current['revision'], step)
                atomic_write(path, json.dumps(accounts, indent=2))
                if target == actor:
                    session['account_revision'] = current['revision']
                    session.pop('settings_enrollment', None)
                    try:
                        pending.unlink(missing_ok=True)
                    except OSError:
                        app.logger.warning('Could not remove expired 2FA enrollment')
        except (OSError, ValueError):
            app.logger.exception('Account update failed')
            return jsonify(error='The account change failed.'), 503
        audit('account_' + action.replace('-', '_'), target_account=target)
        return jsonify(success=True)

    app.register_blueprint(settings)
