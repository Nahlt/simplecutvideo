"""Punto di ingresso usato da PyInstaller (equivale a `python -m simplecutvideo`)."""

import sys

from simplecutvideo.app import main

if __name__ == "__main__":
    sys.exit(main())
