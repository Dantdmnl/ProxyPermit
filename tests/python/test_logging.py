"""Privacy, retention, archive-query and pagination regression tests."""
import json
import logging
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.core.audit import SharedAuditHandler, audit_paths, open_audit, prune_audit
from app.web import logs
from scripts.deploy.logging_policy import assets


class LoggingTests(unittest.TestCase):
    def test_health_visibility_preserves_raw_history_search_and_redaction(self):
        timestamp = time.time() - 1
        records = [dict(timestamp=logs.iso(timestamp), level='INFO', service='proxy_traffic',
                        message='recognized probe', healthCheck=True, _time=timestamp,
                        _id='probe', _raw='private raw metadata'),
                   dict(timestamp=logs.iso(timestamp), level='ERROR', service='tinyproxy',
                        message='real client failure', _time=timestamp, _id='error')]
        with patch.object(logs.sys, 'platform', 'linux'), \
                patch.object(logs, 'journal_records', return_value=(records, False)), \
                patch.object(logs, 'proxy_file_records', return_value=([], False, False)):
            normal = logs.query_logs({'service': 'all'}, self.path)
            included = logs.query_logs({'service': 'all', 'health': 'include'}, self.path)
            searched = logs.query_logs({'service': 'all', 'q': 'recognized probe'}, self.path)
        self.assertEqual([item['message'] for item in normal['logs']], ['real client failure'])
        self.assertEqual(len(included['logs']), 2)
        self.assertEqual(searched['logs'][0]['message'], 'recognized probe')
        self.assertNotIn('_raw', included['logs'][0])
        self.assertTrue(records[0]['healthCheck'])

    def test_health_log_identification_requires_local_matching_connection_and_preserves_errors(self):
        def row(raw, order, level='INFO', pid='42'):
            return dict(message=logs.scrub_message(raw), _raw=raw, _time=100 + order / 100,
                        level=level, service='proxy_traffic', _pid=pid, _origin='fixture')
        local = row('Connect (file descriptor 5): 127.0.0.1', 0)
        request = row('Request (file descriptor 5): GET http://tinyproxy.stats/__proxypermit_health__ HTTP/1.0', 1)
        error = row('read_request_line: Client (file descriptor: 5) closed socket before read.', 2, 'ERROR')
        remote = row('Connect (file descriptor 7): 198.51.100.1', 3)
        remote_request = row('Request (file descriptor 7): GET http://tinyproxy.stats/__proxypermit_health__ HTTP/1.0', 4)
        unrelated = row('Request (file descriptor 8): GET http://tinyproxy.stats/__proxypermit_health__ HTTP/1.0', 5)
        with patch.dict(os.environ, TINYPROXY_CONFIG_PATH='/missing-example-configuration'):
            logs.identify_health_checks([unrelated, remote_request, remote, error, request, local])
        self.assertTrue(local['healthCheck'])
        self.assertTrue(request['healthCheck'])
        for item in (error, remote, remote_request, unrelated):
            self.assertFalse(item.get('healthCheck', False))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'audit.log'
        self.now = time.time()
        environment = patch.dict(os.environ, AUDIT_LOG_RETENTION_DAYS='30', AUDIT_LOG_MAX_BYTES='65536',
                                 AUDIT_LOG_BACKUP_COUNT='5', LOG_RETENTION_DAYS='30', PROXY_TRAFFIC_RETENTION_DAYS='7',
                                 LOG_IP_MODE='masked', FLASK_SECRET_KEY='isolated-test-key')
        environment.start()
        self.addCleanup(environment.stop)

    def entry(self, identifier, age=0, message='AUDIT event=example, username=alice'):
        return dict(id=identifier, timestamp=logs.iso(self.now - age * 86400), level='INFO',
                    event='example', username='alice', message=message)

    def write(self, path, items):
        path.write_text(''.join(json.dumps(item) + '\n' for item in items), encoding='utf-8')

    def test_quiet_active_file_and_archives_expire_by_record_age(self):
        self.write(self.path, [self.entry('expired', 31), self.entry('kept', 29)])
        archive = self.path.with_name('audit.log.1')
        self.write(archive, [self.entry('archive-expired', 45), self.entry('archive-kept', 20)])
        empty = self.path.with_name('audit.log.2')
        self.write(empty, [self.entry('all-expired', 32)])
        self.assertEqual(prune_audit(self.path, now=self.now), 3)
        self.assertEqual([json.loads(line)['id'] for line in self.path.read_text().splitlines()], ['kept'])
        self.assertEqual([json.loads(line)['id'] for line in archive.read_text().splitlines()], ['archive-kept'])
        self.assertFalse(empty.exists())

    def test_retention_removes_malformed_records_and_enforces_storage_budget(self):
        self.write(self.path, [self.entry(str(number), message='x' * 2000) for number in range(60)])
        with self.path.open('a') as handle:
            handle.write('not json\n')
        excess = self.path.with_name('audit.log.9')
        self.write(excess, [self.entry('excess')])
        self.assertGreater(prune_audit(self.path, now=self.now), 1)
        self.assertLessEqual(self.path.stat().st_size, 65536)
        self.assertFalse(excess.exists())
        self.assertEqual(json.loads(self.path.read_text().splitlines()[-1])['id'], '59')

    def test_archive_search_uses_full_utc_dates_and_does_not_return_expired_records(self):
        self.write(self.path, [self.entry('new', 1)])
        self.write(self.path.with_name('audit.log.1'), [self.entry('old', 10), self.entry('expired', 40)])
        result = logs.query_logs(dict(service='audit', since=logs.iso(self.now - 11 * 86400),
                                      until=logs.iso(self.now - 9 * 86400)), self.path)
        self.assertEqual(len(result['logs']), 1)
        self.assertEqual(result['logs'][0]['audit']['username'], 'alice')
        self.assertEqual(result['logs'][0]['timestamp'], self.entry('old', 10)['timestamp'])
        self.assertFalse(result['coverage']['incomplete'])

    def test_old_date_search_is_not_exhausted_by_newer_records(self):
        self.write(self.path, [self.entry(str(number), 1) for number in range(1100)])
        self.write(self.path.with_name('audit.log.1'), [self.entry('historical', 10)])
        result = logs.query_logs(dict(service='audit', scan_limit=1000, until=logs.iso(self.now - 9 * 86400)), self.path)
        self.assertEqual(len(result['logs']), 1)
        self.assertFalse(result['pagination']['truncated'])

    def test_anchor_prevents_new_records_from_shifting_pages_and_rotation_does_not_duplicate(self):
        self.write(self.path, [self.entry('first', 3), self.entry('second', 2), self.entry('third', 1)])
        first = logs.query_logs(dict(service='audit', limit=1), self.path)
        self.path.rename(self.path.with_name('audit.log.1'))
        self.write(self.path, [dict(self.entry('new'), timestamp=logs.iso(self.now + 2))])
        second = logs.query_logs(dict(service='audit', limit=1, offset=1, anchor=first['pagination']['anchor']), self.path)
        self.assertEqual(first['logs'][0]['timestamp'], self.entry('third', 1)['timestamp'])
        self.assertEqual(second['logs'][0]['timestamp'], self.entry('second', 2)['timestamp'])

    def test_invalid_ranges_pagination_and_queries_are_rejected_before_reading(self):
        bad = [dict(since='yesterday'), dict(since='2026-01-01T12:00:00'), dict(offset='-1'),
               dict(since=logs.iso(self.now), until=logs.iso(self.now - 100)), dict(q='x' * 257)]
        with patch.object(logs, 'journal_records') as journal:
            for query in bad:
                with self.assertRaises(ValueError):
                    logs.query_logs(query, self.path)
            journal.assert_not_called()

    def test_scan_limit_is_explicit_and_repeated_distinct_events_are_kept(self):
        self.write(self.path, [self.entry(str(number)) for number in range(1005)])
        result = logs.query_logs(dict(service='audit', scan_limit=1000), self.path)
        self.assertEqual(len(result['logs']), 1000)
        self.assertTrue(result['pagination']['truncated'])
        self.assertTrue(result['coverage']['incomplete'])

    def test_redaction_covers_credentials_url_queries_ipv4_and_ipv6_and_is_idempotent(self):
        raw = 'https://user:password@example.com/path?token=sensitive 192.0.2.123 2001:db8::1234 secret=hidden'
        result = logs.scrub_message(raw)
        for secret in ('password', 'sensitive', '192.0.2.123', '2001:db8::1234', 'hidden'):
            self.assertNotIn(secret, result)
        self.assertIn('https://example.com/path', result)
        self.assertIn('192.0.2.0/24', result)
        self.assertEqual(logs.scrub_message(result), result)

    def test_audit_sink_errors_do_not_copy_the_record_to_operational_logs(self):
        previous = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, previous)
        handler = SharedAuditHandler(self.path, delay=True)
        self.addCleanup(handler.close)
        record = logging.LogRecord('audit', logging.INFO, '', 0, 'private-record-fixture', (), None)
        with patch('app.core.audit.prune_audit', side_effect=PermissionError('private-path')):
            with self.assertLogs('proxypermit.audit', level='ERROR') as captured:
                handler.handle(record)
        self.assertNotIn('private-record-fixture', ' '.join(captured.output))
        self.assertNotIn('private-path', ' '.join(captured.output))

    def test_multiple_writers_rotate_without_losing_or_duplicating_records(self):
        def writer(worker):
            handler = SharedAuditHandler(self.path, maxBytes=1024, backupCount=50, delay=True)
            handler.setFormatter(logging.Formatter('%(message)s'))
            try:
                for number in range(20):
                    handler.handle(logging.LogRecord('fixture', logging.INFO, '', 0,
                        json.dumps(self.entry(f'{worker}-{number}')), (), None))
            finally:
                handler.close()
        with patch.dict(os.environ, AUDIT_LOG_BACKUP_COUNT='50'):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(writer, range(4)))
        identifiers = []
        for source in audit_paths(self.path):
            identifiers.extend(json.loads(line)['id'] for line in source.read_text().splitlines())
        self.assertEqual(len(identifiers), 80)
        self.assertEqual(len(set(identifiers)), 80)

    @unittest.skipIf(os.name == 'nt', 'Linux no-follow and FIFO checks.')
    def test_audit_reader_refuses_symlinks_and_fifos(self):
        target = self.path.with_name('protected')
        target.write_text('private fixture')
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            with open_audit(self.path):
                self.fail('A symlink was opened.')
        self.path.unlink()
        os.mkfifo(self.path)  # pylint: disable=no-member
        with self.assertRaises(ValueError):
            with open_audit(self.path):
                self.fail('A FIFO was opened.')
        self.assertEqual(target.read_text(), 'private fixture')

    def test_native_journal_dates_levels_and_cursors_are_used(self):
        item = dict(__REALTIME_TIMESTAMP=str(int(self.now * 1000000)), MESSAGE='proxy failed',
                    PRIORITY='3', _SYSTEMD_UNIT='tinyproxy.service', __CURSOR='cursor-fixture')
        with patch.object(logs, 'journal_lines', return_value=([json.dumps(item)], False)) as read:
            records, incomplete = logs.journal_records('proxypermit-traffic', self.now - 10, self.now, 20)
        self.assertEqual(records[0]['level'], 'ERROR')
        self.assertEqual(records[0]['service'], 'tinyproxy')
        self.assertEqual(records[0]['_id'], 'cursor-fixture')
        self.assertFalse(incomplete)
        command = read.call_args[0][0]
        self.assertIn('--namespace=proxypermit-traffic', command)
        self.assertTrue(any(value.startswith('--since=@') for value in command))
        self.assertTrue(any(value.startswith('--until=@') for value in command))
        self.assertNotIn('--directory', command)

    def test_missing_sources_are_reported_without_disclosing_exception_text(self):
        with patch.object(logs.sys, 'platform', 'linux'), patch.object(logs, 'journal_records', side_effect=OSError('private path')), \
                patch.object(logs, 'proxy_file_records', return_value=([], False, False)):
            result = logs.query_logs(dict(service='manager'), self.path)
        self.assertTrue(result['coverage']['incomplete'])
        self.assertNotIn('private path', json.dumps(result))

    def test_malformed_journal_and_audit_records_report_incomplete_coverage(self):
        with patch.object(logs, 'journal_lines', return_value=(['invalid json'], False)):
            records, incomplete = logs.journal_records(None, self.now - 100, self.now, 100)
        self.assertFalse(records)
        self.assertTrue(incomplete)
        self.path.write_text('invalid json\n')
        result = logs.query_logs(dict(service='audit'), self.path)
        self.assertEqual(result['coverage']['malformed'], 1)
        self.assertTrue(result['coverage']['incomplete'])

    def test_custom_proxy_log_path_cannot_expose_arbitrary_host_files(self):
        config = self.path.with_name('tinyproxy.conf')
        config.write_text('Syslog Off\nLogFile "/etc/shadow"\n')
        with patch.dict(os.environ, TINYPROXY_CONFIG_PATH=str(config)):
            with self.assertRaisesRegex(ValueError, 'custom Tinyproxy logfile'):
                logs.proxy_file_records(self.now - 100, self.now, 100)

    def test_managed_policies_scope_journals_and_do_not_modify_global_retention(self):
        generated = assets('/opt/proxypermit/releases/fixture', {})
        self.assertNotIn(Path('/etc/systemd/journald.conf'), generated)
        self.assertNotIn(Path('/etc/systemd/system/keepalived.service.d/20-proxypermit-logging.conf'), generated)
        traffic = generated[Path('/etc/systemd/journald@proxypermit-traffic.conf.d/20-proxypermit.conf')]
        self.assertIn('MaxRetentionSec=7day', traffic)
        self.assertIn('ForwardToSyslog=no', traffic)
        audit = generated[Path('/etc/systemd/system/proxypermit-audit-retention.service')]
        self.assertIn('User=proxypermit', audit)
        self.assertNotIn('/current/', audit)
        with self.assertRaises(ValueError):
            assets('/opt/proxypermit/releases/fixture', dict(LOG_RETENTION_DAYS='0'))
