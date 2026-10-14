"""Shared pytest fixtures for mcp-flowise tests.

Ensures the repository root and the ``tests/`` directory are importable so that
``import mcp_flowise`` and ``import mock_flowise`` work from any test module.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

for path in (str(REPO_ROOT), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)
