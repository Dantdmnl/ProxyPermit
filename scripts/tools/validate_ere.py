#!/usr/bin/env python3
"""
Validate ERE whitelist patterns against example hosts using the
shared `WhitelistValidator` where possible.

Usage:
  python scripts/tools/validate_ere.py --whitelist <path> --hosts host1 host2 ...

This script is a developer convenience: it will reuse the production
`WhitelistValidator` from `app.core.validators` when the repository root is
available on `sys.path`. When run as a standalone script it will still
fall back to basic regex matching.
"""
import argparse
import os
import re
import sys
from typing import List

# Ensure the repository root is importable so `app.core.validators` can be loaded
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    from app.core.validators import WhitelistValidator
except Exception:
    WhitelistValidator = None


def load_patterns(path: str) -> List[str]:
    patterns = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for raw in f:
                line = raw.rstrip('\n\r')
                if not line or not line.strip():
                    continue
                stripped = line.strip()

                # Preserve full-line comments so users can test them too
                if stripped.startswith('#'):
                    patterns.append(stripped)
                    continue

                # Inline description handling: split on '  #' or '\t#'
                pattern = stripped
                if '  #' in pattern or '\t#' in pattern:
                    parts = pattern.split('#', 1)
                    pattern = parts[0].rstrip()

                patterns.append(pattern)
    except Exception as e:
        print(f"Fout bij het lezen van whitelist: {e}", file=sys.stderr)
        sys.exit(2)
    return patterns


def test_patterns(patterns: List[str], hosts: List[str]) -> None:
    """Test each pattern against provided hosts.

    When `WhitelistValidator` is available, use its normalization and safety
    checks (preferred). Otherwise fallback to direct regex compilation.
    """
    for idx, pat in enumerate(patterns):
        print(f"[{idx}] Entry: {pat}")

        if pat.startswith('#'):
            print('    COMMENT')
            continue

        # If we can, use the shared validator logic for consistent behavior
        if WhitelistValidator:
            is_valid, error, entry_type = WhitelistValidator.validate_whitelist_entry(pat)
            if not is_valid:
                print(f"    INVALID: {error}")
                continue

            if entry_type == 'domain':
                normalized = WhitelistValidator.normalize_domain_input(pat)
                regex = WhitelistValidator.domain_to_regex(normalized)
                try:
                    compiled = re.compile(regex)
                except re.error as e:
                    print(f"    ERROR compiling generated regex: {e}")
                    continue
                for host in hosts:
                    matched = bool(compiled.fullmatch(host))
                    print(f"    Host: {host} -> {'MATCH' if matched else 'no match'}")

            elif entry_type == 'regex':
                ok, err = WhitelistValidator.validate_regex_pattern(pat)
                if not ok:
                    print(f"    INVALID REGEX: {err}")
                    continue
                try:
                    compiled = re.compile(pat)
                except re.error as e:
                    print(f"    ERROR compiling regex: {e}")
                    continue
                for host in hosts:
                    matched = bool(compiled.fullmatch(host))
                    print(f"    Host: {host} -> {'MATCH' if matched else 'no match'}")

            elif entry_type == 'comment':
                print('    COMMENT')
            else:
                print('    (skipped)')

        else:
            # Fallback: treat the entry as a raw regex and test
            try:
                compiled = re.compile(pat)
            except re.error as e:
                print(f"    ERROR: Ongeldig regex pattern: {e}")
                continue
            for host in hosts:
                m = compiled.fullmatch(host)
                print(f"    Host: {host} -> {'MATCH' if m else 'no match'}")


def main():
    parser = argparse.ArgumentParser(description='Validate ERE whitelist patterns against hosts')
    parser.add_argument('--whitelist', '-w', required=True, help='Path to whitelist file')
    parser.add_argument('--hosts', '-H', nargs='+', required=True, help='Hosts to test (e.g. example.com sub.example.com:8443)')

    args = parser.parse_args()

    if WhitelistValidator:
        print('Using shared WhitelistValidator from app.core.validators')
    else:
        print('Warning: app.core.validators not importable; falling back to basic regex testing')

    patterns = load_patterns(args.whitelist)
    if not patterns:
        print('Geen patronen gevonden in whitelist')
        sys.exit(1)

    test_patterns(patterns, args.hosts)


if __name__ == '__main__':
    main()
