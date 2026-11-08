import unittest

from app.core.validators import WhitelistValidator


class WhitelistValidatorTests(unittest.TestCase):
    def test_normalizes_url_to_domain(self):
        self.assertEqual(
            WhitelistValidator.normalize_domain_input('HTTPS://Example.COM:443/path'),
            'example.com',
        )

    def test_exact_domain_pattern(self):
        self.assertEqual(
            WhitelistValidator.domain_to_regex('example.com'),
            r'^example\.com(:[0-9]+)?$',
        )

    def test_wildcard_includes_root_and_subdomains(self):
        self.assertEqual(
            WhitelistValidator.domain_to_regex('*.example.com'),
            r'^(.+\.)?example\.com(:[0-9]+)?$',
        )

    def test_rejects_match_everything_pattern(self):
        valid, error = WhitelistValidator.validate_regex_pattern(r'^.*$')
        self.assertFalse(valid)
        self.assertIn('too broad', error)

    def test_rejects_partial_ipv4_as_domain(self):
        valid, _, entry_type = WhitelistValidator.validate_whitelist_entry('192.168.1')
        self.assertFalse(valid)
        self.assertEqual(entry_type, 'domain')


if __name__ == '__main__':
    unittest.main()
