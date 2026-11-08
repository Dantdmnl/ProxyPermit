import os
import tempfile
import unittest
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from app.web import manager as main
from app.i18n import CATALOGS, translate


class ImportFieldParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.attributes = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'textarea' and attributes.get('id') == 'importContent':
            self.attributes = attributes


class LocaleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name) / 'tinyproxy.conf'
        self.config.write_text('Port 3128\nAllow example.nl\n')
        self.environment = patch.dict(os.environ, TINYPROXY_CONFIG_PATH=str(self.config), DEPLOYMENT_MODE='standalone')
        self.environment.start()
        self.auth = patch.object(main, 'ENABLE_AUTH', False)
        self.auth.start()
        self.client = main.app.test_client()

    def tearDown(self):
        self.auth.stop()
        self.environment.stop()
        self.temp.cleanup()

    def test_dutch_and_english_pages_render(self):
        for locale, heading in [('nl', 'Overzicht'), ('en', 'Overview')]:
            response = self.client.get('/?lang=' + locale)
            self.assertEqual(response.status_code, 200)
            self.assertIn(f'<html lang="{locale}">'.encode(), response.data)
            self.assertIn(heading.encode(), response.data)
            self.assertEqual(response.headers['Content-Language'], locale)

    def test_language_choice_survives_navigation(self):
        self.client.get('/?lang=en')
        response = self.client.get('/proxy')
        self.assertIn(b'Your proxy, your settings.', response.data)
        self.assertEqual(response.headers['Content-Language'], 'en')

    def test_browser_language_and_invalid_parameter(self):
        response = self.client.get('/?lang=invalid', headers={'Accept-Language': 'en-GB,en;q=0.9'})
        self.assertEqual(response.headers['Content-Language'], 'en')
        self.assertNotIn('proxypermit_locale=invalid', response.headers.get('Set-Cookie', ''))

    def test_api_errors_are_localized_but_config_content_is_not(self):
        loaded = self.client.get('/api/proxy/config?lang=en').get_json()
        self.assertEqual(loaded['content'], 'Port 3128\nAllow example.nl\n')
        for lang, expected in (
            ('en', 'Line 1 (Port): expected a number between 1 and 65535.'),
            ('nl', 'Regel 1 (Port): verwacht een getal van 1 t/m 65535.'),
        ):
            with self.subTest(lang=lang):
                response = self.client.post(
                    '/api/proxy/config/validate?lang=' + lang,
                    json={'content': 'Port 0\nAllow example.nl\n'},
                    headers={'X-CSRF-Token': loaded['csrfToken']})
                self.assertIn(expected, response.get_json()['errors'])

    def test_catalogs_have_identical_keys_and_placeholders(self):
        import re
        self.assertEqual(set(CATALOGS['nl']), set(CATALOGS['en']))
        for source, translated in CATALOGS['en'].items():
            self.assertEqual(sorted(re.findall(r'\{\d+\}', source)), sorted(re.findall(r'\{\d+\}', translated)), source)

    def test_unknown_user_text_remains_unchanged(self):
        with main.app.test_request_context('/?lang=en'):
            main.app.preprocess_request()
            self.assertEqual(translate('Mijn bedrijfsregel.example.nl'), 'Mijn bedrijfsregel.example.nl')

    def test_locale_json_is_safe_inside_script_element(self):
        response = self.client.get('/?lang=en')
        self.assertNotIn(b'{{', response.data)
        self.assertIn(b'/static/js/i18n.js', response.data)
        self.assertIn(b'/static/css/console.css', response.data)

    def test_settings_modal_is_removed_and_account_action_is_direct(self):
        response = self.client.get('/')
        self.assertNotIn(b'id="settingsModal"', response.data)
        self.assertNotIn(b'openSettingsModal()', response.data)
        self.assertIn(b'id="logoutButton"', response.data)
        self.assertIn(b'/static/js/rules.js', response.data)

    def test_leading_button_icons_are_preserved_in_both_languages(self):
        for source, dutch in CATALOGS['nl'].items():
            prefix = ''
            for character in dutch:
                if unicodedata.category(character).startswith('S') or character in '\ufe0f\u200d':
                    prefix += character
                else:
                    break
            if prefix:
                self.assertTrue(CATALOGS['en'][source].startswith(prefix), source)

    def test_import_placeholder_has_real_line_breaks_and_localized_examples(self):
        for locale, first_line, comment in [
            ('nl', 'Plak filterregels (één per regel).', '# Dit is een opmerking'),
            ('en', 'Paste filter rules (one per line).', '# This is a comment'),
        ]:
            with self.subTest(locale=locale):
                response = self.client.get('/?lang=' + locale)
                parser = ImportFieldParser()
                parser.feed(response.data.decode('utf-8'))
                placeholder = parser.attributes['placeholder']
                self.assertEqual(placeholder.splitlines(), [
                    first_line, '', 'Voorbeelden:' if locale == 'nl' else 'Examples:',
                    'github.com', '*.github.com', 'api.example.com', comment,
                ])
                self.assertNotIn('&#10;', placeholder)
                self.assertNotIn('&amp;#', response.data.decode('utf-8'))
                self.assertIn('autofocus', parser.attributes)

    def test_import_placeholder_remains_attribute_escaped(self):
        text = '\" onfocus=\"alert(1)\"><script>text</script>\nsecond line'
        with patch.dict(CATALOGS['en'], {'import.placeholder': text}):
            parser = ImportFieldParser()
            parser.feed(self.client.get('/?lang=en').data.decode('utf-8'))
        self.assertEqual(parser.attributes['placeholder'], text)
        self.assertNotIn('onfocus', parser.attributes)


if __name__ == '__main__':
    unittest.main()
