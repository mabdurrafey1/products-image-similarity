"""The Seller Center catalog adapter: everything that needs no browser, and the crawl it drives."""
import json
import ntpath
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from noon_store.adapters.excel_repository import ExcelListingRepository
from noon_store.adapters.noon_seller_api import (BRAND_SEPARATOR, SEPARATOR, Account, _brand_groups,
                                                 _canonical, _categories, _filters, _project, _sort,
                                                 _store_code, _fetch_profile, _to_product,
                                                 account_projects, forget_profile, landed_project,
                                                 load_accounts, profile_for_project, projects_held,
                                                 remember_projects)
from noon_store.domain import CatalogPage, CatalogQuery, Category, Product, StoreError, StoreRef
from noon_store.use_cases import FetchStore

JAN = datetime(2026, 1, 5, 9, 0)


def hit(sku="ZABC", psku="P1", title="A product", brand="Brand", price=12.5, image="v1/img", offer="OF1"):
    return {"csku_parent": sku, "partner_sku": psku, "offer_code": offer, "price": price,
            "content": {"title": title, "brand": brand, "image": image}}


class StoreCodeTests(unittest.TestCase):
    def test_a_store_link_names_its_seller_centre_store(self):
        self.assertEqual(_store_code(StoreRef.parse("https://www.noon.com/uae-en/p-19740/")), "STR19740-NAE")
        self.assertEqual(_store_code(StoreRef.parse("https://www.noon.com/ksa-en/p-27379/")), "STR27379-NSA")

    def test_a_store_link_names_its_project(self):
        self.assertEqual(_project(StoreRef.parse("https://www.noon.com/uae-en/p-82799/")), "PRJ82799")

    def test_a_link_without_a_store_number_is_refused(self):
        with self.assertRaises(StoreError):
            _store_code(StoreRef.parse("https://www.noon.com/uae-en/some-page/"))


@contextmanager
def signed_in(*profile_names, remembered=None):
    """A home holding one Chrome profile directory per signed-in noon account.

    Yields the glob that finds them and the file the discovered projects are kept in.
    """
    with tempfile.TemporaryDirectory() as home:
        for name in profile_names:
            os.makedirs(os.path.join(home, name))
        kept = os.path.join(home, "accounts.json")
        if remembered:
            with open(kept, "w") as handle:
                json.dump({os.path.join(home, name): list(projects)
                           for name, projects in remembered.items()}, handle)
        yield os.path.join(home, "noon_seller_profile*"), kept, home


class AccountTests(unittest.TestCase):
    """One signed-in Chrome profile per noon account, however many of them there are."""

    def test_every_signed_in_profile_is_an_account(self):
        with signed_in("noon_seller_profile", "noon_seller_profile_2", "noon_seller_profile_x") as (
                pattern, kept, home):
            found = load_accounts(path=kept, pattern=pattern)
        self.assertEqual([account.label for account in found],
                         ["noon_seller_profile", "noon_seller_profile_2", "noon_seller_profile_x"])

    def test_an_account_nobody_signed_into_is_not_offered(self):
        with signed_in() as (pattern, kept, _):
            self.assertEqual(load_accounts(path=kept, pattern=pattern), ())

    def test_an_account_keeps_the_projects_last_discovered_in_it(self):
        with signed_in("noon_seller_profile_2",
                       remembered={"noon_seller_profile_2": ["PRJ55555"]}) as (pattern, kept, _):
            found = load_accounts(path=kept, pattern=pattern)
        self.assertEqual(found[0].projects, ("PRJ55555",))

    def test_a_new_account_starts_with_no_projects_rather_than_being_skipped(self):
        with signed_in("noon_seller_profile_2") as (pattern, kept, _):
            found = load_accounts(path=kept, pattern=pattern)
        self.assertEqual((found[0].label, found[0].projects), ("noon_seller_profile_2", ()))

    def test_discovering_a_project_leaves_the_other_accounts_alone(self):
        with signed_in("noon_seller_profile", "noon_seller_profile_2",
                       remembered={"noon_seller_profile": ["PRJ19740"]}) as (pattern, kept, home):
            remember_projects(os.path.join(home, "noon_seller_profile_2"), ["PRJ55555"], path=kept)
            found = {account.label: account.projects
                     for account in load_accounts(path=kept, pattern=pattern)}
        self.assertEqual(found, {"noon_seller_profile": ("PRJ19740",),
                                 "noon_seller_profile_2": ("PRJ55555",)})


class ProfilePathTests(unittest.TestCase):
    """One profile directory is one account, however the operating system spells the path to it."""

    def test_the_two_windows_spellings_of_one_profile_are_one_account(self):
        # On Windows, expanduser substitutes the home directory but leaves the caller's forward slash
        # alone, while glob rebuilds its results with backslashes. Keyed on the raw strings the two
        # never match, so every Windows account starts out holding no projects at all.
        typed = _canonical("C:/Users/you/noon_seller_profile", ntpath)
        found = _canonical("C:\\Users\\you\\noon_seller_profile", ntpath)
        self.assertEqual(typed, found)

    def test_a_trailing_separator_does_not_make_a_second_account(self):
        self.assertEqual(_canonical("/home/you/noon_seller_profile/"),
                         _canonical("/home/you/noon_seller_profile"))

    def test_a_profile_written_down_by_another_spelling_keeps_its_projects(self):
        # The same failure Windows hits, in a spelling this machine can reproduce: what was written
        # down and what glob finds must be the same account, or the account loads holding nothing.
        with signed_in("noon_seller_profile") as (pattern, kept, home):
            remember_projects(os.path.join(home, "noon_seller_profile") + os.sep, ["PRJ19740"], path=kept)
            found = load_accounts(path=kept, pattern=pattern)
        self.assertEqual(found[0].projects, ("PRJ19740",))

    def test_forgetting_a_profile_spelled_another_way_still_forgets_it(self):
        # A note left behind is inherited by whoever signs into that directory name next, and their
        # store would then be fetched through somebody else's session.
        with signed_in("noon_seller_profile", remembered={"noon_seller_profile": ["PRJ19740"]}) as (
                pattern, kept, home):
            forget_profile(os.path.join(home, "noon_seller_profile") + os.sep, path=kept)
            found = load_accounts(path=kept, pattern=pattern)
        self.assertEqual(found[0].projects, ())


class AccountProjectsTests(unittest.TestCase):
    """What an account holds is what noon says it holds -- nothing is seeded or typed in."""

    def test_every_project_the_account_holds_is_listed(self):
        answer = {"projects": [{"projectCode": "PRJ19740", "projectName": "TIGER"},
                               {"projectCode": "PRJ27379", "projectName": "JAJEEK"},
                               {"projectCode": "PRJ82799", "projectName": "ELTRAZONE"}]}
        self.assertEqual(account_projects(lambda: answer), ("PRJ19740", "PRJ27379", "PRJ82799"))

    def test_a_project_named_after_another_project_is_not_mistaken_for_it(self):
        # Measured on a real account: project names are free text. One is named after another
        # project's code and one after an email address, so only the code field may be read --
        # matching codes out of the text would fetch a store through the wrong account's session.
        answer = {"projects": [{"projectCode": "PRJ467945", "projectName": "PRJ19740"},
                               {"projectCode": "PRJ555572", "projectName": "someone@example.com"}]}
        self.assertEqual(account_projects(lambda: answer), ("PRJ467945", "PRJ555572"))

    def test_an_account_holding_nothing_is_no_projects_rather_than_a_failure(self):
        self.assertEqual(account_projects(lambda: {}), ())

    def test_a_project_without_a_code_is_skipped(self):
        answer = {"projects": [{"projectName": "half a record"}, {"projectCode": "PRJ19740"}]}
        self.assertEqual(account_projects(lambda: answer), ("PRJ19740",))


class ProfileForProjectTests(unittest.TestCase):
    """A store is fetched from the account that owns it -- never from whichever happens to be first."""

    accounts = (Account(profile="/home/first", projects=("PRJ19740", "PRJ27379")),
                Account(profile="/home/second", projects=("PRJ55555",)))

    def test_a_store_is_fetched_from_the_account_that_owns_its_project(self):
        self.assertEqual(profile_for_project("PRJ55555", self.accounts), "/home/second")
        self.assertEqual(profile_for_project("PRJ27379", self.accounts), "/home/first")

    def test_a_project_no_account_owns_is_refused_by_name(self):
        with self.assertRaises(StoreError) as refused:
            profile_for_project("PRJ99999", self.accounts)
        self.assertIn("PRJ99999", str(refused.exception))


class FetchProfileTests(unittest.TestCase):
    """Which signed-in profile a fetch opens: the owner's, unless a person named one."""

    accounts = (Account(profile="/home/first", projects=("PRJ19740",)),
                Account(profile="/home/second", projects=("PRJ55555",)))

    def test_a_fetch_opens_the_account_that_owns_the_store(self):
        store = StoreRef.parse("https://www.noon.com/uae-en/p-55555/")
        self.assertEqual(_fetch_profile(store, "", self.accounts), "/home/second")

    def test_a_named_profile_still_wins_over_the_owning_account(self):
        store = StoreRef.parse("https://www.noon.com/uae-en/p-55555/")
        self.assertEqual(_fetch_profile(store, "~/somewhere_else", self.accounts),
                         os.path.expanduser("~/somewhere_else"))

    def test_a_store_no_signed_in_account_owns_is_refused(self):
        store = StoreRef.parse("https://www.noon.com/uae-en/p-99999/")
        with self.assertRaises(StoreError):
            _fetch_profile(store, "", self.accounts)


class LandedProjectTests(unittest.TestCase):
    """A fresh account names its own project in the requests its catalog makes."""

    def test_the_session_names_the_project_it_landed_on(self):
        self.assertEqual(landed_project({"x-project": "PRJ55555", "accept": "*/*"}), "PRJ55555")

    def test_the_header_is_found_whatever_its_capitals(self):
        self.assertEqual(landed_project({"X-Project": "PRJ55555"}), "PRJ55555")

    def test_headers_that_name_no_project_discover_nothing(self):
        self.assertEqual(landed_project({"accept": "*/*"}), "")


class RequestTests(unittest.TestCase):
    def test_the_whole_store_keeps_the_pages_own_filters(self):
        self.assertEqual(_filters({"live_status": True}, None), {"live_status": True})

    def test_a_family_and_a_brand_narrow_the_filters(self):
        self.assertEqual(_filters({"live_status": True}, "Audio & Video"),
                         {"live_status": True, "family": ["Audio & Video"]})
        self.assertEqual(_filters({}, f"Audio & Video{SEPARATOR}Generic"),
                         {"family": ["Audio & Video"], "brand": ["Generic"]})

    def test_only_the_orderings_the_api_honours_are_sent(self):
        self.assertEqual(_sort(CatalogQuery()), ("", ""))                                  # newest first
        self.assertEqual(_sort(CatalogQuery(sort_by="price", sort_dir="asc")), ("offer_price", "asc"))
        self.assertEqual(_sort(CatalogQuery(sort_by="price", sort_dir="desc")), ("offer_price", "desc"))
        self.assertEqual(_sort(CatalogQuery(sort_by="views", sort_dir="asc")), ("", ""))   # ignored by noon


class RecordTests(unittest.TestCase):
    def test_a_record_becomes_a_product(self):
        product = _to_product(hit(), "uae-en")
        self.assertEqual((product.sku, product.psku, product.title, product.brand, product.price),
                         ("ZABC", "P1", "A product", "Brand", 12.5))
        self.assertEqual(product.link, "https://www.noon.com/uae-en/ZABC/p/?o=OF1")
        self.assertTrue(product.image_urls[0].startswith("https://f.nooncdn.com/p/v1/img.jpg"))

    def test_the_partner_sku_is_carried_through(self):
        """The partner SKU is why this path exists at all: the public store pages never carry one."""
        self.assertEqual(_to_product(hit(psku="ABC-123"), "uae-en").psku, "ABC-123")
        self.assertEqual(_to_product(hit(psku=None), "uae-en").psku, "")

    def test_a_record_without_a_sku_is_skipped(self):
        self.assertIsNone(_to_product({"content": {"title": "No sku"}}, "uae-en"))

    def test_a_record_without_a_price_or_image_still_loads(self):
        product = _to_product(hit(price=None, image=""), "uae-en")
        self.assertIsNone(product.price)
        self.assertEqual(product.image_urls, ())


class FacetTests(unittest.TestCase):
    def test_the_store_is_narrowed_by_family(self):
        facets = {"family": [{"key": "Audio & Video"}, {"key": "Mobiles"}]}
        self.assertEqual(_categories(None, facets), (Category("Audio & Video"), Category("Mobiles")))

    def test_a_family_is_narrowed_by_its_own_brands(self):
        tree = _categories("Mobiles", {"brand": [{"key": "Generic"}]})
        self.assertEqual(tree, (Category("Mobiles", (Category(f"Mobiles{SEPARATOR}Generic"),)),))

    def test_small_brands_share_one_request(self):
        """A family of many small brands must not cost one request per brand."""
        buckets = [{"key": "Big", "doc_count": 8000}, {"key": "Small", "doc_count": 50},
                   {"key": "Tiny", "doc_count": 20}]
        self.assertEqual(_brand_groups(buckets, 10_000), [["Big", "Small", "Tiny"]])

    def test_brands_are_split_when_they_cannot_share(self):
        buckets = [{"key": "A", "doc_count": 8000}, {"key": "B", "doc_count": 7000}]
        self.assertEqual(_brand_groups(buckets, 10_000), [["A"], ["B"]])

    def test_a_group_of_brands_is_one_filter(self):
        code = f"Mobiles{SEPARATOR}Generic{BRAND_SEPARATOR}Other"
        self.assertEqual(_filters({}, code), {"family": ["Mobiles"], "brand": ["Generic", "Other"]})


class FakeSellerCatalog:
    """The seller API in miniature: a per-query cap, family/brand facets, and products in no family."""
    page_size = 2
    max_pages = 2      # cap of 4 records per query
    batch_size = 8

    def __init__(self, entries):
        self.entries = entries      # (product, family, brand)
        self.requests = 0
        self.narrowed = []          # the category of every request that asked for one

    def fetch_pages(self, queries):
        self.requests += len(queries)
        self.narrowed += [q.category for q in queries if q.category]
        return [self.page(query) for query in queries]

    def matching(self, category):
        if not category:
            return list(self.entries)
        family, _, brand = category.partition(SEPARATOR)
        return [e for e in self.entries if e[1] == family and (not brand or e[2] == brand)]

    def page(self, query):
        matching = self.matching(query.category)
        ordered = [p for p, _, _ in matching]
        if query.sort_by == "price":
            ordered.sort(key=lambda p: p.price, reverse=query.sort_dir == "desc")
        start = (query.page - 1) * self.page_size
        if query.page > self.max_pages:
            start = (self.max_pages - 1) * self.page_size   # the cap: no deeper page is served
        facets = {}
        if len(ordered) > self.page_size * self.max_pages:
            if query.category:
                facets = {"brand": [{"key": b} for b in sorted({e[2] for e in matching})]}
            else:
                facets = {"family": [{"key": f} for f in sorted({e[1] for e in matching if e[1]})]}
        return CatalogPage(
            total=len(ordered),
            products=tuple(ordered[start:start + self.page_size]),
            store_name="TIGER",
            price_range=None,
            categories=_categories(query.category, facets),
        )


@contextmanager
def opener(catalog):
    yield catalog


def quiet(message):
    pass


class SweepTests(unittest.TestCase):
    """The crawl must end up with the store's whole count, including products in no family."""

    def entries(self):
        # Two families over the cap, plus one product noon files under no family at all.
        items = []
        for i in range(5):
            items.append((Product(f"SKU-A{i}", f"A{i}", "Brand", float(10 + i), "link"), "Audio", "Generic"))
        for i in range(5):
            items.append((Product(f"SKU-M{i}", f"M{i}", "Brand", float(20 + i), "link"), "Mobiles", "Other"))
        items.append((Product("SKU-LOST", "Lost", "Brand", 1.0, "link"), "", ""))
        return items

    def test_a_store_within_reach_of_two_orders_is_not_split_into_categories(self):
        """The cheap route for a store of under two capfuls: read it cheapest-first, then dearest-first."""
        # Six products against a cap of four: one query can't reach them all, but two opposite orders can.
        entries = [(Product(f"SKU-{i}", f"P{i}", "Brand", float(10 + i), "link"), "Audio", "Generic")
                   for i in range(6)]
        catalog = FakeSellerCatalog(entries)
        with tempfile.TemporaryDirectory() as folder:
            result = FetchStore(lambda store: opener(catalog), ExcelListingRepository(folder), quiet,
                                clock=lambda: JAN).execute("https://www.noon.com/uae-en/p-19740/")
        self.assertEqual(result.product_count, 6)
        self.assertEqual(catalog.narrowed, [], "the store was split by category when it needn't have been")

    def test_every_product_is_found_including_the_ones_in_no_family(self):
        catalog = FakeSellerCatalog(self.entries())
        with tempfile.TemporaryDirectory() as folder:
            use_case = FetchStore(lambda store: opener(catalog), ExcelListingRepository(folder),
                                  quiet, clock=lambda: JAN)
            result = use_case.execute("https://www.noon.com/uae-en/p-19740/")
        self.assertEqual(result.product_count, 11)
        self.assertEqual(result.store_name, "TIGER")

    def test_the_product_in_no_family_is_the_one_the_sweep_recovers(self):
        catalog = FakeSellerCatalog(self.entries())
        with tempfile.TemporaryDirectory() as folder:
            repository = ExcelListingRepository(folder)
            FetchStore(lambda store: opener(catalog), repository, quiet, clock=lambda: JAN).execute(
                "https://www.noon.com/uae-en/p-19740/")
            saved = repository.load(repository.find(StoreRef.parse("https://www.noon.com/uae-en/p-19740/")))
        self.assertIn("SKU-LOST", saved.skus())


class ProjectsHeldTests(unittest.TestCase):
    """Which projects an account reads. noon's own listing is the answer; the landed project is a fallback."""

    def setUp(self):
        self.written = {}

    def remember(self, profile, projects):
        self.written[profile] = tuple(projects)

    def listing(self, *codes):
        return lambda: {"projects": [{"projectCode": code} for code in codes]}

    def test_an_account_reads_every_project_noon_lists_not_only_the_one_it_landed_on(self):
        account = Account(profile="/home/noon_seller_profile")
        held = projects_held(account, self.listing("PRJ19740", "PRJ27379", "PRJ82799"),
                             {"x-project": "PRJ19740"}, self.remember)
        self.assertEqual(held, ("PRJ19740", "PRJ27379", "PRJ82799"))

    def test_the_projects_noon_listed_are_written_down(self):
        account = Account(profile="/home/noon_seller_profile")
        projects_held(account, self.listing("PRJ19740", "PRJ27379"), {"x-project": "PRJ19740"},
                      self.remember)
        self.assertEqual(self.written, {"/home/noon_seller_profile": ("PRJ19740", "PRJ27379")})

    def test_a_project_added_since_last_time_is_picked_up(self):
        # The account was written down holding one project; noon now lists two. noon wins.
        account = Account(profile="/home/noon_seller_profile", projects=("PRJ19740",))
        held = projects_held(account, self.listing("PRJ19740", "PRJ27379"), {"x-project": "PRJ19740"},
                             self.remember)
        self.assertEqual(held, ("PRJ19740", "PRJ27379"))

    def test_an_account_noon_will_not_list_falls_back_to_the_project_it_landed_on(self):
        account = Account(profile="/home/noon_seller_profile")
        held = projects_held(account, lambda: {"projects": []}, {"x-project": "PRJ19740"}, self.remember)
        self.assertEqual(held, ("PRJ19740",))
        self.assertEqual(self.written, {"/home/noon_seller_profile": ("PRJ19740",)})

    def test_a_listing_that_fails_falls_back_to_the_project_it_landed_on(self):
        def refuse():
            raise RuntimeError("project/list answered 400")

        account = Account(profile="/home/noon_seller_profile")
        held = projects_held(account, refuse, {"x-project": "PRJ27379"}, self.remember)
        self.assertEqual(held, ("PRJ27379",))

    def test_an_account_that_names_no_project_at_all_holds_none(self):
        account = Account(profile="/home/noon_seller_profile")
        self.assertEqual(projects_held(account, lambda: {"projects": []}, {}, self.remember), ())
        self.assertEqual(self.written, {}, "an account holding nothing must not be written down as empty")


class FakeContext:
    """Stands in for a launched Chrome so the tests never start a browser."""

    def __init__(self, profile):
        self.profile = profile
        self.closed = False

    def close(self):
        self.closed = True


class SellerBrowserTests(unittest.TestCase):
    """One Chrome per signed-in account, shared by every store read through that account.

    Starting Chrome is almost the whole cost of reading a store: the requests themselves take under two
    seconds, the launch about twelve. Reading seven stores used to pay that twelve seconds seven times.
    """

    def setUp(self):
        from noon_store.adapters.noon_seller_api import SellerBrowser
        self.launched = []

        def launch(profile):
            context = FakeContext(profile)
            self.launched.append(context)
            return context, f"page for {profile}"

        self.browser = SellerBrowser(launch=launch)

    def test_many_stores_of_one_account_share_a_single_launch(self):
        with self.browser as browser:
            for _ in range(7):
                browser.page_for("/home/noon_seller_profile")
        self.assertEqual(len(self.launched), 1,
                         "seven stores of one account must start Chrome once, not seven times")

    def test_a_store_is_read_through_the_page_its_account_is_signed_into(self):
        with self.browser as browser:
            _, first = browser.page_for("/home/noon_seller_profile")
            _, again = browser.page_for("/home/noon_seller_profile")
        self.assertEqual(first, again, "the second store must reuse the signed-in page, not a new one")

    def test_a_second_account_gets_a_browser_of_its_own(self):
        # A profile is one cookie jar, so two accounts cannot share a browser however much it would save.
        with self.browser as browser:
            browser.page_for("/home/noon_seller_profile")
            browser.page_for("/home/noon_seller_profile_2")
        self.assertEqual([context.profile for context in self.launched],
                         ["/home/noon_seller_profile", "/home/noon_seller_profile_2"])

    def test_letting_go_closes_every_browser_it_opened(self):
        # Chrome only writes the session back to the profile on a clean exit, so nothing may be left open.
        with self.browser as browser:
            browser.page_for("/home/noon_seller_profile")
            browser.page_for("/home/noon_seller_profile_2")
        self.assertTrue(all(context.closed for context in self.launched),
                        "a profile left open loses the signed-in session it was holding")

    def test_a_browser_that_will_not_close_does_not_strand_the_others(self):
        with self.browser as browser:
            first, _ = browser.page_for("/home/noon_seller_profile")
            browser.page_for("/home/noon_seller_profile_2")
            first.close = lambda: (_ for _ in ()).throw(RuntimeError("Chrome is already gone"))
        self.assertTrue(self.launched[1].closed, "one browser failing to close must not leave the rest open")


class SharedContextTests(unittest.TestCase):
    """The captured catalog context belongs to the account, not to each of its stores.

    Opening the catalog costs about six seconds and exists only to capture the headers and body template a
    store read sends. Every store of one account was paying that, though the only difference between them
    is the project the headers name -- so seven stores paid forty seconds to learn the same thing seven
    times. Captured once per account, a second store costs about a second.
    """

    def setUp(self):
        from noon_store.adapters.noon_seller_api import SellerBrowser

        self.browser = SellerBrowser(launch=lambda profile: (FakeContext(profile), f"page for {profile}"))
        self.captures = []

    def capture(self, project):
        """Stands in for the six-second catalog load, recording that it happened."""
        def capturing():
            self.captures.append(project)
            return {"headers": {"x-project": project}, "body": {"noon_store_code": "STR1-NAE"}}
        return capturing

    def test_the_catalog_is_opened_once_however_many_stores_the_account_has(self):
        with self.browser as browser:
            for _ in range(7):
                browser.context_for("/home/noon_seller_profile", self.capture("PRJ1"))
        self.assertEqual(len(self.captures), 1,
                         "seven stores of one account must open the catalog once, not seven times")

    def test_every_store_is_given_the_context_that_was_captured(self):
        with self.browser as browser:
            first = browser.context_for("/home/noon_seller_profile", self.capture("PRJ1"))
            again = browser.context_for("/home/noon_seller_profile", self.capture("PRJ1"))
        self.assertEqual(first, again, "the second store must be given the captured context, not an empty one")

    def test_a_second_account_captures_a_context_of_its_own(self):
        # A context carries the account's own project and is read with that account's cookies, so sharing
        # one between accounts would read the wrong catalog -- or somebody else's.
        with self.browser as browser:
            browser.context_for("/home/noon_seller_profile", self.capture("PRJ1"))
            browser.context_for("/home/noon_seller_profile_2", self.capture("PRJ2"))
        self.assertEqual(self.captures, ["PRJ1", "PRJ2"])

    def test_a_capture_that_fails_is_not_remembered_as_the_account_s_context(self):
        # A store that failed to open the catalog must not leave the next one holding an empty context, which
        # would fail every read of the account instead of just the one.
        def refuse():
            raise StoreError("Seller Center didn't load its catalog, so the store couldn't be read.")

        with self.browser as browser:
            with self.assertRaises(StoreError):
                browser.context_for("/home/noon_seller_profile", refuse)
            recovered = browser.context_for("/home/noon_seller_profile", self.capture("PRJ1"))
        self.assertEqual(recovered, {"headers": {"x-project": "PRJ1"}, "body": {"noon_store_code": "STR1-NAE"}})


class BorrowedBrowser:
    """A SellerBrowser that hands out a stub page, so a catalog can be opened without starting Chrome."""

    def __init__(self):
        self.asked_for = []
        self.closed = False
        self._context_of = {}

    def page_for(self, profile):
        self.asked_for.append(profile)
        return FakeContext(profile), f"page for {profile}"

    def context_for(self, profile, capture):
        if profile not in self._context_of:
            self._context_of[profile] = capture()
        return self._context_of[profile]

    def close(self):
        self.closed = True


class CatalogReusingAContextTests(unittest.TestCase):
    """A second store of an account is read with the context its first store captured.

    Opening the catalog is about six seconds of a seven-second read, and every store of an account was
    paying it to capture the same thing. Only the project differs, and the catalog swaps that in itself.
    """

    def setUp(self):
        from noon_store.adapters.noon_seller_api import NoonSellerApiCatalog

        self.loads = []
        test = self

        class Catalog(NoonSellerApiCatalog):
            """The real catalog, with only the page load that talks to noon stubbed out."""

            def _load_context(self):
                # What _open_catalog gets off the page: the FIRST store's project and store code.
                test.loads.append(_project(self.store))
                return {"headers": {"x-project": "PRJ82799", "accept": "application/json"},
                        "body": {"noon_store_code": "STR82799-NAE", "per_page": 100, "filters": {}}}

            def _name_of_store(self):
                return "Test Store"

        self.Catalog = Catalog
        self.browser = BorrowedBrowser()
        self.home = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.home.name, "noon_seller_profile"), exist_ok=True)
        self._saved = os.environ.get("NOON_PROFILE")
        os.environ["NOON_PROFILE"] = os.path.join(self.home.name, "noon_seller_profile")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("NOON_PROFILE", None)
        else:
            os.environ["NOON_PROFILE"] = self._saved
        self.home.cleanup()

    def store(self, path):
        return StoreRef.parse(f"https://www.noon.com/uae-en/{path}/")

    def test_the_second_store_of_an_account_does_not_open_the_catalog_again(self):
        with self.Catalog(self.store("p-82799"), browser=self.browser):
            pass
        with self.Catalog(self.store("p-19740"), browser=self.browser):
            pass
        self.assertEqual(len(self.loads), 1,
                         "the second store must reuse the account's captured context, not spend six seconds "
                         "capturing the same thing again")

    def test_the_second_store_is_read_as_itself_not_as_the_store_that_captured(self):
        # The captured context names the first store. Reading the second with it unchanged would quietly
        # return the first store's products under the second store's name.
        with self.Catalog(self.store("p-19740"), browser=self.browser) as catalog:
            self.assertEqual(catalog._headers["x-project"], "PRJ19740")
            self.assertEqual(catalog._base_body["noon_store_code"], _store_code(self.store("p-19740")))

    def test_the_rest_of_the_captured_context_is_kept(self):
        with self.Catalog(self.store("p-19740"), browser=self.browser) as catalog:
            self.assertEqual(catalog._headers["accept"], "application/json")
            self.assertEqual(catalog._base_body["per_page"], 100)

    def test_one_store_s_context_is_not_handed_to_another_account(self):
        second = BorrowedBrowser()
        with self.Catalog(self.store("p-82799"), browser=self.browser):
            pass
        with self.Catalog(self.store("p-19740"), browser=second):
            pass
        self.assertEqual(len(self.loads), 2, "a second account must capture its own context")


class KnownStoreNameTests(unittest.TestCase):
    """A store whose name is already known is not asked for it again.

    The name costs a request of about a second per store, and Load Stores has already read and saved it --
    asking noon again to learn what the account just told us is a second spent per store to learn nothing.
    """

    def setUp(self):
        from noon_store.adapters.noon_seller_api import NoonSellerApiCatalog

        self.asked = []
        test = self

        class Catalog(NoonSellerApiCatalog):
            """The real catalog with only the two steps that talk to noon stubbed out."""

            def _load_context(self):
                return {"headers": {"x-project": "PRJ19740"},
                        "body": {"noon_store_code": "STR19740-NAE", "filters": {}}}

            def _name_of_store(self):
                test.asked.append(_project(self.store))
                return "The Name noon Gave"

        self.Catalog = Catalog
        self.browser = BorrowedBrowser()
        self.home = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.home.name, "noon_seller_profile"), exist_ok=True)
        self._saved = os.environ.get("NOON_PROFILE")
        os.environ["NOON_PROFILE"] = os.path.join(self.home.name, "noon_seller_profile")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("NOON_PROFILE", None)
        else:
            os.environ["NOON_PROFILE"] = self._saved
        self.home.cleanup()

    def store(self):
        return StoreRef.parse("https://www.noon.com/uae-en/p-19740/")

    def test_a_catalog_given_the_name_does_not_ask_noon_for_it(self):
        with self.Catalog(self.store(), browser=self.browser, name="TIGER") as catalog:
            self.assertEqual(catalog.store_name, "TIGER")
        self.assertEqual(self.asked, [], "the name was already known, so noon must not be asked for it")

    def test_a_catalog_given_no_name_still_asks(self):
        # Nothing saved for this store yet -- a fetch of a store the box has never listed must still work.
        with self.Catalog(self.store(), browser=self.browser) as catalog:
            self.assertEqual(catalog.store_name, "The Name noon Gave")
        self.assertEqual(len(self.asked), 1)

    def test_a_blank_name_is_not_taken_as_an_answer(self):
        # An empty label is missing information, not a store called "". Asking is what gets a real name.
        with self.Catalog(self.store(), browser=self.browser, name="") as catalog:
            self.assertEqual(catalog.store_name, "The Name noon Gave")
        self.assertEqual(len(self.asked), 1)


class CatalogSharingABrowserTests(unittest.TestCase):
    """A store read through a browser the caller already has open, rather than one of its own."""

    def setUp(self):
        from noon_store.adapters.noon_seller_api import NoonSellerApiCatalog

        class Catalog(NoonSellerApiCatalog):
            """The real catalog with only the two steps that talk to noon stubbed out."""

            def _open_catalog(self):
                self._headers = {"x-project": "PRJ19740"}
                self._base_body = {"noon_store_code": "STR19740-NAE"}
                self._base_filters = {}

            def _name_of_store(self):
                return "Test Store"

        self.Catalog = Catalog
        self.browser = BorrowedBrowser()
        self.home = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.home.name, "noon_seller_profile"), exist_ok=True)
        self._saved = os.environ.get("NOON_PROFILE")
        os.environ["NOON_PROFILE"] = os.path.join(self.home.name, "noon_seller_profile")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("NOON_PROFILE", None)
        else:
            os.environ["NOON_PROFILE"] = self._saved
        self.home.cleanup()

    def store(self):
        return StoreRef.parse("https://www.noon.com/uae-en/p-19740/")

    def test_a_catalog_given_a_browser_reads_through_it_instead_of_starting_one(self):
        with self.Catalog(self.store(), browser=self.browser) as catalog:
            self.assertEqual(catalog.store_name, "Test Store")
        self.assertEqual(len(self.browser.asked_for), 1,
                         "the catalog must read through the browser it was given")

    def test_a_catalog_leaves_a_borrowed_browser_open_for_the_next_store(self):
        # The caller owns the browser: closing it here would cost the next store the launch all over again.
        with self.Catalog(self.store(), browser=self.browser):
            pass
        self.assertFalse(self.browser.closed,
                         "a borrowed browser must outlive the store that borrowed it")

    def test_a_borrowed_catalog_still_forgets_the_context_it_captured(self):
        with self.Catalog(self.store(), browser=self.browser) as catalog:
            self.assertTrue(catalog._headers)
        self.assertEqual(catalog._headers, {},
                         "the captured headers must not outlive the fetch that captured them")


if __name__ == "__main__":
    unittest.main()
