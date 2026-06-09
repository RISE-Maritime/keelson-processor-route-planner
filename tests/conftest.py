"""Pytest fixtures / path setup.

Make ``src/`` importable so tests run against the source tree without an install
(``pip install -e .`` works too, but CI and a bare checkout should both just work).
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)
