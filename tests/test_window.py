import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from noon_store.adapters.window import hide_window


class FakeCdp:
    """Stands in for the DevTools connection, which only a real browser can give."""

    def __init__(self, refuses=()):
        self.sent = []
        self.refuses = refuses

    def send(self, method, params=None):
        self.sent.append((method, params))
        if method in self.refuses:
            raise RuntimeError(f"{method} is not available")
        if method == "Browser.getWindowForTarget":
            return {"windowId": 7}
        return {}


class FakeContext:
    def __init__(self, cdp=None, breaks=False):
        self.cdp = cdp or FakeCdp()
        self.breaks = breaks

    def new_cdp_session(self, page):
        if self.breaks:
            raise RuntimeError("this browser has no DevTools")
        return self.cdp


class FakePage:
    """A page knows the context it belongs to, which is what both kinds of launch have in common."""

    def __init__(self, context):
        self.context = context


class HideWindowTests(unittest.TestCase):
    """Hiding the window is cosmetic: a window that stays put must never fail the fetch."""

    def test_the_window_is_put_away(self):
        context = FakeContext()
        self.assertTrue(hide_window(FakePage(context)))
        self.assertIn(("Browser.setWindowBounds",
                       {"windowId": 7, "bounds": {"windowState": "minimized"}}),
                      context.cdp.sent)

    def test_the_window_asked_about_is_the_one_put_away(self):
        """Minimising some other window would leave this one on screen and hide the user's own work."""
        context = FakeContext()
        hide_window(FakePage(context))
        asked, put_away = context.cdp.sent[0], context.cdp.sent[1]
        self.assertEqual(asked[0], "Browser.getWindowForTarget")
        self.assertEqual(put_away[1]["windowId"], 7)

    def test_a_browser_without_devtools_still_fetches(self):
        self.assertFalse(hide_window(FakePage(FakeContext(breaks=True))))

    def test_a_window_that_will_not_minimise_still_fetches(self):
        context = FakeContext(FakeCdp(refuses=("Browser.setWindowBounds",)))
        self.assertFalse(hide_window(FakePage(context)))

    def test_a_browser_that_names_no_window_still_fetches(self):
        context = FakeContext(FakeCdp(refuses=("Browser.getWindowForTarget",)))
        self.assertFalse(hide_window(FakePage(context)))


if __name__ == "__main__":
    unittest.main()
