"""Putting a browser window out of the way, on an operating system that allows it.

A window launched with --window-position=-32000,-32000 stays off the edge of the screen on Windows
and Linux. macOS ignores the flag and moves the window back to the top-left corner, in full view, so
there it has to be minimised by asking the browser itself through DevTools.

noon serves its pages only to a real browser, so the window cannot simply be done away with -- see
the note at the top of noon_browser. Hiding it is cosmetic: a window that stays on screen is untidy,
never a reason to fail a fetch, so every step that can fail is caught and reported as "it stayed".
"""
from __future__ import annotations


def hide_window(page) -> bool:
    """Put this page's window away. True if it went, False if it stayed in view."""
    try:
        cdp = page.context.new_cdp_session(page)
        window = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds",
                 {"windowId": window, "bounds": {"windowState": "minimized"}})
        return True
    except Exception:
        return False
