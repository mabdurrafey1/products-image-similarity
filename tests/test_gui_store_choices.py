"""The store box offers what the accounts were last read to hold, and nothing else.

No store is written into the source. Until Load Stores has read the signed-in accounts the box is
empty, because an empty box is honest: a store offered without being read is a guess, and picking one
fetches a catalog the account may not even own.
"""

import unittest
from types import SimpleNamespace
from unittest import mock

import gui


class StoreChoiceTests(unittest.TestCase):
    """What the store box offers, given what was last written down."""

    def choices(self, config):
        with mock.patch.object(gui, "load_config", lambda: config):
            return gui._saved_store_choices()

    def test_no_store_is_offered_before_the_accounts_have_been_read(self):
        self.assertEqual(self.choices({}), {})

    def test_a_config_holding_no_stores_offers_none(self):
        self.assertEqual(self.choices({"noon_stores": []}), {})

    def test_the_stores_that_were_read_are_the_stores_offered(self):
        config = {"noon_stores": [{"label": "TIGER — p-19740", "url": "https://www.noon.com/uae-en/p-19740/"}]}
        self.assertEqual(self.choices(config),
                         {"TIGER — p-19740": "https://www.noon.com/uae-en/p-19740/"})

    def test_an_entry_missing_its_link_is_not_offered(self):
        config = {"noon_stores": [{"label": "TIGER", "url": ""},
                                  {"label": "", "url": "https://www.noon.com/uae-en/p-27379/"}]}
        self.assertEqual(self.choices(config), {})

    def test_no_store_is_written_into_the_source(self):
        """The screenshot's three stores came from here; nothing may reintroduce them."""
        self.assertFalse([name for name in vars(gui) if "DEFAULT_STORE" in name])


class FakeControl:
    """A control of the Stores dialog, remembering only what it was last told to be."""

    def __init__(self, alive=True):
        self.state = "normal"
        self.alive = alive

    def winfo_exists(self):
        return self.alive

    def config(self, **settings):
        self.state = settings["state"]


class StoreControlStateTests(unittest.TestCase):
    """The dialog's controls are dead while a task runs, and live again once it ends.

    Nothing here guards against a store being typed by hand any more: the choices are radiobuttons
    carrying a fixed label out of `store_choices`, so no string that isn't already a key can be
    chosen. The old readonly-combobox rule guarded a hazard the widgets no longer have.
    """

    def tab(self, *controls):
        return SimpleNamespace(_stores_window=object(), _stores_widgets=list(controls))

    def test_the_controls_cannot_be_used_while_a_task_runs(self):
        control = FakeControl()
        gui.SearchTab._enable_store_box(self.tab(control), False)
        self.assertEqual(control.state, "disabled")

    def test_the_controls_come_back_when_the_task_ends(self):
        control = FakeControl()
        tab = self.tab(control)
        gui.SearchTab._enable_store_box(tab, False)
        gui.SearchTab._enable_store_box(tab, True)
        self.assertEqual(control.state, "normal")

    def test_a_closed_dialog_leaves_nothing_to_enable(self):
        """The controls die with the dialog, so a task that ends after it closes must not touch them."""
        control = FakeControl()
        tab = SimpleNamespace(_stores_window=None, _stores_widgets=[control])
        gui.SearchTab._enable_store_box(tab, False)
        self.assertEqual(control.state, "normal")

    def test_a_destroyed_control_is_left_alone(self):
        """A widget can outlive its window as a dead reference; configuring one raises."""
        control = FakeControl(alive=False)
        gui.SearchTab._enable_store_box(self.tab(control), False)
        self.assertEqual(control.state, "normal")


if __name__ == "__main__":
    unittest.main()
