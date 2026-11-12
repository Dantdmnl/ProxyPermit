"""Explicit permissions for browser endpoints; machine sync keeps its own auth."""
from flask import request
from ..core.tinyproxy import ConfigError, parse

POLICY_DIRECTIVES = frozenset('timeout maxclients allow deny basicauth connectport filterurls '
                              'filtercasesensitive filterdefaultdeny filterextended filtertype upstream '
                              'addheader anonymous viaproxyname xtinyproxy disableviaheader reversepath '
                              'reverseonly reversemagic reversebaseurl loglevel'.split())


class PolicyPermissionError(ConfigError):
    pass


def ensure_policy_only(previous, proposed):
    """Proxy managers cannot change service identity, bindings or filesystem paths."""
    def host_settings(content):
        return [(row['name'].lower(), row['args']) for row in parse(content)
                if row['name'].lower() not in POLICY_DIRECTIVES]
    if host_settings(previous) != host_settings(proposed):
        raise PolicyPermissionError('Only a system administrator can change service identity, listener settings or file paths.')

READ = frozenset({'api_config', 'get_logs', 'get_entries', 'get_status', 'service_status',
                  'sync_peer_status', 'run_sync_status', 'settings.ha_setup_status'})
PROXY = frozenset({'merge_remote_preview', 'sync_merge_data', 'sync_merge_apply', 'run_sync_now',
                   'add_entry', 'update_entry', 'delete_entry', 'reorder_entries', 'validate_entry',
                   'restart_service', 'domain_to_regex', 'import_whitelist', 'export_whitelist',
                   'proxy.read_config', 'proxy.validate_config', 'proxy.save_config',
                   'proxy.discard_draft', 'proxy.native_filter'})
ADMIN = frozenset({'auth_force_logout', 'settings.ha_network', 'settings.preview_ha',
                   'settings.apply_ha_setup', 'settings.retire_ha', 'proxy.ha_policy'})
SELF = frozenset({'auth_logout', 'auth_transfer', 'settings.change'})


def required_permission():
    endpoint = request.endpoint
    if endpoint == 'proxy.manage_replication':
        return 'read' if (request.view_args or {}).get('action') == 'status' else 'admin'
    for permission, endpoints in (('read', READ), ('proxy', PROXY), ('admin', ADMIN), ('self', SELF)):
        if endpoint in endpoints:
            return permission
    return 'unclassified'
