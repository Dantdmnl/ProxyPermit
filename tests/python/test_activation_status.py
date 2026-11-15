"""Background activation reports application outcome rather than process liveness."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.web import manager as main


class ActivationStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.patches = [
            patch.object(main, 'SESSION_STATE_FILE', str(root / 'session.json')),
            patch.object(main, 'DEMO_MODE', False),
            patch.object(main.whitelist_manager, 'whitelist_path', root / 'filters'),
            patch.object(main, 'has_pending_changes', return_value=False),
            patch.object(main.threading, 'Thread', side_effect=lambda target, daemon: SimpleNamespace(start=target)),
            patch.object(main, '_audit_event'),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_success_is_recorded_after_confirmed_activation(self):
        with patch.object(main.whitelist_manager, 'restart_tinyproxy', return_value=(True, None)), \
                patch.object(main, 'record_published_policy') as publish:
            main.restart_tinyproxy_async()
        self.assertEqual(main.activation_status()['state'], 'succeeded')
        publish.assert_called_once()

    def test_running_service_does_not_hide_activation_failure(self):
        with patch.object(main.whitelist_manager, 'restart_tinyproxy', return_value=(False, 'unsafe backend diagnostic')), \
                patch.object(main, 'record_published_policy') as publish:
            main.restart_tinyproxy_async()
        result = main.activation_status()
        self.assertEqual(result['state'], 'failed')
        self.assertNotIn('unsafe backend', result['error'])
        publish.assert_not_called()

    def test_opt_in_reload_failure_never_restarts_or_publishes(self):
        with patch.dict('os.environ', {'TINYPROXY_RELOAD_ENABLED': 'true'}), \
                patch.object(main.whitelist_manager, 'reload_tinyproxy', return_value=(False, 'Reload was not acknowledged.')), \
                patch.object(main.whitelist_manager, 'restart_tinyproxy') as restart, \
                patch.object(main, 'record_published_policy') as publish:
            main.restart_tinyproxy_async('policy')
        self.assertEqual(main.activation_status()['state'], 'failed')
        restart.assert_not_called()
        publish.assert_not_called()

    def test_pending_configuration_prevents_background_activation(self):
        with patch.object(main, 'has_pending_changes', return_value=True), \
                patch.object(main.whitelist_manager, 'restart_tinyproxy') as restart:
            main.restart_tinyproxy_async()
        self.assertEqual(main.activation_status()['state'], 'failed')
        restart.assert_not_called()
