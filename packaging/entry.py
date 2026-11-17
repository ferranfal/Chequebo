#!/usr/bin/python3
"""Ubuntu entry point; use the distro Python and Qt libraries."""
import sys

sys.path.insert(0, '/usr/lib/chequebo')
from chequebo.__main__ import main

if __name__ == '__main__':
    raise SystemExit(main())
