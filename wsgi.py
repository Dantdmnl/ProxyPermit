"""WSGI entrypoint for ProxyPermit."""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.web.manager import app  # noqa: E402

__all__ = ["app"]
