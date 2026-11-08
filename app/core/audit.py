"""Serialize audit log rotation across web workers."""
from logging.handlers import RotatingFileHandler

from .files import file_lock


class SharedAuditHandler(RotatingFileHandler):
    def emit(self, record):
        try:
            with file_lock(self.baseFilename + '.lock'):
                try:
                    super().emit(record)
                finally:
                    if self.stream:
                        self.stream.close()
                        self.stream = None
        except OSError:
            self.handleError(record)
