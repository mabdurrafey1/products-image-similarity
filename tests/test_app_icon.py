import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_icon import set_app_icon


class FakeRoot:
    """Stands in for the tkinter window: the icon code only ever asks it to wear an image."""

    def __init__(self, breaks=False):
        self.worn = []
        self.breaks = breaks

    def iconphoto(self, default, image):
        if self.breaks:
            raise RuntimeError("this window manager has no icons")
        self.worn.append(image)


def an_image(path):
    """Stands in for loading the file into tkinter, which would want a real window to do it in."""
    return ("image", path)


def a_png(directory):
    from PIL import Image

    path = os.path.join(directory, "logo.png")
    Image.new("RGBA", (64, 64), (254, 226, 0, 255)).save(path)
    return path


class AppIconTests(unittest.TestCase):
    """An icon is decoration: nothing about it may stop the program starting."""

    def test_an_icon_that_is_not_there_leaves_the_app_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root = FakeRoot()
            self.assertFalse(set_app_icon(root, os.path.join(directory, "nothing.png"), an_image))
            self.assertEqual(root.worn, [])

    def test_an_icon_that_cannot_be_read_leaves_the_app_running(self):
        def unreadable(path):
            raise OSError("not an image")

        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(set_app_icon(FakeRoot(), a_png(directory), unreadable))

    def test_a_window_that_refuses_icons_leaves_the_app_running(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(set_app_icon(FakeRoot(breaks=True), a_png(directory), an_image))

    def test_the_window_wears_the_icon_it_is_given(self):
        with tempfile.TemporaryDirectory() as directory:
            root, png = FakeRoot(), a_png(directory)
            self.assertTrue(set_app_icon(root, png, an_image))
            self.assertEqual(root.worn, [("image", png)])

    def test_the_icon_is_held_on_to(self):
        """tkinter collects an image nobody references, and the icon blanks a moment after it appears."""
        with tempfile.TemporaryDirectory() as directory:
            root, png = FakeRoot(), a_png(directory)
            set_app_icon(root, png, an_image)
            self.assertEqual(root._app_icon, ("image", png))


if __name__ == "__main__":
    unittest.main()
