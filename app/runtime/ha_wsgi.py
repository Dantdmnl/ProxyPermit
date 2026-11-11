"""Narrow WSGI entry point for the internal TLS replication listener."""
from ..web.manager import app


def application(environ, start_response):
    allowed = {('/internal/ha/enroll', 'POST'), ('/internal/ha/policy', 'GET')}
    if (environ.get('PATH_INFO'), environ.get('REQUEST_METHOD')) not in allowed:
        start_response('404 Not Found', [('Content-Type', 'text/plain'), ('Content-Length', '9')])
        return [b'Not found']
    return app(environ, start_response)
