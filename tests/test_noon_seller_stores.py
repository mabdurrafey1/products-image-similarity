"""The account's stores, as read from Seller Center: everything that needs no browser."""
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from noon_seller_stores import (SellerSessionError, SellerStore, _digits, _merge, _store_url, _to_store,
                                account_overview, add_account, next_profile, remove_account,
                                sign_in_account, store_urls)
from noon_store.adapters.excel_repository import _store_tag
from noon_store.adapters.noon_seller_api import _remembered, remember_projects
from noon_store.domain import StoreRef


def raw(code="STR19740-NAE", country="AE", name_en="TIGER", project="PRJ19740", status="ACTIVE"):
    return {"noon_store_code": code, "country_code": country, "project_code": project,
            "status_code": status, "name_locale": {"name_en": name_en, "name_ar": "الأسد"}}


@contextmanager
def profiles(signed_in=(), empty=(), remembered=None):
    """A home holding profile directories: some signed into, some made but never used.

    Yields the glob that finds them and the file the discovered projects are kept in.
    """
    with tempfile.TemporaryDirectory() as home:
        for name in signed_in:
            os.makedirs(os.path.join(home, name))
            open(os.path.join(home, name, "Cookies"), "w").close()   # what a session leaves behind
        for name in empty:
            os.makedirs(os.path.join(home, name))
        kept = os.path.join(home, "accounts.json")
        if remembered:
            with open(kept, "w") as handle:
                json.dump({os.path.join(home, name): list(projects)
                           for name, projects in remembered.items()}, handle)
        yield os.path.join(home, "noon_seller_profile*"), kept, home


class StoreCodeTests(unittest.TestCase):
    def test_the_store_number_comes_from_the_code(self):
        self.assertEqual(_digits("STR19740-NAE"), "19740")
        self.assertEqual(_digits("STR82799-NSA"), "82799")
        self.assertEqual(_digits(""), "")

    def test_a_store_link_is_built_for_the_country(self):
        self.assertEqual(_store_url("STR19740-NAE", "AE"), "https://www.noon.com/uae-en/p-19740/")
        self.assertEqual(_store_url("STR19740-NSA", "SA"), "https://www.noon.com/ksa-en/p-19740/")
        self.assertEqual(_store_url("STR19740-NEG", "EG"), "https://www.noon.com/egypt-en/p-19740/")

    def test_an_unknown_country_falls_back_to_the_uae(self):
        self.assertEqual(_store_url("STR19740-NXX", "XX"), "https://www.noon.com/uae-en/p-19740/")

    def test_a_code_without_a_number_has_no_link(self):
        self.assertEqual(_store_url("STR-NAE", "AE"), "")


class SellerStoreTests(unittest.TestCase):
    def test_a_listed_store_keeps_its_name_code_and_project(self):
        store = _to_store(raw())
        self.assertEqual((store.name, store.code, store.project, store.country),
                         ("TIGER", "STR19740-NAE", "PRJ19740", "AE"))
        self.assertEqual(store.url, "https://www.noon.com/uae-en/p-19740/")

    def test_the_box_shows_the_name_and_the_store(self):
        self.assertEqual(_to_store(raw()).label, "TIGER — p-19740")
        self.assertEqual(_to_store(raw()).path, "p-19740")

    def test_a_store_without_a_name_is_shown_by_its_number(self):
        self.assertEqual(_to_store(raw(name_en="")).name, "p-19740")

    def test_a_store_whose_code_has_no_number_is_skipped(self):
        self.assertIsNone(_to_store(raw(code="STR-NAE")))

    def test_links_keep_the_order_of_the_stores(self):
        stores = [_to_store(raw(code="STR27379-NAE", name_en="JAJEEK", project="PRJ27379")),
                  _to_store(raw())]
        self.assertEqual(store_urls(stores), ["https://www.noon.com/uae-en/p-27379/",
                                              "https://www.noon.com/uae-en/p-19740/"])


class CombinedStoreTests(unittest.TestCase):
    """One box holds every account's stores, so a store is picked without picking an account first."""

    def test_the_stores_of_every_account_are_listed_together_by_name(self):
        first = [_to_store(raw(code="STR19740-NAE", name_en="TIGER", project="PRJ19740"))]
        second = [_to_store(raw(code="STR55555-NAE", name_en="ahmad", project="PRJ55555"))]
        third = [_to_store(raw(code="STR27379-NAE", name_en="JAJEEK", project="PRJ27379"))]
        self.assertEqual([store.name for store in _merge([first, second, third])],
                         ["ahmad", "JAJEEK", "TIGER"])

    def test_a_store_listed_by_two_accounts_is_offered_once(self):
        listed = [_to_store(raw())]
        self.assertEqual([store.name for store in _merge([listed, list(listed)])], ["TIGER"])

    def test_an_account_that_listed_nothing_costs_the_others_nothing(self):
        listed = [_to_store(raw())]
        self.assertEqual([store.name for store in _merge([[], listed, []])], ["TIGER"])


class AddAccountTests(unittest.TestCase):
    """Adding an account is making a profile directory and signing into it once."""

    def test_the_first_account_gets_the_plain_profile_directory(self):
        with profiles() as (pattern, _, home):
            self.assertEqual(next_profile(pattern), os.path.join(home, "noon_seller_profile"))

    def test_the_next_account_gets_the_next_free_directory(self):
        with profiles(signed_in=("noon_seller_profile",)) as (pattern, _, home):
            self.assertEqual(next_profile(pattern), os.path.join(home, "noon_seller_profile_2"))

    def test_a_directory_nobody_signed_into_is_filled_rather_than_skipped(self):
        """An empty profile already asks to be signed into on every Load Stores; this is that sign-in."""
        with profiles(signed_in=("noon_seller_profile",),
                      empty=("noon_seller_profile_2",)) as (pattern, _, home):
            self.assertEqual(next_profile(pattern), os.path.join(home, "noon_seller_profile_2"))

    def test_a_new_account_keeps_its_profile_and_brings_its_stores(self):
        with profiles(signed_in=("noon_seller_profile",),
                      remembered={"noon_seller_profile": ["PRJ19740"]}) as (pattern, kept, home):
            def read(account, country, log):
                remember_projects(account.profile, ["PRJ55555"], kept)
                return [_to_store(raw(code="STR55555-NAE", name_en="ahmad", project="PRJ55555"))]

            stores = add_account(read=read, pattern=pattern, accounts_file=kept, log=lambda message: None)
            self.assertEqual([store.name for store in stores], ["ahmad"])
            self.assertTrue(os.path.isdir(os.path.join(home, "noon_seller_profile_2")))

    def test_a_sign_in_nobody_completed_leaves_no_account_behind(self):
        """Otherwise a stray click adds an empty account that asks to be signed into forever."""
        with profiles(signed_in=("noon_seller_profile",)) as (pattern, kept, home):
            def read(account, country, log):
                raise SellerSessionError("Nobody signed in.")

            with self.assertRaises(SellerSessionError):
                add_account(read=read, pattern=pattern, accounts_file=kept, log=lambda message: None)
            self.assertFalse(os.path.exists(os.path.join(home, "noon_seller_profile_2")))

    def test_signing_into_an_account_already_added_is_refused(self):
        """A second profile on the same account is a phantom whose stores are all duplicates."""
        with profiles(signed_in=("noon_seller_profile",),
                      remembered={"noon_seller_profile": ["PRJ19740"]}) as (pattern, kept, home):
            def read(account, country, log):
                remember_projects(account.profile, ["PRJ19740"], kept)
                return [_to_store(raw())]

            with self.assertRaises(SellerSessionError) as caught:
                add_account(read=read, pattern=pattern, accounts_file=kept, log=lambda message: None)
            self.assertIn("noon_seller_profile", str(caught.exception))
            self.assertFalse(os.path.exists(os.path.join(home, "noon_seller_profile_2")))

    def test_a_discarded_account_is_forgotten_as_well_as_deleted(self):
        """A leftover entry would make the next account reuse this name inherit projects it doesn't own."""
        with profiles(signed_in=("noon_seller_profile",),
                      remembered={"noon_seller_profile": ["PRJ19740"]}) as (pattern, kept, home):
            def read(account, country, log):
                remember_projects(account.profile, ["PRJ19740"], kept)
                return [_to_store(raw())]

            with self.assertRaises(SellerSessionError):
                add_account(read=read, pattern=pattern, accounts_file=kept, log=lambda message: None)
            self.assertNotIn(os.path.join(home, "noon_seller_profile_2"), _remembered(kept))


SAVED = {"TIGER — p-19740": "https://www.noon.com/uae-en/p-19740/",
         "ahmad — p-55555": "https://www.noon.com/uae-en/p-55555/"}


class AccountOverviewTests(unittest.TestCase):
    """What the Accounts dialog shows: every account, and the stores each one holds."""

    def test_each_account_is_listed_with_the_stores_it_holds(self):
        with profiles(signed_in=("noon_seller_profile", "noon_seller_profile_2"),
                      remembered={"noon_seller_profile": ["PRJ19740"],
                                  "noon_seller_profile_2": ["PRJ55555"]}) as (pattern, kept, _):
            views, orphans = account_overview(SAVED, path=kept, pattern=pattern)
            self.assertEqual([[label for label, _ in view.stores] for view in views],
                             [["TIGER — p-19740"], ["ahmad — p-55555"]])
            self.assertEqual(orphans, [])

    def test_an_account_holding_no_stores_yet_is_still_listed(self):
        """Hiding it would leave no way to sign into it or remove it."""
        with profiles(signed_in=("noon_seller_profile",), empty=("noon_seller_profile_2",),
                      remembered={"noon_seller_profile": ["PRJ19740"]}) as (pattern, kept, _):
            views, _ = account_overview(SAVED, path=kept, pattern=pattern)
            self.assertEqual(len(views), 2)
            self.assertEqual(views[1].stores, ())
            self.assertEqual(views[1].number, 2)

    def test_a_store_no_account_holds_is_reported_rather_than_attached(self):
        """Attaching it to the first account would fetch it through somebody else's session."""
        with profiles(signed_in=("noon_seller_profile",),
                      remembered={"noon_seller_profile": ["PRJ19740"]}) as (pattern, kept, _):
            views, orphans = account_overview(SAVED, path=kept, pattern=pattern)
            self.assertEqual([label for label, _ in views[0].stores], ["TIGER — p-19740"])
            self.assertEqual(orphans, ["ahmad — p-55555"])

    def test_an_account_with_no_session_is_not_called_ready(self):
        """A directory holding a session may still be expired, so "Ready" is the most that is claimed."""
        with profiles(signed_in=("noon_seller_profile",),
                      empty=("noon_seller_profile_2",)) as (pattern, kept, _):
            views, _ = account_overview(SAVED, path=kept, pattern=pattern)
            self.assertEqual([view.status for view in views], ["Ready", "Not signed in"])

    def test_an_account_is_named_by_the_stores_it_holds(self):
        with profiles(signed_in=("noon_seller_profile",), empty=("noon_seller_profile_2",),
                      remembered={"noon_seller_profile": ["PRJ19740"]}) as (pattern, kept, _):
            views, _ = account_overview(SAVED, path=kept, pattern=pattern)
            self.assertEqual(views[0].title, "Account 1 — TIGER")
            self.assertEqual(views[1].title, "Account 2")


class RemoveAccountTests(unittest.TestCase):
    """Removing an account has to delete the profile: forgetting the note alone removes nothing."""

    def test_removing_an_account_deletes_its_profile_and_its_note(self):
        with profiles(signed_in=("noon_seller_profile", "noon_seller_profile_2"),
                      remembered={"noon_seller_profile": ["PRJ19740"],
                                  "noon_seller_profile_2": ["PRJ55555"]}) as (pattern, kept, home):
            gone = os.path.join(home, "noon_seller_profile_2")
            remove_account(gone, kept)
            self.assertFalse(os.path.exists(gone))
            self.assertNotIn(gone, _remembered(kept))

    def test_removing_one_account_leaves_the_others_alone(self):
        with profiles(signed_in=("noon_seller_profile", "noon_seller_profile_2"),
                      remembered={"noon_seller_profile": ["PRJ19740"],
                                  "noon_seller_profile_2": ["PRJ55555"]}) as (pattern, kept, home):
            kept_profile = os.path.join(home, "noon_seller_profile")
            remove_account(os.path.join(home, "noon_seller_profile_2"), kept)
            self.assertTrue(os.path.isdir(kept_profile))
            self.assertEqual(_remembered(kept)[kept_profile], ["PRJ19740"])

    def test_the_stores_left_after_a_removal_are_the_ones_still_held(self):
        """The box must stop offering stores that no signed-in account can fetch any more."""
        with profiles(signed_in=("noon_seller_profile", "noon_seller_profile_2"),
                      remembered={"noon_seller_profile": ["PRJ19740"],
                                  "noon_seller_profile_2": ["PRJ55555"]}) as (pattern, kept, home):
            remove_account(os.path.join(home, "noon_seller_profile_2"), kept)
            views, orphans = account_overview(SAVED, path=kept, pattern=pattern)
            self.assertEqual([label for view in views for label, _ in view.stores],
                             ["TIGER — p-19740"])
            self.assertEqual(orphans, ["ahmad — p-55555"])


class SignInAgainTests(unittest.TestCase):
    """Signing an account in again opens that account's own profile, or refuses."""

    def test_an_account_that_is_gone_is_refused_rather_than_guessed_at(self):
        """Falling back to another profile would sign in as, and then read, the wrong account."""
        with profiles(signed_in=("noon_seller_profile",)) as (pattern, kept, home):
            with self.assertRaises(SellerSessionError):
                sign_in_account(os.path.join(home, "noon_seller_profile_9"),
                                path=kept, pattern=pattern, log=lambda message: None)

    def test_the_account_signed_into_is_the_one_asked_for(self):
        with profiles(signed_in=("noon_seller_profile", "noon_seller_profile_2"),
                      remembered={"noon_seller_profile_2": ["PRJ55555"]}) as (pattern, kept, home):
            wanted = os.path.join(home, "noon_seller_profile_2")
            opened = []

            def read(account, country, log):
                opened.append(account.profile)
                return []

            sign_in_account(wanted, path=kept, pattern=pattern, log=lambda message: None, read=read)
            self.assertEqual(opened, [wanted])


class ListingBridgeTests(unittest.TestCase):
    """A store read from the account must find the listing file the program already keeps for it."""

    def test_an_account_store_matches_its_saved_listing(self):
        for code, expected in (("STR19740-NAE", "uae p-19740"),
                               ("STR27379-NAE", "uae p-27379"),
                               ("STR82799-NAE", "uae p-82799")):
            store = _to_store(raw(code=code))
            self.assertEqual(_store_tag(StoreRef.parse(store.url)), expected)


if __name__ == "__main__":
    unittest.main()
