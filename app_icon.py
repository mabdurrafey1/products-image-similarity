"""The program's icon: the window's, the taskbar's, and on macOS the Dock tile.

An icon is decoration. Nothing here may stop the program starting, so every step that can fail -- a
missing file, a window manager that has no icons, a Mac API that isn't there -- is caught and
reported as "no icon went on", never raised.
"""
import os
import sys

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
ICON_PNG = os.path.join(ASSETS, "app_icon.png")


def _tk_image(path):
    """The window icon, as tkinter wants it. Tk 8.6 reads PNG itself, so nothing else is needed."""
    import tkinter as tk

    return tk.PhotoImage(file=path)


def set_app_icon(root, path=ICON_PNG, load=_tk_image):
    """Dress the window, taskbar and Dock in this icon. True if it went on, False if it didn't."""
    if not path or not os.path.isfile(path):
        return False
    try:
        image = load(path)
        root.iconphoto(True, image)
    except Exception:
        return False
    # tkinter throws away an image nobody holds a reference to, and the icon goes blank moments later
    root._app_icon = image
    set_dock_icon(path)
    return True


def set_dock_icon(path):
    """The macOS Dock tile, which the window icon alone does not touch. A no-op anywhere else."""
    if sys.platform != "darwin" or not path or not os.path.isfile(path):
        return False
    try:
        from AppKit import NSApplication, NSImage

        image = NSImage.alloc().initWithContentsOfFile_(path)
        if image is None:
            return False
        NSApplication.sharedApplication().setApplicationIconImage_(image)
        return True
    except Exception:
        return False
