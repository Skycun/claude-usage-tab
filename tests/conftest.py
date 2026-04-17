"""Put the repo root on sys.path so tests can ``import strings`` etc.

The project deliberately doesn't use a package layout (no ``__init__.py``);
tests run against the sibling modules directly.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
