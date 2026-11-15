"""Validation of native Tinyproxy filter files without a web dependency."""
import os


def native_patterns(content):
    """Mirror Tinyproxy's first-token/comment parsing rather than Python regex parsing."""
    patterns = []
    for line in content.split('\n'):
        if len(line.encode('utf-8')) > 510:
            raise ValueError('Filter lines must not exceed 510 bytes; Tinyproxy splits longer lines.')
        line = line.lstrip(' \t\r\v\f')
        end = len(line)
        for index, character in enumerate(line):
            if character in ' \t\r\v\f' or (character == '#' and (index == 0 or line[index - 1] != '\\')):
                end = index
                break
        if end:
            patterns.append(line[:end])
    return patterns


def validate_filter(content, pattern_type):
    """Check native regex on Linux using the same POSIX family; never log rules."""
    import subprocess
    import tempfile
    if not isinstance(content, str) or len(content.encode('utf-8')) > 1024 * 1024 or '\x00' in content:
        return dict(valid=False, errors=['The filter must be text without NUL characters and no larger than 1 MiB.'], warnings=[])
    try:
        rules = native_patterns(content)
    except ValueError as exc:
        return dict(valid=False, errors=[str(exc)], warnings=[])
    errors, warnings = [], []
    if pattern_type not in ('ere', 'bre', 'fnmatch'):
        errors.append('Unknown filter type.')
    elif pattern_type != 'fnmatch' and os.name != 'nt':
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8') as patterns:
                patterns.write('\n'.join(rules))
                patterns.flush()
                result = subprocess.run(['grep', '-E' if pattern_type == 'ere' else '-G', '-f', patterns.name],
                                        input='', text=True, capture_output=True, timeout=5, env={**os.environ, 'LC_ALL': 'C'})
            if result.returncode not in (0, 1):
                errors.append('One or more filter rules contain an invalid POSIX pattern.')
        except (OSError, subprocess.TimeoutExpired):
            errors.append('POSIX validation is unavailable on this server.')
    elif pattern_type != 'fnmatch':
        warnings.append('POSIX regex validation is only available on Linux; check before activation.')
    return dict(valid=not errors, errors=errors, warnings=warnings)
