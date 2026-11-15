"""Entry point. Run with:  python -m ztor   or   python main.py"""

import sys
import os

from ztor.app import main


def _remove_pre_rename_files():
    """Delete what a build from before the rename left in this folder when
    a new build was copied over it. Built copies only, these exact files
    only, quietly; data is never touched."""
    if not getattr(sys, "frozen", False):
        return
    root = os.path.dirname(os.path.abspath(sys.executable))
    old = [os.path.join(root, "MyTorrent.exe")]  # pre-rename name
    for folder in (os.path.join(root, "app"), os.path.join(root, "app", "logs")):
        try:
            old += [os.path.join(folder, n) for n in os.listdir(folder)
                    if n.startswith("MyTorrent.log")]  # pre-rename name
        except OSError:
            pass
    for path in old:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


if __name__ == "__main__":
    _remove_pre_rename_files()
    sys.exit(main())
