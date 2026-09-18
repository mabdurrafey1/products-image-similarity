"""The store box offers what the accounts were last read to hold, and nothing else.

No store is written into the source. Until Load Stores has read the signed-in accounts the box is
empty, because an empty box is honest: a store offered without being read is a guess, and picking one
fetches a catalog the account may not even own.
"""

import unittest
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


if __name__ == "__main__":
    unittest.main()
