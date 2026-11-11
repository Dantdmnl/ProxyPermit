"""Lossless Tinyproxy 1.11 configuration editing, independent of Flask.

The ordered native file is the interchange format. Unknown directives and
comments survive migration; unknown directives block activation until reviewed.
Reference: https://tinyproxy.github.io/ (tinyproxy.conf(5), release 1.11.1).
"""
import hashlib
import ipaddress
import json
import os
import re
import shlex
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .files import file_lock


# type, section, repeatable. Complex directives retain their native arguments.
DIRECTIVES = {}
for section, kind, repeatable, names in [
    ('Network', 'port', False, 'Port'),
    ('Network', 'ip', False, 'Listen'),
    ('Network', 'ip', True, 'Bind'),
    ('Network', 'boolean', False, 'BindSame'),
    ('Capacity', 'positive', False, 'Timeout MaxClients'),
    ('Access', 'acl', True, 'Allow Deny'),
    ('Access', 'credentials', True, 'BasicAuth'),
    ('Access', 'connect_port', True, 'ConnectPort'),
    ('Filtering', 'path', False, 'Filter'),
    ('Filtering', 'boolean', False, 'FilterURLs FilterCaseSensitive FilterDefaultDeny FilterExtended'),
    ('Filtering', 'filter_type', False, 'FilterType'),
    ('Upstream', 'upstream', True, 'Upstream'),
    ('Headers', 'pair', True, 'AddHeader'),
    ('Headers', 'text', True, 'Anonymous'),
    ('Headers', 'text', False, 'ViaProxyName'),
    ('Headers', 'boolean', False, 'XTinyproxy DisableViaHeader'),
    ('Reverse proxy', 'reverse_path', True, 'ReversePath'),
    ('Reverse proxy', 'boolean', False, 'ReverseOnly ReverseMagic'),
    ('Reverse proxy', 'url', False, 'ReverseBaseURL'),
    ('Logging', 'path', False, 'LogFile'),
    ('Logging', 'boolean', False, 'Syslog'),
    ('Logging', 'log_level', False, 'LogLevel'),
    ('System', 'text', False, 'User Group StatHost'),
    ('System', 'path', False, 'PidFile DefaultErrorFile StatFile'),
    ('System', 'error_file', True, 'ErrorFile'),
]:
    for name in names.split():
        DIRECTIVES[name.lower()] = dict(name=name, kind=kind, section=section, repeatable=repeatable)


class ConfigError(ValueError):
    pass


class RevisionConflict(ConfigError):
    pass


def revision(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def parse(content):
    """Parse without rewriting: quotes, comments, ordering and blank lines survive."""
    if not isinstance(content, str) or len(content.encode('utf-8')) > 1024 * 1024:
        raise ConfigError('Configuration must be text, at most 1 MiB.')
    if any(ord(c) < 32 and c not in '\n\r\t' for c in content):
        raise ConfigError('Configuration contains invalid control characters.')
    rows = []
    for number, raw in enumerate(content.splitlines(), 1):
        lexer = shlex.shlex(raw, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = '#'
        try:
            tokens = list(lexer)
        except ValueError:
            raise ConfigError(f'Line {number}: unclosed quotes.') from None
        if tokens:
            spec = DIRECTIVES.get(tokens[0].lower())
            rows.append(dict(line=number, name=tokens[0], args=tokens[1:], spec=spec))
    return rows


def validate(content):
    errors, warnings = [], []
    try:
        rows = parse(content)
    except ConfigError as exc:
        return dict(valid=False, errors=[str(exc)], warnings=[])
    grouped = {}
    for row in rows:
        key, args, spec = row['name'].lower(), row['args'], row['spec']
        grouped.setdefault(key, []).append(args)
        prefix = f"Line {row['line']} ({row['name']}): "
        if spec is None:
            errors.append(prefix + 'unknown directive for Tinyproxy 1.11.')
            continue
        kind = spec['kind']
        expected = 2 if kind in ('credentials', 'pair', 'reverse_path', 'error_file') else 1
        if kind != 'upstream' and len(args) != expected:
            errors.append(prefix + f'expected {expected} argument(s).')
            continue
        if not args or any(not arg for arg in args):
            errors.append(prefix + 'empty values are not allowed.')
            continue
        value = args[0]
        try:
            if kind == 'boolean' and value.lower() not in ('yes', 'no', 'on', 'off'):
                raise ValueError('use Yes/No or On/Off.')
            if kind in ('port', 'connect_port', 'positive'):
                minimum = 0 if kind == 'connect_port' else 1
                maximum = 65535 if kind != 'positive' else 2147483647
                if not re.fullmatch(r'[0-9]+', value) or not minimum <= int(value) <= maximum:
                    raise ValueError(f'expected a number between {minimum} and {maximum}.')
            if kind == 'ip':
                ipaddress.ip_address(value)
            if kind == 'acl':
                if ':' in value or '/' in value or re.fullmatch(r'[0-9.]+', value):
                    ipaddress.ip_network(value, strict=False)
                elif not re.fullmatch(r'\.?[A-Za-z0-9][A-Za-z0-9.-]*', value):
                    raise ValueError('invalid network, IP address, or domain.')
            if kind == 'path' and not (value.startswith('/') or (os.name == 'nt' and re.match(r'^[A-Za-z]:[/\\]', value))):
                raise ValueError('use an absolute Linux path.')
            if kind == 'filter_type' and value.lower() not in ('bre', 'ere', 'fnmatch'):
                raise ValueError('use bre, ere or fnmatch.')
            if kind == 'log_level' and value.lower() not in ('critical', 'error', 'warning', 'notice', 'connect', 'info'):
                raise ValueError('unknown log level.')
            if kind == 'upstream':
                if value.lower() == 'none':
                    if len(args) != 2:
                        raise ValueError('use Upstream none "domain".')
                elif value.lower() not in ('http', 'socks4', 'socks5') or len(args) not in (2, 3):
                    raise ValueError('use http/socks4/socks5 host:port ["domain"].')
                else:
                    target = args[1].rsplit('@', 1)[-1]
                    host, separator, port = target.rpartition(':')
                    if not separator or not host or not port.isdigit() or not 0 <= int(port) <= 65535:
                        raise ValueError('invalid upstream address or port.')
            if kind in ('url', 'reverse_path'):
                from urllib.parse import urlsplit
                url = args[-1]
                parsed = urlsplit(url)
                if parsed.scheme not in ('http', 'https') or not parsed.hostname:
                    raise ValueError('expected an HTTP or HTTPS URL.')
                if kind == 'reverse_path' and not value.startswith('/'):
                    raise ValueError('the reverse proxy path must begin with /.')
            if kind == 'error_file' and (not value.isdigit() or not 400 <= int(value) <= 599 or not args[1].startswith('/')):
                raise ValueError('expected an HTTP error code and an absolute file path.')
        except ValueError as exc:
            # Never include argument values: BasicAuth/Upstream may hold secrets.
            message = str(exc) if kind not in ('ip', 'acl') else 'invalid IP address, network or domain.'
            errors.append(prefix + message)
    for key, occurrences in grouped.items():
        spec = DIRECTIVES.get(key)
        if spec and not spec['repeatable'] and len(occurrences) > 1:
            errors.append(f"{spec['name']}: may occur only once.")
    enabled = lambda key: bool(grouped.get(key) and grouped[key][-1] and grouped[key][-1][0].lower() in ('yes', 'on'))
    if 'port' not in grouped:
        errors.append('Port is required.')
    if enabled('syslog') and 'logfile' in grouped:
        errors.append('Syslog and LogFile cannot be used together.')
    if 'allow' not in grouped and 'deny' not in grouped:
        errors.append('Add an Allow/Deny rule to prevent unintended open access.')
    if 'filter' in grouped and 'filtertype' not in grouped:
        warnings.append('Without FilterType, Tinyproxy uses BRE; the domain editor expects ERE.')
    if enabled('filterurls'):
        warnings.append('URL filtering works for HTTP; HTTPS paths are not visible inside a CONNECT tunnel.')
    if 'reversepath' in grouped and not enabled('reverseonly'):
        warnings.append('Reverse proxy is enabled while forward proxy remains available.')
    if 'connectport' not in grouped:
        warnings.append('Without ConnectPort, Tinyproxy allows CONNECT to all ports.')
    if 'basicauth' in grouped:
        warnings.append('Tinyproxy stores proxy passwords in readable form; restrict access to configuration and backups.')
    if 'filter' not in grouped:
        warnings.append('Domain filtering is disabled; Allow/Deny controls network access.')
    return dict(valid=not errors, errors=errors, warnings=warnings)


def summary(content):
    rows = parse(content)
    values = {r['name'].lower(): r['args'] for r in rows if r['args']}
    active = values.get('filterdefaultdeny', ['No'])[0].lower() in ('yes', 'on')
    return dict(filterMode=('allowlist' if active else 'blocklist') if 'filter' in values else 'disabled',
                filterType=values.get('filtertype', ['bre'])[0],
                filterURLs=values.get('filterurls', ['No'])[0].lower() in ('yes', 'on'),
                filterPath=values.get('filter', [''])[0])


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        if path.exists() and os.name != 'nt' and os.geteuid() == 0:  # pylint: disable=no-member
            info = path.stat()
            os.chown(name, info.st_uid, info.st_gid)  # pylint: disable=no-member
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class TinyproxyConfigStore:
    def __init__(self, path, validator=validate, mode=0o600):
        self.path = Path(path)
        self.backup_dir = self.path.parent / '.proxypermit-config-backups'
        self.validate = validator
        self.mode = mode
        self.pending_path = self.backup_dir / ('pending-' + hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()[:16] + '.json')
        self.draft_path = self.pending_path.with_name(self.pending_path.name.replace('pending-', 'draft-', 1))

    def finish_activation(self):
        if self.pending_path.exists():
            self.pending_path.unlink()

    def restore_pending(self):
        """Restore the last activated baseline, including saves before activation."""
        if self.pending_path.exists():
            baseline = json.loads(self.pending_path.read_text(encoding='utf-8'))
            if baseline.get('existed', True):
                atomic_write(self.path, baseline['content'], self.mode)
            else:
                self.path.unlink(missing_ok=True)
            self.finish_activation()

    def _live_content(self):
        return self.path.read_text(encoding='utf-8-sig') if self.path.exists() else ''

    def _draft(self):
        try:
            return json.loads(self.draft_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None

    def read(self):
        draft = self._draft()
        content = draft['content'] if draft else self._live_content()
        return dict(content=content, revision=revision(content), draft=draft is not None,
                    staged=draft is not None or self.pending_path.exists(),
                    **self.validate(content))

    def _check_baseline(self, draft, live):
        if draft and draft['base'] != revision(live):
            raise RevisionConflict('The proxy file changed outside ProxyPermit. Discard the saved draft before continuing.')

    def _publish(self, content, live):
        if not self.pending_path.exists():
            atomic_write(self.pending_path, json.dumps(dict(content=live, existed=self.path.exists())))
        atomic_write(self.path, content, self.mode)

    def publish_draft(self):
        draft = self._draft()
        if draft:
            live = self._live_content()
            self._check_baseline(draft, live)
            self.publish_content(draft['content'], live)

    def publish_content(self, content, live):
        """Stage an active replacement under the caller's file lock."""
        checks = self.validate(content)
        if not checks['valid']:
            raise ConfigError('\n'.join(checks['errors']))
        self._publish(content, live)

    def discard_draft(self, expected_revision):
        with file_lock(str(self.path) + '.lock'):
            draft = self._draft()
            if draft is None or revision(draft['content']) != expected_revision:
                raise RevisionConflict('Configuration has changed. Reload and compare your changes.')
            self.draft_path.unlink(missing_ok=True)

    def save(self, content, expected_revision, activate=None):
        checks = self.validate(content)
        if not checks['valid']:
            raise ConfigError('\n'.join(checks['errors']))
        with file_lock(str(self.path) + '.lock'):
            before = self.read()
            if not expected_revision or before['revision'] != expected_revision:
                raise RevisionConflict('Configuration has changed. Reload and compare your changes.')
            self.backup_dir.mkdir(exist_ok=True, mode=0o700)
            live = self._live_content()
            self._check_baseline(self._draft(), live)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = self.backup_dir / (stamp + '.conf')
            atomic_write(backup, live)
            if activate is None:
                atomic_write(self.draft_path, json.dumps(dict(content=content, base=revision(live))))
            else:
                self._publish(content, live)
                try:
                    ok, _ = activate()
                except Exception:
                    ok = False
                if not ok:
                    self.restore_pending()
                    try:
                        recovered, _ = activate()
                    except Exception:
                        recovered = False
                    raise ConfigError('Activation failed. The previous configuration was restored. ' +
                                      ('The service is running again.' if recovered else 'Check the service on the server.'))
                self.finish_activation()
                self.draft_path.unlink(missing_ok=True)
            return dict(revision=revision(content), backup=backup.name, activated=activate is not None,
                        warnings=checks['warnings'])
