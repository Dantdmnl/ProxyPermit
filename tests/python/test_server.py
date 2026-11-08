import unittest

from app.runtime.server import build_command


class ServerCommandTests(unittest.TestCase):
    def test_gunicorn_remains_compatible_default(self):
        command = build_command('gunicorn', {}, 'python')
        self.assertEqual(command[:3], ['python', '-m', 'gunicorn'])
        self.assertEqual(command[-1], 'wsgi:app')

    def test_granian_uses_wsgi_and_single_blocking_thread(self):
        command = build_command('granian', {'APP_BIND': '127.0.0.1:9090'}, 'python')
        self.assertIn('wsgi', command)
        self.assertIn('--no-access-log', command)
        self.assertEqual(command[command.index('--port') + 1], '9090')
        self.assertEqual(command[command.index('--blocking-threads') + 1], '1')

    def test_ipv6_and_legacy_bind_are_preserved(self):
        command = build_command('granian', {'GUNICORN_BIND': '[::1]:8080'})
        self.assertEqual(command[command.index('--host') + 1], '::1')

    def test_unix_socket_has_restricted_permissions(self):
        command = build_command('granian', {'APP_BIND': 'unix:/run/proxypermit/app.sock'})
        self.assertEqual(command[command.index('--uds') + 1], '/run/proxypermit/app.sock')
        self.assertIn('0660', command)

    def test_invalid_settings_fail_before_start(self):
        for values in ({'APP_WORKERS': '0'}, {'APP_BIND': 'localhost:nope'},
                       {'APP_BIND': 'unix:relative'}, {'MANAGER_SCHEME': 'https'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                build_command('granian', values)


if __name__ == '__main__':
    unittest.main()
