"""Manage Tinyproxy filter files with validation, backups, and atomic writes."""
import os
import re
import shutil
import subprocess
import logging
import ipaddress
import tempfile
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path
from functools import wraps

from .validators import WhitelistValidator
from .files import file_lock


logger = logging.getLogger(__name__)


def serialized_change(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with file_lock(str(self.whitelist_path) + '.lock'):
            return method(self, *args, **kwargs)
    return locked


class WhitelistManager:
    """Read and update Tinyproxy filter files."""
    
    def __init__(self, whitelist_path: str, service_name: str = 'tinyproxy'):
        """Set the filter path and the Tinyproxy systemd service name."""
        self.whitelist_path = Path(whitelist_path)
        self.service_name = service_name
        self.validator = WhitelistValidator()
        self.entry_log_path = Path(
            os.getenv('WHITELIST_ENTRY_LOG_PATH', str(self.whitelist_path.parent / 'whitelist_entry_log.json'))
        )
        
        # Backup directory
        self.backup_dir = self.whitelist_path.parent / '.backups'
        # Create the backup directory with restricted permissions on Unix.
        import sys
        if sys.platform == 'win32':
            self.backup_dir.mkdir(exist_ok=True)
        else:
            self.backup_dir.mkdir(exist_ok=True, mode=0o750)
        
        logger.info(f"WhitelistManager geïnitialiseerd: {self.whitelist_path}")
    
    def _create_backup(self) -> Optional[Path]:
        """Back up the current filter file at most once per minute."""
        if not self.whitelist_path.exists():
            logger.warning(f'Filter file does not exist: {self.whitelist_path}')
            return None
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = self.backup_dir / f'whitelist_{timestamp}.txt.bak'
        
        # Skip duplicate backups within the same minute.
        timestamp_minute = datetime.now().strftime('%Y%m%d_%H%M')
        existing_backups = list(self.backup_dir.glob(f'whitelist_{timestamp_minute}*.txt.bak'))
        
        if existing_backups:
            logger.debug('Skipping backup because one already exists for this minute')
            return existing_backups[0]
        
        try:
            shutil.copy2(self.whitelist_path, backup_path)
            logger.info(f'Backup created: {backup_path}')
            
            # Keep the ten most recent backups.
            self._cleanup_old_backups(keep=10)
            
            return backup_path
        except Exception as e:
            logger.error(f'Could not create backup: {e}')
            raise
    
    def _cleanup_old_backups(self, keep: int = 10):
        """Keep only the most recent backups."""
        try:
            backups = sorted(self.backup_dir.glob('whitelist_*.txt.bak'))
            if len(backups) > keep:
                for old_backup in backups[:-keep]:
                    old_backup.unlink()
                    logger.debug(f'Removed old backup: {old_backup}')
        except Exception as e:
            logger.warning(f'Could not prune backups: {e}')
    
    def read_whitelist(self) -> List[Dict[str, Any]]:
        """Read and parse the filter file."""
        entries = []
        
        if not self.whitelist_path.exists():
            logger.warning(f'Filter file not found: {self.whitelist_path}')
            return entries
        
        try:
            with open(self.whitelist_path, 'r', encoding='utf-8-sig') as f:
                lines = f.readlines()
            
            # Log counts without recording user data.
            logger.info(f'Read {len(lines)} lines from the filter file')
            
            for idx, line in enumerate(lines):
                line = line.rstrip('\n\r')

                # Hide blank lines in the interface.
                if not line.strip():
                    continue

                # Standalone comment entry (organisatorische comment)
                if line.strip().startswith('#'):
                    comment_text = line.strip()[1:].strip()
                    entries.append({
                        'id': len(entries),
                        'type': 'comment',
                        'content': comment_text,
                        'display': f"# {comment_text}",
                        'description': None,
                        'domain_hint': None,
                        'original': line
                    })
                    continue

                # Look for an inline description after a domain or pattern.
                pattern_line = line.strip()
                description = None
                # Split on inline description marker: space+#, tab+#, or double-space+#
                import re as _re
                if _re.search(r'\s+#', pattern_line):
                    parts = pattern_line.split('#', 1)
                    if len(parts) == 2:
                        pattern_line = parts[0].strip()
                        description = parts[1].strip()
                # Determine type: regex or domain
                is_valid, _, entry_type = self.validator.validate_whitelist_entry(pattern_line)
                if not is_valid or entry_type not in ('regex', 'domain'):
                    entry_type = 'regex'  # fallback
                # For UI: always extract the domain or IP for the edit field
                domain_for_edit = None
                if entry_type == 'domain':
                    domain_for_edit = pattern_line
                elif entry_type == 'regex':
                    # Try to extract domain or IP from regex
                    domain_for_edit = self._extract_domain_from_regex(pattern_line)
                    # If the regex is a domain regex, convert it back to the domain string
                    # Keep the extracted domain hint verbatim (including leading wildcard)
                    # so the UI can show and preserve wildcards like "*.example.com".
                    # domain_for_edit may be None or a domain/IP string.
                entries.append({
                    'id': len(entries),
                    'type': entry_type,
                    'content': pattern_line,
                    'display': pattern_line,
                    'description': description,
                    'domain_hint': domain_for_edit,
                    'original': line
                })
            
            return entries
            
        except Exception as e:
            logger.error(f'Could not read filter file: {e}')
            raise
    
    def _extract_domain_from_regex(self, pattern: str) -> Optional[str]:
        """Extract a domain from a regular expression for display."""
        import re
        try:
            s = pattern.strip()
            # Remove anchors
            s = s.lstrip('^').rstrip('$')
            # Detect if the regex contains/starts with common subdomain/group tokens
            # that imply a wildcard/subdomain match (e.g. '(^|\\.)', '^(.+\\.)').
            wildcard = False
            try:
                if ('(^|\\.)' in pattern or '(^|.)' in pattern
                        or pattern.startswith('^(.+\\.)') or pattern.startswith('^(.+.')
                        or pattern.startswith('(.+\\.)') or pattern.startswith('(.+.')
                        or pattern.startswith('^([A-Za-z0-9-]+\\.)*')
                        or pattern.startswith('^([A-Za-z0-9-]+\\.)+')):
                    wildcard = True
            except Exception:
                wildcard = False

            # Unescape common escaped characters used in generated patterns.
            s = s.replace('\\.', '.')
            s = s.replace('\\-', '-')
            # Remove optional port suffix patterns
            s = s.replace('(:[0-9]+)?', '')
            # Remove common leading wildcard/group tokens like '(^|\.)' or '(.+\.)' so we get the base domain
            s = re.sub(r'^\(\^\|\\\\\.\)', '', s)
            s = re.sub(r'^\(\.\+\\\\\.\)', '', s)
            s = re.sub(r'^\(\.\+\.\\\\\.\)', '', s)
            s = re.sub(r'^\(\.\+\)\.', '', s)
            s = re.sub(r'^\(\?:\^\|\\\\\.\)', '', s)
            s = re.sub(r'^\(\.\+\)\.', '', s)
            s = s.strip()
            # Now try to find an IP
            ip_m = re.search(r'([0-9]{1,3}(?:\.[0-9]{1,3}){3})', s)
            if ip_m:
                return ip_m.group(1)
            # Try to find domain-like substring
            dom_m = re.search(r'([A-Za-z0-9][A-Za-z0-9\-\*]*(?:\.[A-Za-z0-9\-\*]+)+)', s)
            if dom_m:
                dom = dom_m.group(1)
                # If we detected a leading subdomain/group pattern, present the hint as a wildcard
                # e.g. return "*.example.com" for patterns matching subdomains
                if wildcard and not dom.startswith('*.'):
                    # Don't prefix IPs
                    if not re.match(r'^\d{1,3}(?:\.\d{1,3}){3}$', dom):
                        dom = '*.' + dom
                return dom
            # Fallback cleanup: remove leftover regex tokens
            domain = re.sub(r'[\^$\\()\[\]\+\*\?\|]', '', pattern)
            domain = domain.replace('(:[0-9]+)?', '')
            domain = domain.strip()
            if domain and '.' in domain and len(domain) < 100:
                # remove escaped characters for readable domain hints
                return domain.replace('\\.', '.').replace('\\-', '-')
        except Exception:
            pass
        return None

    def regex_to_export_entry(self, pattern: str) -> str:
        """Render a regex as a readable export rule where possible."""
        pattern = pattern.strip()
        if not pattern:
            return pattern

        domain_hint = self._extract_domain_from_regex(pattern)
        if domain_hint:
            normalized = self.validator.normalize_domain_input(domain_hint)
            if normalized:
                try:
                    ip = ipaddress.ip_address(normalized.replace('*.', ''))
                    if ip.version == 4:
                        return self.validator.domain_to_regex(normalized)
                except ValueError:
                    canonical_domain_regex = self.validator.domain_to_regex(normalized)
                    # Also accept older wildcard formats that may exist in existing whitelists.
                    old_formats = []
                    if normalized and normalized.startswith('*.'):
                        import re as _re
                        base_esc = _re.escape(normalized[2:])
                        # Old Python ^([A-Za-z0-9-]+\.)* and transitional ^(.+\.) (without ?)
                        old_formats.append(r'^([A-Za-z0-9-]+\.)*' + base_esc + r'(:[0-9]+)?$')
                        old_formats.append(r'^(.+\.)' + base_esc + r'(:[0-9]+)?$')
                    if pattern == canonical_domain_regex or pattern in old_formats:
                        return domain_hint

        return pattern

    def render_export_content(self, prefer_readable: bool = False) -> str:
        """Export readable domain rules or the raw filter file."""
        if not self.whitelist_path.exists():
            return ''

        export_lines = []
        with open(self.whitelist_path, 'r', encoding='utf-8') as handle:
            for raw_line in handle:
                line = raw_line.rstrip('\n\r')
                stripped = line.strip()

                if not stripped:
                    export_lines.append('')
                    continue

                if stripped.startswith('#'):
                    export_lines.append(stripped)
                    continue

                description = None
                pattern = stripped
                if re.search(r'\s+#', pattern):
                    parts = pattern.split('#', 1)
                    if len(parts) == 2:
                        pattern = parts[0].strip()
                        description = parts[1].strip()

                if prefer_readable:
                    export_entry = self.regex_to_export_entry(pattern)
                else:
                    # Return the raw stored pattern (preserve original regex)
                    export_entry = pattern

                if description:
                    export_lines.append(f"{export_entry} # {description}")
                else:
                    export_lines.append(export_entry)

        return '\n'.join(export_lines) + '\n'
    
    @serialized_change
    def write_whitelist(self, entries: List[str], create_backup: bool = True) -> Tuple[bool, Optional[str]]:
        """Write filter rules atomically, optionally making a backup first."""
        # Validate all rules before writing.
        validated_entries = []
        for idx, entry in enumerate(entries):
            # Sanitize input
            entry = self.validator.sanitize_input(entry)
            
            # Validate the sanitized rule.
            is_valid, error, entry_type = self.validator.validate_whitelist_entry(entry)
            if not is_valid:
                return False, f'Invalid rule on line {idx + 1}: {error}'
            
            validated_entries.append(entry)
        
        # Back up the current file if requested.
        backup_path = None
        if create_backup:
            try:
                backup_path = self._create_backup()
            except Exception as e:
                return False, f'Could not create backup: {str(e)}'
        
        # Write through a temporary file.
        temp_path = None
        
        try:
            self.whitelist_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f'.{self.whitelist_path.name}.',
                suffix='.tmp',
                dir=str(self.whitelist_path.parent),
                text=True,
            )
            temp_path = Path(temp_name)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                for entry in validated_entries:
                    f.write(entry + '\n')
                f.flush()
                os.fsync(f.fileno())
            
            # Atomische rename (overschrijf origineel)
            os.replace(temp_path, self.whitelist_path)
            
            # Keep the file readable by Tinyproxy.
            os.chmod(self.whitelist_path, 0o644)
            
            logger.info(f'Wrote {len(validated_entries)} rules to the filter file')
            return True, None
            
        except Exception as e:
            logger.error(f'Could not write filter file: {e}')
            
            # Try to restore the backup.
            if backup_path and backup_path.exists():
                try:
                    shutil.copy2(backup_path, self.whitelist_path)
                    logger.info("Whitelist hersteld vanuit backup")
                except Exception as restore_err:
                    logger.critical(f'Could not restore backup: {restore_err}')
            
            return False, f'Write failed: {str(e)}'
        finally:
            # Remove the temporary file if it remains.
            if temp_path and temp_path.exists():
                temp_path.unlink()
    
    @serialized_change
    def add_entry(self, domain_or_pattern: str, is_comment: bool = False, description: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """Add a domain, regular expression, or comment to the filter file."""
        # Sanitize
        entry = self.validator.sanitize_input(domain_or_pattern).strip()
        
        # Sanitize description
        if description:
            description = self.validator.sanitize_input(description).strip()
            # Remove a leading comment marker if one was included.
            if description.startswith('#'):
                description = description[1:].strip()
        
        if is_comment:
            entry = f"# {entry}"
            is_valid, error, _ = self.validator.validate_whitelist_entry(entry)
        else:
            # Determine whether the input is a domain or a regular expression.
            is_valid, error, entry_type = self.validator.validate_whitelist_entry(entry)
            
            if is_valid and entry_type == 'domain':
                # Normaliseer domein input (strip schema/pad/poort)
                normalized = self.validator.normalize_domain_input(entry)
                if normalized:
                    entry = normalized
                
                # Hervalideer na normalisatie
                is_valid, error = self.validator.validate_domain(entry)
                if is_valid:
                    # Convert the domain to a regular expression.
                    entry = self.validator.domain_to_regex(entry)
                    # Validate the generated pattern.
                    is_valid, error = self.validator.validate_regex_pattern(entry)
        
        if not is_valid:
            return False, error
        
        # Append the description as an inline comment.
        if description and not is_comment:
            entry = f"{entry}  # {description}"
        
        # Lees huidige entries
        current_entries = self.read_whitelist()
        
        # Check non-comment rules for duplicates.
        if not is_comment:
            entry_without_desc = entry.split('#')[0].strip()
            for existing in current_entries:
                if existing['type'] in ['regex', 'domain']:
                    existing_without_desc = existing['content']
                    if existing_without_desc == entry_without_desc:
                        return False, 'This rule is already in the filter list.'
        
        # Add the rule.
        new_entries = [e['original'] for e in current_entries]
        new_entries.append(entry)

        # Record the addition for conflict resolution.
        try:
            import hashlib, json, socket
            log_path = self.entry_log_path
            entry_hash = hashlib.sha256(entry.encode('utf-8')).hexdigest()
            log_entry = {
                'timestamp': datetime.now().isoformat(),
                'entry_hash': entry_hash,
                'entry': entry,
                'node': socket.gethostname()
            }
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # Append to log file
            logs = []
            try:
                with open(log_path, 'r') as f:
                    logs = json.load(f)
            except Exception:
                pass
            logs.append(log_entry)
            with open(log_path, 'w') as f:
                json.dump(logs, f, indent=2)
        except Exception as log_exc:
            logger.warning(f"Entry loggen faalde: {log_exc}")

        return self.write_whitelist(new_entries)
    
    @serialized_change
    def delete_entry(self, entry_id: int) -> Tuple[bool, Optional[str]]:
        """Remove a rule by its line ID."""
        current_entries = self.read_whitelist()
        
        if entry_id < 0 or entry_id >= len(current_entries):
            return False, 'Invalid rule ID.'
        
        # Remove the rule.
        new_entries = [e['original'] for idx, e in enumerate(current_entries) if idx != entry_id]
        
        return self.write_whitelist(new_entries)
    
    @serialized_change
    def update_entry(self, entry_id: int, new_content: str, description: Optional[str] = None, is_comment: bool = False, domain_field: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """Validate and update an existing filter rule."""
        # Sanitize input
        new_content = self.validator.sanitize_input(new_content).strip()
        if description:
            description = self.validator.sanitize_input(description).strip()
            if description.startswith('#'):
                description = description[1:].strip()
        if domain_field:
            domain_field = self.validator.sanitize_input(domain_field).strip()

        # Read the current rule for comparison.
        current_entries = self.read_whitelist()
        if entry_id < 0 or entry_id >= len(current_entries):
            return False, 'Invalid rule ID.'
        old_entry = current_entries[entry_id]['original'].strip()

        # Skip the write when the rule has not changed.
        candidate = new_content
        if description and not is_comment:
            candidate = f"{new_content}  # {description}"
        if candidate == old_entry:
            return True, None

        if is_comment:
            comment = new_content.lstrip('#').strip()
            is_valid, error = self.validator.validate_comment(comment)
            if not is_valid:
                return False, error
            new_line = f"# {comment}"
        else:
            # Determine whether the input is a domain or a regular expression.
            is_valid, error, entry_type = self.validator.validate_whitelist_entry(new_content)
            if not is_valid:
                return False, error
            if entry_type == 'domain':
                normalized = self.validator.normalize_domain_input(new_content)
                if normalized:
                    new_content = normalized
                is_valid, error = self.validator.validate_domain(new_content)
                if not is_valid:
                    return False, error
                regex = self.validator.domain_to_regex(new_content)
                is_valid, error = self.validator.validate_regex_pattern(regex)
                if not is_valid:
                    return False, error
                # Accept patterns that match the domain in the same way as the UI.
                if domain_field:
                    expected_regex = self.validator.domain_to_regex(domain_field)
                    def regex_to_domain_backend(regex):
                        import re
                        if not regex:
                            return ''
                        # IPv4: ^1\.2\.3\.4$
                        m = re.match(r'^\^([0-9]{1,3}(?:\\.[0-9]{1,3}){3})\$$', regex)
                        if m:
                            return m.group(1).replace('\\.', '.')
                        # IPv6: ^\[....\]$
                        m = re.match(r'^\^\\\[([0-9a-fA-F:]+)\\\]\$$', regex)
                        if m:
                            return m.group(1)
                        # Domain style produced by domainToRegex: '(^|\\.)' + escapedDomain + '(:[0-9]+)?$'
                        if regex.startswith('(^|\\.)') or regex.startswith('(^|.)'):
                            inner = re.sub(r'^\(\^\|\\\.\)', '', regex)
                            suffix = '(:[0-9]+)?$'
                            if inner.endswith(suffix):
                                inner = inner[:-len(suffix)]
                            inner = inner.replace('\\.', '.').replace('.*', '*')
                            return inner
                        # Patterns like '^(.+\.)example\.com(:[0-9]+)?$'
                        if regex.startswith('^(.+\\.)') or regex.startswith('^(.+.)'):
                            inner = re.sub(r'^\^\(\.\+\\\.\)', '', regex)
                            inner = re.sub(r'^\^\(\.\+\.\)', '', inner)
                            suffix = '(:[0-9]+)?$'
                            if inner.endswith(suffix):
                                inner = inner[:-len(suffix)]
                            inner = inner.replace('\\.', '.').replace('.*', '*')
                            return re.sub(r'^\*\.', '', inner)
                        return ''
                    domain_norm = self.validator.normalize_domain_input(domain_field)
                    regex_domain = regex_to_domain_backend(regex)
                    if regex_domain and regex_domain == domain_norm:
                        pass  # Semantisch akkoord
                    else:
                        def norm(s):
                            return s.replace(' ', '').replace('\r', '').replace('\n', '')
                        if norm(regex) != norm(expected_regex):
                            return False, f'Regex does not match the domain: expected {expected_regex}, got {regex}'
                new_line = regex
            elif entry_type == 'regex':
                is_valid, error = self.validator.validate_regex_pattern(new_content)
                if not is_valid:
                    return False, error
                # Accept patterns that match the domain in the same way as the UI.
                if domain_field:
                    expected_regex = self.validator.domain_to_regex(domain_field)
                    def regex_to_domain_backend(regex):
                        import re
                        if not regex:
                            return ''
                        m = re.match(r'^\^([0-9]{1,3}(?:\\.[0-9]{1,3}){3})\$$', regex)
                        if m:
                            return m.group(1).replace('\\.', '.')
                        m = re.match(r'^\^\\\[([0-9a-fA-F:]+)\\\]\$$', regex)
                        if m:
                            return m.group(1)
                        if regex.startswith('(^|\\.)') or regex.startswith('(^|.)'):
                            inner = re.sub(r'^\(\^\|\\\.\)', '', regex)
                            suffix = '(:[0-9]+)?$'
                            if inner.endswith(suffix):
                                inner = inner[:-len(suffix)]
                            inner = inner.replace('\\.', '.').replace('.*', '*')
                            return inner
                        if regex.startswith('^(.+\\.)') or regex.startswith('^(.+.)'):
                            inner = re.sub(r'^\^\(\.\+\\\.\)', '', regex)
                            inner = re.sub(r'^\^\(\.\+\.\)', '', inner)
                            suffix = '(:[0-9]+)?$'
                            if inner.endswith(suffix):
                                inner = inner[:-len(suffix)]
                            inner = inner.replace('\\.', '.').replace('.*', '*')
                            return re.sub(r'^\*\.', '', inner)
                        return ''
                    domain_norm = self.validator.normalize_domain_input(domain_field)
                    regex_domain = regex_to_domain_backend(new_content)
                    if regex_domain and regex_domain == domain_norm:
                        pass  # Semantisch akkoord
                    else:
                        def norm(s):
                            return s.replace(' ', '').replace('\r', '').replace('\n', '')
                        if norm(new_content) != norm(expected_regex):
                            return False, f'Regex does not match the domain: expected {expected_regex}, got {new_content}'
                new_line = new_content
            else:
                return False, 'Invalid rule type.'
            if description:
                new_line = f"{new_line}  # {description}"

        # Update entry in whitelist
        new_entries = []
        for idx, e in enumerate(current_entries):
            if idx == entry_id:
                new_entries.append(new_line)
            else:
                new_entries.append(e['original'])
        return self.write_whitelist(new_entries)
    
    @serialized_change
    def reorder_entries(self, entry_ids: List[int]) -> Tuple[bool, Optional[str]]:
        """Reorder filter rules without creating a backup."""
        current_entries = self.read_whitelist()
        
        # Require every ID to be valid and unique.
        if len(entry_ids) != len(current_entries):
            return False, 'The number of IDs must match the number of rules.'
        
        if len(set(entry_ids)) != len(entry_ids):
            return False, 'Duplicate rule IDs found.'
        
        for entry_id in entry_ids:
            if entry_id < 0 or entry_id >= len(current_entries):
                return False, f'Invalid rule ID: {entry_id}'
        
        # Herorden entries
        new_entries = [current_entries[entry_id]['original'] for entry_id in entry_ids]
        
        # Reordering does not require a backup.
        return self.write_whitelist(new_entries, create_backup=False)
    
    def restart_tinyproxy(self) -> Tuple[bool, Optional[str]]:
        """Restart the Tinyproxy service."""
        # Mock the Tinyproxy service on Windows.
        import sys
        if sys.platform == 'win32':
            logger.info("[MOCK] Service restart geskipped op Windows")
            return True, "Service restart mocked (Windows)"
        
        try:
            # Restart the service.
            prefix = ['sudo', '-n'] if os.getenv('SERVICE_CONTROL_SUDO', 'false').lower() == 'true' else []
            result = subprocess.run(
                prefix + ['/usr/bin/systemctl', 'restart', self.service_name],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                logger.error(f"Tinyproxy restart gefaald: {result.stderr}")
                return False, f"Service restart gefaald: {result.stderr}"
            
            # Check status
            result = subprocess.run(
                ['systemctl', 'is-active', self.service_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.stdout.strip() != 'active':
                logger.error('Tinyproxy is inactive after restart')
                return False, 'The service is inactive after restart.'
            
            logger.info('Tinyproxy restarted successfully')
            return True, None
            
        except subprocess.TimeoutExpired:
            return False, 'The command timed out.'
        except FileNotFoundError:
            return False, 'systemctl or Tinyproxy was not found.'
        except Exception as e:
            logger.error(f'Could not restart Tinyproxy: {e}')
            return False, f'Unexpected error: {str(e)}'
    
    def get_service_status(self) -> Dict[str, Any]:
        """Get the Tinyproxy service status."""
        # Windows mock
        import sys
        if sys.platform == 'win32':
            return {
                'running': True,
                'status': 'mocked (Windows)',
                'uptime': 'N/A',
                'error': None
            }
        
        status = {
            'running': False,
            'status': 'unknown',
            'uptime': None,
            'error': None
        }
        
        try:
            # Check whether the service is active.
            result = subprocess.run(
                ['systemctl', 'is-active', self.service_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            status['status'] = result.stdout.strip()
            status['running'] = (status['status'] == 'active')
            
            # Fetch additional details for a running service.
            if status['running']:
                result = subprocess.run(
                    ['systemctl', 'show', self.service_name, '--property=ActiveEnterTimestamp'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                if result.returncode == 0:
                    status['uptime'] = result.stdout.strip()
            
        except Exception as e:
            status['error'] = str(e)
            logger.warning(f'Could not read service status: {e}')
        
        return status
    
    @serialized_change
    def replace_all_entries(self, entries: List[str]) -> Tuple[bool, Optional[str]]:
        """Replace the filter rules and back up the previous file."""
        return self.write_whitelist(entries, create_backup=True)
    
    @serialized_change
    def append_entries(self, entries: List[str]) -> Tuple[bool, Optional[str]]:
        """Append filter rules and back up the previous file."""
        current_entries = [entry['original'] for entry in self.read_whitelist()]
        return self.write_whitelist(current_entries + entries, create_backup=True)
