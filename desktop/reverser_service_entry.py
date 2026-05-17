"""PyInstaller entry-point wrapper for reverser.gui_service.

PyInstaller cannot run __main__.py files that use relative imports directly.
This thin wrapper imports the package properly so relative imports work, then
delegates to the same main() function that `python -m reverser.gui_service`
calls.
"""
import sys
from reverser.gui_service.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
