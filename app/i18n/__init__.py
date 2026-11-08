"""Shared, request-scoped Dutch/English catalog for templates and browser UI."""
import json
import re
from pathlib import Path

from flask import g, request
from .. import __version__

LOCALES = ('nl', 'en')
CATALOGS = {locale: json.loads((Path(__file__).parent / 'catalogs' / (locale + '.json')).read_text(encoding='utf-8'))
            for locale in LOCALES}


def translate(text):
    catalog = CATALOGS[getattr(g, 'locale', 'nl')]
    direct = catalog.get(text)
    if direct is not None and direct != text:
        return direct
    normalized = ' '.join(text.split())
    if catalog.get(normalized, normalized) != normalized:
        return catalog[normalized]
    for source, target in catalog.items():
        if '{0}' not in source or source == target:
            continue
        pattern = re.sub(r'\\\{\d+\\\}', '(.*?)', re.escape(source))
        match = re.fullmatch(pattern, text, flags=re.S)
        if match:
            return re.sub(r'\{(\d+)\}', lambda part: translate(match[int(part[1]) + 1]), target)
    return direct if direct is not None else text


def register_i18n(app):
    @app.before_request
    def select_locale():
        requested = request.args.get('lang')
        saved = request.cookies.get('proxypermit_locale')
        g.locale = requested if requested in LOCALES else saved if saved in LOCALES else request.accept_languages.best_match(LOCALES, default='nl')

    @app.context_processor
    def locale_context():
        return dict(_=translate, locale=g.locale, locale_catalog=CATALOGS[g.locale], app_version=__version__)

    @app.after_request
    def locale_headers(response):
        if response.is_json and request.path.startswith('/api/') and not request.path.startswith('/api/logs'):
            payload = response.get_json(silent=True)
            if isinstance(payload, dict):
                for key in ('error', 'message', 'errors', 'warning', 'warnings', 'restart_error'):
                    value = payload.get(key)
                    if isinstance(value, str):
                        payload[key] = translate(value)
                    elif isinstance(value, list):
                        payload[key] = [translate(item) if isinstance(item, str) else item for item in value]
                response.set_data(app.json.dumps(payload))
        response.headers['Content-Language'] = g.locale
        response.vary.add('Accept-Language')
        response.vary.add('Cookie')
        if request.args.get('lang') in LOCALES:
            response.set_cookie('proxypermit_locale', g.locale, max_age=365 * 86400,
                                httponly=True, secure=request.is_secure, samesite='Lax')
        return response
