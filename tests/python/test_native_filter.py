"""Native validation must check the patterns that Tinyproxy actually reads."""
import unittest
from app.core.native_filter import native_patterns, validate_filter


class NativeFilterTests(unittest.TestCase):
    def test_comments_are_removed_before_regex_validation(self):
        self.assertEqual(native_patterns(' # comment [\n^example\\.com$ # description [\n^other$# [\n'),
                         ['^example\\.com$', '^other$'])
        self.assertTrue(validate_filter('^example$ # comment with unmatched [\n', 'ere')['valid'])

    def test_escaped_hash_remains_part_of_pattern(self):
        self.assertEqual(native_patterns(r'^example\#fragment$ # comment'), [r'^example\#fragment$'])

    def test_oversize_lines_are_rejected_instead_of_silently_split(self):
        self.assertFalse(validate_filter('x' * 511, 'fnmatch')['valid'])
        self.assertFalse(validate_filter('# ' + 'x' * 511, 'fnmatch')['valid'])
        self.assertFalse(validate_filter('é' * 256, 'fnmatch')['valid'])
