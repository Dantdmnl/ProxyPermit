import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from pathlib import Path

from app.core.filters import WhitelistManager


class WhitelistManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.whitelist_path = Path(self.temp_dir.name) / 'whitelist.txt'
        self.whitelist_path.write_text('# managed\n', encoding='utf-8')
        self.manager = WhitelistManager(str(self.whitelist_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_add_domain_writes_canonical_regex(self):
        success, error = self.manager.add_entry('example.com')
        self.assertTrue(success, error)
        self.assertIn(r'^example\.com(:[0-9]+)?$', self.whitelist_path.read_text(encoding='utf-8'))

    def test_replace_uses_validated_writer(self):
        success, error = self.manager.replace_all_entries([r'^example\.com$'])
        self.assertTrue(success, error)
        self.assertEqual(self.whitelist_path.read_text(encoding='utf-8'), '^example\\.com$\n')

    def test_replace_rejects_invalid_entry_without_changing_file(self):
        before = self.whitelist_path.read_text(encoding='utf-8')
        success, _ = self.manager.replace_all_entries([r'^.*$'])
        self.assertFalse(success)
        self.assertEqual(self.whitelist_path.read_text(encoding='utf-8'), before)

    def test_empty_lines_do_not_change_entry_identity(self):
        self.whitelist_path.write_text('^first\\.example$\n\n^second\\.example$\n^third\\.example$\n', encoding='utf-8')
        entries = self.manager.read_whitelist()
        success, error = self.manager.delete_entry(entries[1]['id'])
        self.assertTrue(success, error)
        self.assertEqual([entry['content'] for entry in self.manager.read_whitelist()],
                         [r'^first\.example$', r'^third\.example$'])

    def test_concurrent_manager_instances_do_not_lose_appends(self):
        other = WhitelistManager(str(self.whitelist_path))
        with patch('app.core.filters.logger'), ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit((self.manager if number % 2 else other).append_entries,
                                   [fr'^host{number}\.example$']) for number in range(12)]
            for future in futures:
                success, error = future.result()
                self.assertTrue(success, error)
        entries = [entry['content'] for entry in self.manager.read_whitelist() if entry['type'] != 'comment']
        self.assertEqual(len(entries), 12)
        self.assertEqual(len(set(entries)), 12)


if __name__ == '__main__':
    unittest.main()
