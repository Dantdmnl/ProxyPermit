"""Append a constrained OS-maintenance event as the unprivileged service account."""
import argparse
from datetime import datetime, timezone
import json
import logging
import os
import secrets
import socket

from dotenv import load_dotenv
from app.core.audit import SharedAuditHandler, bounded_setting


class StrictAuditHandler(SharedAuditHandler):
    def handleError(self, record):  # pylint: disable=invalid-name
        raise OSError('The maintenance audit event could not be recorded.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('event', choices=('ha_certificate_staged', 'ha_certificate_commit_requested',
        'ha_certificate_committed', 'ha_certificate_completed', 'ha_certificate_cancelled', 'ha_certificate_recovered',
        'cluster_maintenance_started', 'cluster_maintenance_verified', 'cluster_maintenance_recovered',
        'management_tls_requested', 'management_tls_enabled', 'management_tls_renewed', 'management_tls_recovered',
        'management_tls_prepared', 'cluster_setup_reserved', 'cluster_setup_transport_started',
        'cluster_setup_configuration_seeded', 'cluster_setup_activation_requested', 'cluster_setup_activated'))
    parser.add_argument('operation')
    parser.add_argument('actor_uid', type=int)
    parser.add_argument('--requested-by')
    parser.add_argument('--request-id')
    args = parser.parse_args(argv)
    if len(args.operation) != 32 or any(c not in '0123456789abcdef' for c in args.operation) or args.actor_uid < 0:
        parser.error('Invalid maintenance operation or UID.')
    if args.requested_by is not None and (not 1 <= len(args.requested_by) <= 128
            or not args.requested_by.isprintable() or not args.request_id
            or len(args.request_id) != 32 or any(c not in '0123456789abcdef' for c in args.request_id)):
        parser.error('Invalid web maintenance attribution.')
    load_dotenv('/etc/proxypermit/.env')
    event = dict(timestamp=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        id=secrets.token_hex(16), event=args.event, message=args.event, level='INFO',
        operation=args.operation, initiated_by_uid=args.actor_uid, role='system',
        username='os-administrator', node=socket.gethostname(), ip='-', path='-', method='-')
    if args.requested_by:
        event.update(requested_by=args.requested_by, request_id=args.request_id)
    handler = StrictAuditHandler(os.getenv('AUDIT_LOG_PATH', '/var/log/proxypermit/audit.log'),
        maxBytes=bounded_setting('AUDIT_LOG_MAX_BYTES', 2097152, 65536, 104857600),
        backupCount=bounded_setting('AUDIT_LOG_BACKUP_COUNT', 5, 1, 50), encoding='utf-8', delay=True)
    try:
        handler.emit(logging.LogRecord('proxypermit-maintenance', logging.INFO, __file__, 0,
                                      json.dumps(event), (), None))
    finally:
        handler.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
