import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.core.tinyproxy import ConfigError, RevisionConflict, TinyproxyConfigStore, parse, summary, validate

BASE = 'Port 3128\nAllow 127.0.0.1\nConnectPort 443\n'


class ConfigTests(unittest.TestCase):
    def test_all_sections(self):
        content = BASE + '''Listen ::1
Bind 192.0.2.1
Timeout 600
MaxClients 100
BasicAuth user secret
Filter "/etc/tinyproxy/filter"
FilterType fnmatch
FilterDefaultDeny No
Upstream socks5 proxy.example:1080 ".example.com"
Upstream none ".internal"
AddHeader "X-Company" "Some Company"
Anonymous "Host"
ReversePath "/app/" "http://192.0.2.2/"
ReverseOnly Yes
LogLevel Connect
Syslog On
ErrorFile 403 "/usr/share/tinyproxy/403.html"
'''
        self.assertTrue(validate(content)['valid'], validate(content))
        self.assertEqual(summary(content)['filterMode'], 'blocklist')

    def test_invalid_values_and_conflicts(self):
        for extra in ('Port 0', 'Listen nope', 'Syslog On\nLogFile /tmp/test', 'FilterType regex',
                      'BasicAuth only-user', 'Upstream socks6 host:2', 'ReversePath app not-a-url',
                      'ConnectPort 65536', 'Timeout', 'FilterURLs', 'NewOption yes'):
            with self.subTest(extra=extra):
                self.assertFalse(validate(BASE + extra)['valid'])

    def test_open_proxy_is_rejected(self):
        self.assertFalse(validate('Port 3128\n')['valid'])

    def test_empty_or_malformed_content_never_crashes(self):
        for content in ('', 'Port\nAllow\nFilterDefaultDeny\n', 'Port "oops', '\x00'):
            self.assertFalse(validate(content)['valid'])

    def test_argument_errors_do_not_expose_credentials(self):
        checks = validate(BASE + 'Upstream http user:very-secret@bad-port\n')
        self.assertNotIn('very-secret', str(checks))

    def test_roundtrip_preserves_comments_unknowns_and_order(self):
        content = '# migration\n\n' + BASE + '# Custom stays disabled\n'
        with tempfile.TemporaryDirectory() as directory:
            store = TinyproxyConfigStore(Path(directory) / 'tinyproxy.conf')
            store.path.write_text(content, encoding='utf-8')
            store.save(content, store.read()['revision'])
            self.assertEqual(store.path.read_text(encoding='utf-8'), content)
        self.assertIsNone(parse('FutureDirective yes')[0]['spec'])

    def test_stale_revision_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TinyproxyConfigStore(Path(directory) / 'tinyproxy.conf')
            initial = store.read()['revision']
            store.save(BASE, initial)
            with self.assertRaises(RevisionConflict):
                store.save(BASE + 'Timeout 40\n', initial)
            self.assertEqual(store.read()['content'], BASE)

    def test_activation_failure_restores_exact_previous_content(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TinyproxyConfigStore(Path(directory) / 'tinyproxy.conf')
            store.path.write_text(BASE, encoding='utf-8')
            activate = Mock(side_effect=[(False, 'failure'), (True, None)])
            with self.assertRaisesRegex(ConfigError, 'The previous configuration was restored'):
                store.save(BASE + 'Timeout 40\n', store.read()['revision'], activate)
            self.assertEqual(store.read()['content'], BASE)
            self.assertEqual(activate.call_count, 2)

    def test_invalid_configuration_does_not_touch_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TinyproxyConfigStore(Path(directory) / 'tinyproxy.conf')
            with self.assertRaises(ConfigError):
                store.save('Port nope', store.read()['revision'])
            self.assertFalse(store.path.exists())

    def test_failed_activation_after_multiple_saves_restores_active_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TinyproxyConfigStore(Path(directory) / 'tinyproxy.conf')
            store.path.write_text(BASE, encoding='utf-8')
            store.save(BASE + 'Timeout 40\n', store.read()['revision'])
            store.save(BASE + 'Timeout 50\n', store.read()['revision'])
            with self.assertRaises(ConfigError):
                store.save(BASE + 'Timeout 60\n', store.read()['revision'], Mock(return_value=(False, 'failed')))
            self.assertEqual(store.path.read_text(encoding='utf-8'), BASE)
            self.assertEqual(store.read()['content'], BASE + 'Timeout 50\n')


if __name__ == '__main__':
    unittest.main()
