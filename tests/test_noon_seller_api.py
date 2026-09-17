"""The Seller Center catalog adapter: everything that needs no browser, and the crawl it drives."""
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from noon_store.adapters.excel_repository import ExcelListingRepository
from noon_store.adapters.noon_seller_api import (BRAND_SEPARATOR, SEPARATOR, Account, _brand_groups,
                                                 _categories, _filters, _project, _sort, _store_code,
                                                 _fetch_profile, _to_product, landed_project,
                                                 load_accounts, profile_for_project, remember_projects)
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
            found = load_accounts(path=kept, pattern=pattern, seed={})
        self.assertEqual([account.label for account in found],
                         ["noon_seller_profile", "noon_seller_profile_2", "noon_seller_profile_x"])

    def test_an_account_nobody_signed_into_is_not_offered(self):
        with signed_in() as (pattern, kept, _):
            self.assertEqual(load_accounts(path=kept, pattern=pattern, seed={}), ())

    def test_an_account_keeps_the_projects_last_discovered_in_it(self):
        with signed_in("noon_seller_profile_2",
                       remembered={"noon_seller_profile_2": ["PRJ55555"]}) as (pattern, kept, _):
            found = load_accounts(path=kept, pattern=pattern, seed={})
        self.assertEqual(found[0].projects, ("PRJ55555",))

    def test_a_new_account_starts_with_no_projects_rather_than_being_skipped(self):
        with signed_in("noon_seller_profile_2") as (pattern, kept, _):
            found = load_accounts(path=kept, pattern=pattern, seed={})
        self.assertEqual((found[0].label, found[0].projects), ("noon_seller_profile_2", ()))

    def test_a_seeded_profile_knows_the_projects_noon_cannot_list(self):
        with signed_in("noon_seller_profile") as (pattern, kept, home):
            found = load_accounts(path=kept, pattern=pattern,
                                  seed={os.path.join(home, "noon_seller_profile"): ("PRJ19740",)})
        self.assertEqual(found[0].projects, ("PRJ19740",))

    def test_discovering_a_project_leaves_the_other_accounts_alone(self):
        with signed_in("noon_seller_profile", "noon_seller_profile_2",
                       remembered={"noon_seller_profile": ["PRJ19740"]}) as (pattern, kept, home):
            remember_projects(os.path.join(home, "noon_seller_profile_2"), ["PRJ55555"], path=kept)
            found = {account.label: account.projects
                     for account in load_accounts(path=kept, pattern=pattern, seed={})}
        self.assertEqual(found, {"noon_seller_profile": ("PRJ19740",),
                                 "noon_seller_profile_2": ("PRJ55555",)})


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


if __name__ == "__main__":
    unittest.main()
