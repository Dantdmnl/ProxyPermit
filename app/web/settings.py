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

from ..core.accounts import ROLES, account_role, admin_count, consume_totp, load_accounts, matching_totp_step
from ..core.files import file_lock
from ..core.access_safety import read_profile
from ..core.ha import HA_SETUP_ENV, HA_SETUP_REQUEST, HA_SETUP_RESULT, ha_firewall_rules, ha_proxy_config, ha_setup_firewall_rules, ha_topology, setup_revision
from ..core.tinyproxy import ConfigError, atomic_write
from .proxy import activation_supported, ha_enabled, has_pending_changes, managed_ha
from . import ha_replication
from .ha_network import discover_network

HA_RETIRE_COMMAND = ('/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'start', 'proxypermit-ha-retire.service')
HA_SETUP_COMMAND = ('/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'start', '--no-block', 'proxypermit-ha-setup.service')


def ha_setup_available():
    return (not ha_enabled() and activation_supported()
            and Path('/etc/systemd/system/proxypermit-ha-setup.service').is_file())


def setup_topology(data):
    code = data.get('joinCode', '')
    if code:
        if not isinstance(code, str) or len(code) > 2048:
            raise ValueError('Invalid HA pairing code.')
        invite = json.loads(code)
        topology = invite.get('topology') if isinstance(invite, dict) else None
        if not isinstance(topology, dict):
            raise ValueError('Create a new pairing code on the primary node.')
        topology = ha_topology('b', data.get('interface'), topology.get('nodeA'), topology.get('nodeB'),
                               topology.get('vip'), topology.get('clients'), topology.get('vrid'))
        cluster = '/'.join((topology['nodeA'], topology['nodeB'], topology['vip'].split('/')[0]))
        ha_replication.parse_pairing_code(code, cluster)
        return topology
    if data.get('node') != 'a':
        raise ValueError('Create a primary cluster or join using its pairing code.')
    keys = ('interface', 'nodeA', 'nodeB', 'vip', 'clients', 'vrid')
    if any(not isinstance(data.get(key), str) or len(data[key]) > 64 for key in keys):
        raise ValueError('Invalid input.')
    return ha_topology('a', *(data[key].strip() for key in keys[:-1]), int(data['vrid']))


def setup_fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


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
        can_admin = app.extensions['has_permission']('admin')
        if not can_admin:
            accounts = {actor: accounts[actor]} if actor in accounts else {}
        return render_template('settings.html', access_safety=read_profile(), actor=actor, accounts=accounts, account_roles={name: account_role(account) for name, account in accounts.items()},
                               editable=bool(path and secure_access() and os.access(path.parent, os.W_OK)),
                               ha_enabled=ha_enabled(), managed_ha=managed_ha(),
                               ha_policy_role=os.getenv('PROXYPERMIT_HA_NODE') if managed_ha() and secure_access() and activation_supported() else None,
                               ha_policy_csrf=session.setdefault('proxy_csrf', secrets.token_urlsafe(32)) if managed_ha() else None,
                               ha_retire_available=bool(can_admin and path and secure_access() and ha_retirement_available()),
                               ha_setup_available=bool(can_admin and path and secure_access() and ha_setup_available()),
                               csrf=session.setdefault('settings_csrf', secrets.token_urlsafe(32)))

    @settings.get('/api/settings/ha/network')
    @requires_auth
    def ha_network():
        if not session.get('user') or not secure_access():
            return jsonify(error='Use HTTPS or a local SSH tunnel for HA policy transfer.'), 403
        try:
            return jsonify(networks=discover_network())
        except (OSError, ValueError, subprocess.SubprocessError):
            return jsonify(error='Network detection is unavailable. Enter the network settings manually.'), 503

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
        if ha_setup_available() and secure_access():
            try:
                if not isinstance(data, dict):
                    raise ValueError('Invalid input.')
                topology = setup_topology(data)
                config = Path(os.getenv('TINYPROXY_CONFIG_PATH', '/etc/tinyproxy/tinyproxy.conf'))
                filters = Path(os.getenv('WHITELIST_PATH', '/etc/tinyproxy/whitelist.txt'))
                if has_pending_changes(filters):
                    raise ValueError('Publish or discard saved drafts before HA setup.')
                ha_proxy_config(config.read_text(encoding='utf-8-sig'), topology)
                revision = setup_revision(config, filters)
                session['ha_setup_plan'] = dict(revision=revision, fingerprint=setup_fingerprint(data), created=int(time.time()))
                return jsonify(topology=topology, firewallRules=ha_setup_firewall_rules(topology),
                               canApply=True, revision=revision)
            except (ValueError, ConfigError, TypeError, KeyError, OSError):
                return jsonify(error='Cannot prepare HA setup. Check the pairing code, network settings, and saved drafts.'), 400
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

    @settings.post('/api/settings/ha/setup')
    @requires_auth
    def apply_ha_setup():
        path, _ = paths()
        if not path or not secure_access() or not ha_setup_available() or not session.get('user'):
            return jsonify(error='HA setup is unavailable on this node.'), 403
        csrf, supplied = session.get('settings_csrf', ''), request.headers.get('X-CSRF-Token', '')
        if not csrf or not supplied.isascii() or not secrets.compare_digest(csrf, supplied):
            return jsonify(error='Session expired. Reload the page.'), 403
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get('confirmed') is not True or not isinstance(data.get('plan'), dict):
            return jsonify(error='Review and confirm the HA setup plan first.'), 400
        approved = session.get('ha_setup_plan', {})
        if (data.get('revision') != approved.get('revision') or setup_fingerprint(data['plan']) != approved.get('fingerprint')
                or time.time() - approved.get('created', 0) > 300):
            return jsonify(error='The HA setup plan expired or changed. Review it again.'), 409
        password, otp = data.get('password'), data.get('otp', '')
        if not isinstance(password, str) or len(password) > 256 or not isinstance(otp, str) or len(otp) > 6:
            return jsonify(error='Invalid input.'), 400
        with file_lock(str(path) + '.lock'):
            account = load_accounts(path).get(session['user'])
            if not account or not authenticated():
                return jsonify(error='Authentication required'), 403
            error, _ = reauthenticate(session['user'], account, password, otp)
        if error is not None:
            return error
        try:
            topology = setup_topology(data['plan'])
            with file_lock(str(HA_SETUP_REQUEST) + '.lock'):
                if HA_SETUP_REQUEST.exists():
                    return jsonify(error='HA setup is already running.'), 409
                if HA_SETUP_RESULT.is_file():
                    previous = json.loads(HA_SETUP_RESULT.read_text())
                    if previous.get('phase') in ('queued', 'running', 'activating') and time.time() - HA_SETUP_RESULT.stat().st_mtime < 330:
                        return jsonify(error='HA setup is already running.'), 409
                identifier = secrets.token_hex(16)
                atomic_write(HA_SETUP_REQUEST, json.dumps(dict(id=identifier, topology=topology,
                    revision=approved['revision'], created=int(time.time()), joinCode=data['plan'].get('joinCode', ''))))
                # The helper writes its own result. An application-owned initial record is only a progress indicator.
                atomic_write(HA_SETUP_RESULT, json.dumps(dict(id=identifier, phase='queued')), 0o644)
                try:
                    subprocess.run(HA_SETUP_COMMAND, check=True, capture_output=True, timeout=10)
                except subprocess.TimeoutExpired:
                    # The service may already have been queued. Keep its request and
                    # concurrency guard intact; the status endpoint reports the outcome.
                    pass
            session.pop('ha_setup_plan', None)
            audit('ha_setup_requested', setup_id=identifier, node=topology['node'])
            return jsonify(success=True, id=identifier), 202
        except (ValueError, OSError, TimeoutError, subprocess.SubprocessError):
            return jsonify(error='HA setup could not be started. Check the host service log.'), 409

    @settings.get('/api/settings/ha/setup-status')
    @requires_auth
    def ha_setup_status():
        try:
            state = json.loads(HA_SETUP_RESULT.read_text()) if HA_SETUP_RESULT.is_file() else dict(phase='idle')
            if (state.get('phase') in ('queued', 'running', 'activating')
                    and time.time() - HA_SETUP_RESULT.stat().st_mtime > 420):
                state = {**state, 'phase': 'failed', 'error': 'HA setup failed. Check the host service log.'}
            return jsonify(state)
        except (OSError, ValueError):
            return jsonify(error='Cannot read HA setup status.'), 500

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
        if action not in ('create', 'remove', 'role', 'password', 'totp-start', 'totp-confirm', 'totp-disable'):
            return jsonify(error='Invalid input.'), 400
        pending = state.with_name('enroll-' + hashlib.sha256(actor.encode()).hexdigest() + '.json')
        target = data.get('username') if action in ('create', 'remove', 'role') else actor
        if not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', target):
            return jsonify(error='Enter a valid username.'), 400
        requested_role = data.get('role', 'monitor')
        if action in ('create', 'role') and (not isinstance(requested_role, str) or requested_role not in ROLES):
            return jsonify(error='Choose a valid role.'), 400
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
                if action in ('create', 'remove', 'role') and account_role(current) != 'admin':
                    audit('permission_denied', required='admin', target_account=target)
                    return jsonify(error='Your role does not permit this action.'), 403
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
                                        'revision': secrets.token_hex(16), 'role': requested_role}
                elif action in ('remove', 'role'):
                    if target == actor or target not in accounts:
                        return jsonify(error='This account cannot be removed.'), 409
                    if account_role(accounts[target]) == 'admin' and admin_count(accounts) <= 1 and (action == 'remove' or requested_role != 'admin'):
                        return jsonify(error='Keep at least one system administrator.'), 409
                    if action == 'remove':
                        del accounts[target]
                    else:
                        accounts[target]['role'] = requested_role
                        accounts[target]['revision'] = secrets.token_hex(16)
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
        audit('account_' + action.replace('-', '_'), target_account=target, target_role=requested_role if action in ('create', 'role') else None)
        return jsonify(success=True)

    app.register_blueprint(settings)
