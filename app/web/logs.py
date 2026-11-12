"""Bounded, date-aware queries over retained journals and audit archives."""
import hashlib
import gzip
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ..core.tinyproxy import parse
from ..core.proxy_health import HEALTH_PATH, probe_settings
from ..core.audit import audit_lock, audit_paths, bounded_setting, open_audit, record_time

SERVICES = ('manager', 'tinyproxy', 'proxy_traffic', 'keepalived', 'audit')
UNITS = ('proxypermit.service', 'proxypermit-ha-policy.service', 'proxypermit-ha-tls.service',
         'proxypermit-ha-setup.service', 'proxypermit-ha-retire.service', 'tinyproxy.service', 'keepalived.service')
LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR')


def identify_health_checks(records):
    """Recognize complete, local probe connections before IP redaction.

    Never suppress warnings/errors, remote requests or an unmatched descriptor.
    Raw metadata stays private and is removed from the returned API objects.
    """
    try:
        _, host = probe_settings(os.getenv('TINYPROXY_CONFIG_PATH', '/etc/tinyproxy/tinyproxy.conf'))
    except (OSError, ValueError):
        host = 'tinyproxy.stats'
    connections, recent = {}, {}
    for _, item in sorted(enumerate(records), key=lambda row: (row[1]['_time'], -row[0])):
        if item['service'] not in ('tinyproxy', 'proxy_traffic'):
            continue
        raw = item.get('_raw', '')
        process = (item.get('_origin'), item.get('_pid'))
        if not isinstance(process[1], (str, int)) or not process[1]:
            continue
        connect = re.fullmatch(r'Connect \(file descriptor (\d+)\): (\S+)', raw)
        if connect:
            try:
                local = ipaddress.ip_address(connect[2]).is_loopback
            except ValueError:
                local = False
            connections[(*process, connect[1])] = (local, item)
            recent.pop(process, None)
            continue
        request_line = re.fullmatch(r'Request \(file descriptor (\d+)\): GET (http://\S+) HTTP/1\.[01]', raw)
        if request_line:
            previous = connections.pop((*process, request_line[1]), None)
            try:
                target = urlsplit(request_line[2])
                target_host = target.hostname
            except ValueError:
                recent.pop(process, None)
                continue
            if (previous and previous[0] and 0 <= item['_time'] - previous[1]['_time'] <= 1
                    and target_host == host.lower() and target.path == HEALTH_PATH and not target.query
                    and item['level'] in ('DEBUG', 'INFO') and previous[1]['level'] in ('DEBUG', 'INFO')):
                item['healthCheck'] = previous[1]['healthCheck'] = True
                recent[process] = item['_time']
            else:
                recent.pop(process, None)
        elif raw in ('Request for the stathost.', f'Proxying refused on filtered domain "{host}"') and item['level'] in ('DEBUG', 'INFO'):
            if 0 <= item['_time'] - recent.get(process, -10) <= 1:
                item['healthCheck'] = True
            recent.pop(process, None)
        else:
            recent.pop(process, None)


def policy():
    return dict(auditDays=bounded_setting('AUDIT_LOG_RETENTION_DAYS', 30),
                managementDays=bounded_setting('LOG_RETENTION_DAYS', 30),
                trafficDays=bounded_setting('PROXY_TRAFFIC_RETENTION_DAYS', 7),
                auditMaxBytes=bounded_setting('AUDIT_LOG_MAX_BYTES', 2097152, 65536, 104857600),
                auditBackups=bounded_setting('AUDIT_LOG_BACKUP_COUNT', 5, 1, 50),
                managed=os.getenv('PROXYPERMIT_MANAGED_LOGGING') == 'true')


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def scrub_message(message):
    """Defense in depth for historic records and downloads; not storage erasure."""
    def clean_url(match):
        try:
            value = urlsplit(match.group())
            host = value.hostname or ''
            if ':' in host:
                host = '[' + host + ']'
            if value.port:
                host += ':' + str(value.port)
            return urlunsplit((value.scheme, host, value.path, '', ''))
        except ValueError:
            return '[redacted URL]'
    text = re.sub(r'https?://[^\s\"<>]+', clean_url, str(message))
    text = re.sub(r'(?i)\b(authorization|cookie|set-cookie)\s*[:=][^\r\n]*', r'\1=[redacted]', text)
    text = re.sub(r'(?i)\b(password|passwd|secret|token|otp|totp|code)\s*[:=]\s*[^\s,;]+', r'\1=[redacted]', text)
    mode = os.getenv('LOG_IP_MODE', 'masked')
    if mode != 'full':
        def mask(match):
            try:
                address = ipaddress.ip_address(match.group().split('/')[0])
                if mode == 'hash':
                    import hmac
                    return hmac.new(os.getenv('FLASK_SECRET_KEY', '').encode(), str(address).encode(), hashlib.sha256).hexdigest()[:12]
                network = ipaddress.ip_network(f'{address}/{24 if address.version == 4 else 64}', strict=False)
                return str(network)
            except ValueError:
                return match.group()
        text = re.sub(r'(?<![\w:])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?:/[0-9]{1,2})?(?![\w.])|(?<![\w:])(?:[a-fA-F0-9]{0,4}:){2,7}[a-fA-F0-9]{0,4}(?:/[0-9]{1,3})?(?![\w:])', mask, text)
    return text[:16384]


def reverse_lines(handle, budget):
    position = handle.seek(0, os.SEEK_END)
    pending = b''
    while position > 0 and budget[0] > 0:
        size = min(65536, position, budget[0])
        position -= size
        handle.seek(position)
        pending = handle.read(size) + pending
        budget[0] -= size
        lines = pending.split(b'\n')
        pending = lines.pop(0)
        for line in reversed(lines):
            if line:
                yield line
        if len(pending) > 65536:
            pending = b''
    if pending and position == 0:
        yield pending


def audit_records(path, since, until, maximum):
    records, scanned, malformed = [], 0, 0
    truncated = False
    budget = [64 * 1024 * 1024]
    with audit_lock(path):
        for source in audit_paths(path):
            try:
                with open_audit(source) as handle:
                    for line in reverse_lines(handle, budget):
                        try:
                            item = json.loads(line)
                            timestamp = record_time(item.get('timestamp')) if isinstance(item, dict) else None
                            if timestamp is None:
                                raise ValueError('Missing timestamp')
                        except (ValueError, UnicodeDecodeError, RecursionError):
                            malformed += 1
                            continue
                        if since <= timestamp <= until:
                            scanned += 1
                            if scanned > maximum:
                                truncated = True
                                break
                            message = scrub_message(item.get('message') or item.get('event', ''))
                            entry = dict(timestamp=iso(timestamp), level=item.get('level', 'INFO'), service='audit',
                                         message=message, _time=timestamp,
                                         _id=item['id'] if isinstance(item.get('id'), str) else hashlib.sha256(line).hexdigest())
                            if isinstance(item.get('event'), str) and isinstance(item.get('username'), str):
                                entry['audit'] = dict(event=item['event'][:100], username=scrub_message(item['username'])[:200])
                            records.append(entry)
                    if budget[0] <= 0:
                        truncated = True
            except FileNotFoundError:
                continue
            if truncated:
                break
    return records, truncated, malformed


def journal_lines(command):
    """Bound output bytes and wall time independently of the record limit."""
    output, size, truncated = [], 0, False
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        timer = threading.Timer(3, process.kill)
        timer.start()
        try:
            while True:
                line = process.stdout.readline(65537)
                if not line:
                    break
                size += len(line) + 1
                if len(line) > 65536 or size > 16 * 1024 * 1024:
                    truncated = True
                    process.kill()
                    break
                output.append(line.decode('utf-8', errors='replace'))
            process.wait(timeout=1)
            if process.returncode and not truncated:
                raise OSError('Journal query failed or timed out.')
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=1)
    return output, truncated


def journal_records(namespace, since, until, maximum):
    command = ['journalctl', '--no-pager', '--quiet', '-o', 'json', '--reverse', '-n', str(maximum + 1),
               '--since=@' + str(since), '--until=@' + str(until)]
    if namespace:
        command.append('--namespace=' + namespace)
    for unit in UNITS:
        command.extend(['-u', unit])
    lines, incomplete = journal_lines(command)
    records = []
    for line in lines[:maximum]:
        try:
            item = json.loads(line)
            timestamp = int(item['__REALTIME_TIMESTAMP']) / 1000000
            raw = item.get('MESSAGE', '')
            if not isinstance(raw, str):
                incomplete = True
                continue
            if re.search(r'\b(?:AUTH_AUDIT|AUDIT)\s+[^\n]*\bevent=', raw):
                continue
            unit = item.get('_SYSTEMD_UNIT') or item.get('UNIT', '')
            identifier = item.get('SYSLOG_IDENTIFIER', '')
            service = 'keepalived' if 'keepalived' in unit or identifier == 'Keepalived' else 'manager'
            if 'tinyproxy' in unit or identifier == 'tinyproxy':
                service = 'proxy_traffic' if re.search(r'(?i)request \(|connect \(|filtered domain|unauthorized connection|established connection|opensock:', raw) else 'tinyproxy'
            priority = int(item.get('PRIORITY', 6))
            level = 'ERROR' if priority <= 3 else 'WARNING' if priority == 4 else 'DEBUG' if priority == 7 else 'INFO'
            records.append(dict(timestamp=iso(timestamp), level=level, service=service, message=scrub_message(raw),
                                _raw=raw, _pid=item.get('_PID'), _origin=namespace or 'host',
                                _time=timestamp, _id=item['__CURSOR'] if isinstance(item.get('__CURSOR'), str) else hashlib.sha256(line.encode()).hexdigest()))
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError):
            incomplete = True
            continue
    return records, incomplete or len(lines) > maximum


def traffic_message(message):
    return bool(re.search(r'(?i)request \(|connect \(|filtered domain|unauthorized connection|established connection|opensock:', message))


def proxy_file_records(since, until, maximum):
    """Support the standard Tinyproxy logfile without exposing arbitrary paths."""
    configuration = Path(os.getenv('TINYPROXY_CONFIG_PATH', '/etc/tinyproxy/tinyproxy.conf'))
    if not configuration.exists():
        return [], False, False
    with open_audit(configuration) as handle:
        if os.fstat(handle.fileno()).st_size > 2 * 1024 * 1024:
            raise ValueError('Tinyproxy configuration is too large.')
        rows = parse(handle.read().decode('utf-8-sig'))
    logfile = next((row['args'][0] for row in rows if row['name'].lower() == 'logfile'), None)
    syslog = next((row['args'][0].lower() for row in rows if row['name'].lower() == 'syslog'), 'off')
    if not logfile or syslog == 'on':
        return [], False, False
    path = Path(logfile)
    if path != Path('/var/log/tinyproxy/tinyproxy.log'):
        raise ValueError('A custom Tinyproxy logfile requires an operator-managed reader and retention policy.')
    paths = [path]
    for number in range(1, 32):
        paths.extend((Path(str(path) + '.' + str(number)), Path(str(path) + '.' + str(number) + '.gz')))
    records, scanned, size, incomplete = [], 0, 0, False
    for source in paths:
        try:
            with open_audit(source) as handle:
                year = datetime.fromtimestamp(os.fstat(handle.fileno()).st_mtime).year
                stream = gzip.GzipFile(fileobj=handle) if source.suffix == '.gz' else handle
                try:
                    if stream is handle:
                        lines = reverse_lines(handle, [32 * 1024 * 1024 - size])
                    else:
                        data = stream.read(32 * 1024 * 1024 - size + 1)
                        if len(data) + size > 32 * 1024 * 1024:
                            return records, True, True
                        lines = reversed(data.splitlines())
                    for line in lines:
                        size += len(line) + 1
                        if len(line) > 65536 or size > 32 * 1024 * 1024:
                            return records, True, True
                        text = line.decode('utf-8', errors='replace').strip()
                        match = re.match(r'^(DEBUG|INFO|NOTICE|WARNING|ERROR|CRITICAL|CONNECT)\s+([A-Za-z]{3}\s+\d+\s+[\d:.]+)\s+\[\d+\]:\s*(.*)', text)
                        if not match:
                            incomplete = True
                            continue
                        try:
                            date = datetime.strptime(f'{year} {match[2]}', '%Y %b %d %H:%M:%S.%f' if '.' in match[2] else '%Y %b %d %H:%M:%S')
                            timestamp = date.astimezone(timezone.utc).timestamp()
                            if timestamp > until + 86400:
                                timestamp = date.replace(year=year - 1).astimezone(timezone.utc).timestamp()
                        except ValueError:
                            incomplete = True
                            continue
                        if not since <= timestamp <= until:
                            continue
                        scanned += 1
                        if scanned > maximum:
                            return records, True, True
                        message = scrub_message(match[3])
                        level = match[1] if match[1] in LEVELS else 'ERROR' if match[1] == 'CRITICAL' else 'INFO'
                        records.append(dict(timestamp=iso(timestamp), level=level,
                            service='proxy_traffic' if traffic_message(match[3]) else 'tinyproxy', message=message,
                            _raw=match[3], _pid=re.search(r'\[(\d+)\]', text)[1], _origin='tinyproxy-file',
                            _time=timestamp, _id=hashlib.sha256(line).hexdigest()))
                finally:
                    if stream is not handle:
                        stream.close()
        except FileNotFoundError:
            continue
    return records, incomplete, True


def query_logs(arguments, audit_path):
    now = time.time()
    configuration = policy()
    service = arguments.get('service', 'all')
    if service not in (*SERVICES, 'all'):
        raise ValueError('Choose a valid log source.')
    level = arguments.get('level', '').upper()
    if level and level not in LEVELS:
        raise ValueError('Choose a valid log level.')
    try:
        limit = min(max(int(arguments.get('limit', 1000)), 1), bounded_setting('LOG_API_MAX_PAGE_LIMIT', 5000, 1, 5000))
        offset = int(arguments.get('offset', 0))
        maximum = min(max(int(arguments.get('scan_limit', 12000)), 1000), bounded_setting('LOG_API_MAX_SCAN_LIMIT', 120000, 1000, 120000))
    except (ValueError, TypeError):
        raise ValueError('Invalid log pagination.') from None
    if not 0 <= offset <= 120000:
        raise ValueError('Invalid log pagination.')
    until = now
    for key in ('until', 'anchor'):
        if arguments.get(key):
            value = record_time(arguments[key])
            if value is None or value > now + 60:
                raise ValueError('Use a valid date and time with a timezone.')
            until = min(until, value)
    cutoff = now - max(configuration['auditDays'], configuration['managementDays'], configuration['trafficDays']) * 86400
    since = cutoff
    if arguments.get('since'):
        value = record_time(arguments['since'])
        if value is None:
            raise ValueError('Use a valid date and time with a timezone.')
        since = max(cutoff, value)
    if since > until:
        raise ValueError('The start date must be before the end date and within the retention period.')
    query = str(arguments.get('q', '')).strip().casefold()
    if len(query) > 256:
        raise ValueError('Use a search query of at most 256 characters.')
    records, errors, truncated, malformed = [], [], False, 0
    if service in ('all', 'audit'):
        try:
            items, partial, malformed = audit_records(audit_path, max(since, now - configuration['auditDays'] * 86400), until, maximum)
            records.extend(items)
            truncated |= partial
        except (OSError, ValueError, TimeoutError):
            errors.append('audit')
    if service != 'audit' and sys.platform != 'win32':
        namespaces = ['proxypermit', 'proxypermit-traffic', None] if configuration['managed'] else [None]
        for namespace in namespaces:
            if namespace == 'proxypermit' and service in ('tinyproxy', 'proxy_traffic'):
                continue
            if namespace == 'proxypermit-traffic' and service in ('manager', 'keepalived'):
                continue
            try:
                items, partial = journal_records(namespace, since, until, maximum)
                records.extend(items)
                truncated |= partial
            except (OSError, subprocess.SubprocessError):
                errors.append(namespace or 'host journal')
    external_file = False
    if service in ('all', 'tinyproxy', 'proxy_traffic') and sys.platform != 'win32':
        try:
            items, partial, external_file = proxy_file_records(max(since, now - configuration['trafficDays'] * 86400), until, maximum)
            records.extend(items)
            truncated |= partial
        except (OSError, ValueError, EOFError):
            errors.append('Tinyproxy file logging')
    configuration['externalFileLogging'] = external_file
    identify_health_checks(records)
    include_health = arguments.get('health') == 'include' or bool(query)
    hidden_health = 0
    seen, filtered = set(), []
    for item in records:
        days = configuration['auditDays'] if item['service'] == 'audit' else configuration['trafficDays'] if item['service'] in ('tinyproxy', 'proxy_traffic') else configuration['managementDays']
        if not max(since, now - days * 86400) <= item['_time'] <= until:
            continue
        if service != 'all' and item['service'] != service or level and item['level'] != level:
            continue
        if query and query not in ' '.join(str(item.get(key, '')) for key in ('message', 'timestamp', 'level', 'service', 'audit')).casefold():
            continue
        if item.get('healthCheck') and not include_health:
            hidden_health += 1
            continue
        if item['_id'] in seen:
            continue
        seen.add(item['_id'])
        filtered.append(item)
    filtered.sort(key=lambda item: (item['_time'], item['_id']), reverse=True)
    page = [{key: value for key, value in item.items() if not key.startswith('_')} for item in filtered[offset:offset + limit]]
    return dict(success=True, logs=page, healthChecksHidden=hidden_health, pagination=dict(offset=offset, limit=limit, total=len(filtered), returned=len(page),
                hasMore=offset + len(page) < len(filtered), query=query, anchor=iso(until), truncated=truncated),
                serviceCounts={name: sum(item['service'] == name for item in filtered) for name in SERVICES},
                coverage=dict(since=iso(since), until=iso(until), incomplete=bool(truncated or errors or malformed),
                              unavailable=errors, malformed=malformed), retention=configuration)
