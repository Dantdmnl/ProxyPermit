"""Explicit WSGI server selection, without importing the web app during planning."""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _integer(value, name, minimum, maximum):
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise ValueError(f'{name} must be an integer.') from None
    if not minimum <= number <= maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}.')
    return str(number)


def build_command(backend, env, executable=None):
    python = executable or sys.executable
    workers = _integer(env.get('APP_WORKERS', env.get('GUNICORN_WORKERS', '2')), 'APP_WORKERS', 1, 64)
    if backend == 'gunicorn':
        command = [python, '-m', 'gunicorn', '--config', str(ROOT / 'deploy/gunicorn.conf.py'), '--workers', workers]
        if env.get('APP_BIND'):
            command.extend(['--bind', env['APP_BIND']])
        return command + ['wsgi:app']
    if backend != 'granian':
        raise ValueError('Set APP_SERVER to gunicorn or granian.')
    # Keep direct TLS migrations explicit: Granian requires PKCS#8 keys.
    tls_override = env.get('GUNICORN_USE_TLS', '').lower()
    tls = tls_override in ('true', '1', 'yes') or (
        not tls_override and env.get('MANAGER_SCHEME', 'http').lower() == 'https')
    if tls:
        raise ValueError('Use Granian behind a TLS-terminating reverse proxy and set GUNICORN_USE_TLS=false. Use Gunicorn for direct TLS.')
    command = [python, '-m', 'granian', '--interface', 'wsgi', '--workers', workers,
               '--blocking-threads', '1', '--working-dir', str(ROOT), '--no-access-log']
    bind = env.get('APP_BIND') or env.get('GUNICORN_BIND') or f"{env.get('MANAGER_BIND', '127.0.0.1')}:{env.get('MANAGER_PORT', '8080')}"
    if bind.startswith('unix:'):
        path = bind[5:]
        if not path.startswith('/'):
            raise ValueError('A Unix socket requires an absolute path.')
        command.extend(['--uds', path, '--uds-permissions', '0660'])
    else:
        if ',' in bind:
            raise ValueError('This Granian integration supports one listener.')
        host, separator, port = bind.rpartition(':')
        if not separator or not host:
            raise ValueError('Set APP_BIND to host:port or unix:/path.')
        command.extend(['--host', host.strip('[]'), '--port', _integer(port, 'Poort', 1, 65535)])
    return command + ['wsgi:app']


def main(argv=None):
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    load_dotenv('/etc/proxypermit/.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['gunicorn', 'granian'])
    parser.add_argument('--check', action='store_true', help='Print the server command without starting it.')
    args = parser.parse_args(argv)
    backend = args.backend or os.getenv('APP_SERVER', 'gunicorn')
    try:
        command = build_command(backend, os.environ)
        if args.check:
            print(json.dumps(command))
            return 0
        if importlib.util.find_spec(backend) is None:
            raise ValueError(f'{backend} is missing. Install the corresponding requirements.')
        if backend == 'gunicorn' and os.name == 'nt':
            raise ValueError('Gunicorn requires Unix. Use WSL or choose Granian.')
        os.chdir(ROOT)
        os.execv(sys.executable, command)
        return 0
    except (OSError, ValueError) as exc:
        print(f'Could not start the server: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
