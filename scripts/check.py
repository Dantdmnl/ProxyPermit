#!/usr/bin/env python3
"""The same focused checks locally and in CI. Run with development dependencies."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*command):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=ROOT, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', choices=['quality', 'types', 'python', 'frontend', 'http', 'version', 'shell'], nargs='+')
    args = parser.parse_args(argv)
    checks = args.only or ['version', 'quality', 'types', 'python', 'frontend', 'shell', 'http']
    try:
        for check in checks:
            if check == 'version':
                run(sys.executable, 'scripts/release.py', 'check')
            elif check == 'quality':
                files = sorted(str(path.relative_to(ROOT)) for directory in ('app', 'scripts', 'tests/python', 'legacy', 'deploy')
                               for path in (ROOT / directory).rglob('*.py'))
                run(sys.executable, '-m', 'pylint', '-E', *files, 'wsgi.py')
            elif check == 'types':
                for platform in ('Windows', 'Linux'):
                    run(sys.executable, '-m', 'pyright', '--pythonpath', sys.executable,
                        '--pythonplatform', platform, 'scripts/deploy/install.py')
            elif check == 'python':
                run(sys.executable, 'scripts/dev/test.py')
            elif check == 'frontend':
                for path in sorted((ROOT / 'app/static/js').glob('*.js')):
                    run('node', '--check', path)
                for path in sorted((ROOT / 'tests/js').glob('test_*.cjs')):
                    run('node', path)
            elif check == 'shell':
                if os.name == 'nt':
                    print('Shell checks require Linux/WSL; skipped on Windows.')
                    continue
                files = [ROOT / 'install.sh'] + list((ROOT / 'legacy').glob('*.sh')) + list((ROOT / 'scripts/deploy').glob('*.sh'))
                for path in sorted(files):
                    run('bash', '-n', path)
            elif check == 'http':
                for server in (['granian'] if os.name == 'nt' else ['gunicorn', 'granian']):
                    run(sys.executable, 'scripts/dev/check_server.py', '--backend', server)
        return 0
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f'Check failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
