"""Presentation for the installer wizard, using only the Python standard library."""
import os
import select
import shutil
import sys


class InstallTerminal:
    def __init__(self):
        self.color = sys.stdout.isatty() and os.getenv('TERM', 'dumb') != 'dumb' and 'NO_COLOR' not in os.environ

    def accent(self, text):
        return f'\033[1;36m{text}\033[0m' if self.color else text

    def heading(self, text):
        print(f'\n{self.accent(text)}')

    def ask(self, label, default=None, validate=None):
        hint = f' [{default}]' if default is not None else ''
        while True:
            value = input(f'  {self.accent(">")} {label}{hint}: ').strip()
            if not value and default is not None:
                value = default
            if not value:
                print('    Enter a value.')
                continue
            try:
                return validate(value) if validate else value
            except ValueError as exc:
                print(f'    {exc}')

    def choose(self, label, options, default=0) -> int:
        self.heading(label)
        if (os.name == 'posix' and sys.stdin.isatty() and sys.stdout.isatty()
                and os.getenv('TERM', 'dumb') != 'dumb' and shutil.get_terminal_size().columns >= 76):
            return self.choose_with_arrows(options, default)
        for index, option in enumerate(options):
            marker = self.accent('*') if index == default else ' '
            print(f'  {marker} {index + 1}. {option}')

        def selection(value):
            if value not in {str(index) for index in range(1, len(options) + 1)}:
                raise ValueError(f'Choose a number from 1 to {len(options)}.')
            return value

        return int(self.ask('Choice', str(default + 1), selection)) - 1

    def choose_with_arrows(self, options, selected) -> int:
        import termios  # pylint: disable=import-error
        import tty  # pylint: disable=import-error

        fd = sys.stdin.fileno()
        previous = termios.tcgetattr(fd)

        def draw():
            for index, option in enumerate(options):
                label = f'{index + 1}. {option}'
                print(f'\033[2K  {self.accent("> " + label) if index == selected else "  " + label}')
            print('\x1b[2K  ↑/↓ or number · Enter to confirm', flush=True)

        draw()
        try:
            tty.setcbreak(fd, termios.TCSANOW)
            while True:
                key = os.read(fd, 1)
                if key in (b'', b'\x04'):
                    raise EOFError
                if key in (b'\r', b'\n'):
                    return selected
                if key == b'\x1b':
                    key = b''
                    for _ in range(2):
                        if not select.select([fd], [], [], 0.05)[0]:
                            break
                        key += os.read(fd, 1)
                if key in (b'[A', b'OA'):
                    selected = (selected - 1) % len(options)
                elif key in (b'[B', b'OB', b'\t'):
                    selected = (selected + 1) % len(options)
                elif key.isdigit() and 1 <= int(key) <= len(options):
                    selected = int(key) - 1
                else:
                    continue
                print(f'\033[{len(options) + 1}A\r', end='')
                draw()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)

    def review(self, plan, args):
        self.heading('03  Review your installation plan')
        print(f"  Action          {('Upgrade' if plan['action'] == 'upgrade' else 'Fresh installation')}")
        print(f"  Topology        {('Two-node HA' if plan['mode'] == 'ha' else 'Standalone node')}")
        print(f"  Web server      {plan['server']}")
        print(f"  Release path    {plan['installDir']}")
        if plan['action'] == 'install':
            print(f'  Console         http://127.0.0.1:{args.manager_port}/')
            print(f'  Administrator   {args.admin_user}')
        if plan.get('ha'):
            ha = plan['ha']
            print(f"  Local node      {ha['node'].upper()} · {ha['local']} · {ha['interface']}")
            print(f"  Peer node       {ha['peer']}")
            print(f"  Virtual IP      {ha['vip']}")
            print(f"  Client network  {ha['clients']}")
            print(f"  VRRP-ID         {ha['vrid']}")
        self.heading('Installation steps')
        for step in plan['steps']:
            print(f'  - {step}')
        print()
        for note in plan['notes']:
            print(f'  - {note}')
