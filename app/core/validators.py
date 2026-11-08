"""Validate and normalize Tinyproxy filter rules."""
import re
import ipaddress
from urllib.parse import urlparse
from typing import Tuple, Optional


class WhitelistValidator:
    """Validate Tinyproxy filter rules, including POSIX ERE patterns."""
    
    # Toegestane karakters in domeinnamen (RFC 1035)
    DOMAIN_CHAR_PATTERN = re.compile(r'^[a-zA-Z0-9\-\.]+$')
    
    # Input length limits.
    MAX_DOMAIN_LENGTH = 255
    MAX_ENTRY_LENGTH = 1000
    MAX_COMMENT_LENGTH = 200
    
    # Gevaarlijke patronen die service kunnen verstoren
    DANGEROUS_PATTERNS = [
        r'\{[0-9]{4,}',  # Zeer grote repetities
    ]

    @staticmethod
    def _strip_regex_for_safety_checks(pattern: str) -> str:
        """Remove escaped chars and character classes to reduce false positives."""
        if not pattern:
            return ''
        out = []
        escaped = False
        in_class = False
        for ch in pattern:
            if escaped:
                escaped = False
                if not in_class:
                    out.append('x')
                continue
            if ch == '\\':
                escaped = True
                continue
            if in_class:
                if ch == ']':
                    in_class = False
                continue
            if ch == '[':
                in_class = True
                continue
            out.append(ch)
        return ''.join(out)
    
    @staticmethod
    def normalize_domain_input(domain: str) -> str:
        """Strip the scheme, path, and port and normalize the hostname."""
        if not isinstance(domain, str):
            return ""

        value = domain.strip()
        if not value:
            return value

        # Strip the URL scheme and path.
        if '://' in value:
            parsed = urlparse(value)
            host = parsed.netloc or parsed.path
        else:
            host = value.split('/', 1)[0]

        host = host.strip()

        # Strip credentials if present.
        if '@' in host:
            host = host.split('@', 1)[1]

        # Strip the port safely from an IPv4 address or hostname.
        # Preserve bracketed IPv6 here. Domain validation handles it later.
        if host.startswith('['):
            closing = host.find(']')
            if closing > 0:
                host = host[1:closing]
        elif ':' in host:
            before, sep, after = host.rpartition(':')
            if sep and after.isdigit():
                host = before

        # Strip surrounding dots and normalize the domain.
        host = host.strip().strip('.').lower()

        return host

    @staticmethod
    def validate_domain(domain: str) -> Tuple[bool, Optional[str]]:
        """Validate a domain name."""
        if not domain or not isinstance(domain, str):
            return False, 'Domain name cannot be empty'
        
        # Trim whitespace
        domain = domain.strip()
        
        # Lengte check
        if len(domain) > WhitelistValidator.MAX_DOMAIN_LENGTH:
            return False, f'Domain name is too long (maximum {WhitelistValidator.MAX_DOMAIN_LENGTH} characters).'
        
        if len(domain) < 2:
            return False, 'Domain name is too short (at least 2 characters)'
        
        # IPv4-adressen ook toelaten in domeinveld (bijv. node management IPs)
        try:
            ip = ipaddress.ip_address(domain.replace('*.', ''))
            if ip.version == 4:
                if '*' in domain:
                    return False, 'Wildcards are not allowed for IP addresses'
                return True, None
            return False, 'Only IPv4 addresses are allowed'
        except ValueError:
            pass

        # Basiskarakter validatie (voordat we regex escapen)
        # Allow letters, digits, dots, and hyphens.
        clean_domain = domain.replace('*', '')  # Wildcards zijn OK
        if not all(c.isalnum() or c in '.-' for c in clean_domain):
            return False, 'Domain name contains invalid characters (only a-z, 0-9, -, . and * are allowed)'
        
        # Reject consecutive dots.
        parts = domain.split('.')
        for part in parts:
            if not part:  # Lege parts door '..'
                return False, 'Domain name cannot contain consecutive dots'
            
            if part.startswith('-') or part.endswith('-'):
                return False, 'Domain labels cannot start or end with a hyphen'

        # Reject numeric-only TLDs so incomplete IPv4 addresses such as
        # 1.1, 10.0, or 192.168.1 cannot be treated as domains.
        # Geldige TLD's (.com, .nl, .org, ...) zijn altijd alfabetisch.
        tld_base = domain[2:] if domain.startswith('*.') else domain
        tld = tld_base.split('.')[-1]
        if tld.isdigit():
            return False, 'The top-level domain cannot contain only numbers (use .com, .nl or .org)'

        # Validate wildcard placement.
        if '*' in domain:
            # Allow the wildcard only at the beginning.
            if not domain.startswith('*.'):
                return False, 'A wildcard (*) is only allowed at the start as a subdomain (e.g. *.example.com)'
            
            # Allow a single wildcard.
            if domain.count('*') > 1:
                return False, 'At most one wildcard is allowed'

            base = domain[2:]
            base_parts = base.split('.')
            if len(base_parts) < 2:
                return False, 'A wildcard requires a base domain with a top-level domain (e.g. *.example.com)'

            for label in base_parts:
                if label.isdigit():
                    return False, 'Domain labels cannot contain only numbers'
        
        # Validate the top-level domain.
        if '.' not in domain.replace('*.', ''):
            return False, 'Domain name must contain at least one dot (e.g. example.com)'
        
        # Reject leading or trailing dots.
        if domain.startswith('.') or domain.endswith('.'):
            return False, 'Domain name cannot start or end with a dot'
        
        return True, None
    
    @staticmethod
    def domain_to_regex(domain: str) -> str:
        """Convert a domain name or wildcard to a Tinyproxy ERE pattern."""
        try:
            ip = ipaddress.ip_address(domain)
            if ip.version == 4:
                return r'^' + re.escape(domain) + r'(:[0-9]+)?$'
        except ValueError:
            pass

        # *.example.com matches the base domain and all subdomains.
        # example.com and host.example.com match only the specified host.
        if domain.startswith('*.'):
            base_domain = domain[2:]
            base_escaped = re.escape(base_domain)
            # The optional prefix matches all subdomains and the base domain.
            pattern = r'^(.+\.)?' + base_escaped + r'(:[0-9]+)?$'
        else:
            # Match only the exact hostname.
            escaped = re.escape(domain)
            pattern = r'^' + escaped + r'(:[0-9]+)?$'

        return pattern
    
    @staticmethod
    def validate_regex_pattern(pattern: str) -> Tuple[bool, Optional[str]]:
        """Validate a user-supplied regular expression."""
        if not pattern or not isinstance(pattern, str):
            return False, 'Pattern cannot be empty'
        
        # Lengte check
        if len(pattern) > WhitelistValidator.MAX_ENTRY_LENGTH:
            return False, f'Pattern is too long (maximum {WhitelistValidator.MAX_ENTRY_LENGTH} characters).'

        # Tinyproxy uses ERE, which does not support noncapturing groups or lookarounds.
        pcre_only_tokens = ['(?:', '(?=', '(?!', '(?<=', '(?<!']
        if any(token in pattern for token in pcre_only_tokens):
            return False, 'Pattern contains PCRE syntax (e.g. (?:...)) unsupported by Tinyproxy ERE'
        
        # Reject unsafe patterns.
        for dangerous in WhitelistValidator.DANGEROUS_PATTERNS:
            if re.search(dangerous, pattern):
                return False, f'Pattern contains a potentially unsafe expression: {dangerous}'

        # Voorkom geneste wildcard-repetitie (kan backtracking explosies veroorzaken)
        stripped = WhitelistValidator._strip_regex_for_safety_checks(pattern)
        if re.search(r'\([^)]*(\.\*|\.\+)[^)]*\)\s*[*+{]', stripped):
            return False, 'Pattern contains nested wildcard repetition (e.g. (.*)+), which is too expensive'
        
        # Compile the pattern to check its syntax.
        try:
            re.compile(pattern)
        except re.error as e:
            return False, f'Invalid regular expression: {str(e)}'
        
        # Reject patterns that match every destination.
        if pattern in [r'.*', r'.+', r'^.*$', r'^.+$']:
            return False, 'The pattern is too broad and would allow every domain.'
        
        return True, None
    
    @staticmethod
    def validate_comment(comment: str) -> Tuple[bool, Optional[str]]:
        """Validate a filter comment."""
        if not isinstance(comment, str):
            return False, 'Comment must be a string'
        
        # Lengte check
        if len(comment) > WhitelistValidator.MAX_COMMENT_LENGTH:
            return False, f'Comment is too long (maximum {WhitelistValidator.MAX_COMMENT_LENGTH} characters).'
        
        # Reject line breaks in comments.
        if '\n' in comment or '\r' in comment:
            return False, 'Comment cannot contain newlines'
        
        # Reject control characters.
        if any(ord(c) < 32 and c not in '\t' for c in comment):
            return False, 'The comment contains invalid control characters.'
        
        return True, None
    
    @staticmethod
    def validate_whitelist_entry(entry: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """Validate a filter rule and determine its type."""
        if not isinstance(entry, str):
            return False, 'Rule must be a string', None
        
        # Trim whitespace
        entry = entry.strip()
        
        # Lege regel
        if not entry:
            return True, None, 'empty'
        
        # Commentaar
        if entry.startswith('#'):
            comment = entry[1:].strip()
            is_valid, error = WhitelistValidator.validate_comment(comment)
            return is_valid, error, 'comment'
        
        # Normalize and validate as a domain first.
        normalized = WhitelistValidator.normalize_domain_input(entry)
        is_valid_domain, domain_error = WhitelistValidator.validate_domain(normalized)
        if is_valid_domain:
            return True, None, 'domain'

        # For domain-like input, including wildcards, return the domain error
        # so the user sees how to correct it.
        domain_like_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-*')
        if all(c in domain_like_chars for c in entry):
            return False, domain_error, 'domain'

        # Try regex validation only for input that looks like a pattern.
        looks_like_regex = (
            entry.startswith('^')
            or entry.endswith('$')
            or entry.startswith('(')
            or any(c in entry for c in ['|', '\\', '[', ']', '{', '}', '+', '?', '*'])
        )
        if looks_like_regex:
            is_valid_regex, regex_error = WhitelistValidator.validate_regex_pattern(entry)
            if is_valid_regex:
                return True, None, 'regex'
            return False, regex_error, 'regex'

        return False, domain_error, 'domain'
    
    @staticmethod
    def sanitize_input(text: str) -> str:
        """
        Sanitize user input before writing it to a filter file.
        
        Args:
            text: Input to sanitize.
            
        Returns:
            Gesanitized tekst
        """
        if not isinstance(text, str):
            return ""
        
        # Strip control characters and DEL (127).
        # Preserve tabs and newlines for filter files.
        sanitized = ''.join(c for c in text if ((ord(c) >= 32 and ord(c) != 127) or c in '\t\n\r'))
        
        # Normaliseer newlines
        sanitized = sanitized.replace('\r\n', '\n').replace('\r', '\n')
        
        return sanitized
