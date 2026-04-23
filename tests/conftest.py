"""Put src/ on sys.path so tests can `from audit import ...`.

The repo ships `src/audit.py` as a flat module rather than a package (per
pyproject.toml's `claude-source-audit = "audit:main"` entry point). Tests
need the same import path.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
