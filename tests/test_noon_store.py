"""Tests for the noon store use cases, page reading, page links and Excel listings. No browser or network: noon is
replaced by a fake.

Run with:  python -m unittest discover -s tests
"""
import copy
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from noon_store.adapters.excel_repository import ExcelListingRepository
from noon_store.adapters.noon_browser import NoonBrowserCatalog
from noon_store.adapters.page_data import IMAGE_URL, PageDataError, read_page
from noon_store.domain import (CatalogPage, CatalogQuery, Category, Product, StopRequested, StoreError, StoreListing,
                               StoreRef)
from noon_store.use_cases import FetchStore, RefreshStore

STORE_URL = "https://www.noon.com/uae-en/p-1/?limit=50"
JAN = datetime(2026, 1, 5, 9, 0)
FEB = datetime(2026, 2, 5, 9, 0)


def product(n, price=10.0):
    return Product(sku=f"SKU{n}", title=f"Product {n}", brand="Brand", price=price,
                   link=f"https://www.noon.com/uae-en/product-{n}/SKU{n}/p/",
                   image_urls=(f"https://img.test/{n}a.jpg", f"https://img.test/{n}b.jpg"), psku=f"P{n}")


def category_tree(paths):
    roots = {}
    for path in paths:
        level = roots
        parts = path.split("/")
        for depth in range(1, len(parts) + 1):
            level = level.setdefault("/".join(parts[:depth]), {})

    def build(level):
        return tuple(Category(code, build(children)) for code, children in level.items())
    return build(roots)


class FakeCatalog:
    """noon's catalog in memory, including its cap: pages past max_pages repeat the last one."""
    page_size = 3
    max_pages = 2
    batch_size = 4

    def __init__(self, entries):
        self.entries = entries  # (product, category path), newest first
        self.requests = 0
        self.batches = []  # size of each fetch_pages call

    def fetch_pages(self, queries):
        self.batches.append(len(queries))
        self.requests += len(queries)
        return [self.page(query) for query in queries]

    def matches(self, query, product, category):
        return ((query.category is None or category == query.category or category.startswith(query.category + "/"))
                and (query.price_min is None or product.price >= query.price_min)
                and (query.price_max is None or product.price <= query.price_max))

    def page(self, query):
        matching = [(p, c) for p, c in self.entries if self.matches(query, p, c)]
        ordered = [p for p, _ in matching]
        if query.sort_by == "price":
            ordered.sort(key=lambda p: p.price, reverse=query.sort_dir == "desc")
        elif query.sort_dir == "asc":
            ordered.reverse()
        start = (min(query.page, self.max_pages) - 1) * self.page_size
        prices = [p.price for p in ordered]
        return CatalogPage(total=len(ordered), products=tuple(ordered[start:start + self.page_size]),
                           store_name="Test Store", price_range=(min(prices), max(prices)) if prices else None,
                           categories=category_tree(c for _, c in matching))


class PriceBlindCatalog(FakeCatalog):
    """A catalog whose price filter does nothing, so splitting by price never narrows a search."""

    def matches(self, query, product, category):
        return super().matches(replace(query, price_min=None, price_max=None), product, category)


class ShortCatalog(FakeCatalog):
    """A catalog that answers with fewer products than it says the store holds.

    This is what a throttled or partly refused read looks like from the use case's side: noon's own count
    stands, but the products behind it never all arrive."""
    claimed = 6

    def page(self, query):
        return replace(super().page(query), total=self.claimed)


class MemoryRepository:
    def __init__(self):
        self.saved = {}

    def find(self, store):
        return next((location for location, listing in self.saved.items() if listing.store.key == store.key), None)

    def load(self, location):
        if location not in self.saved:
            raise StoreError("not a store listing")
        return copy.deepcopy(self.saved[location])

    def save(self, listing, location=None):
        location = location or f"memory:{listing.store.key}"
        self.saved[location] = copy.deepcopy(listing)
        return location


def opener(catalog, asked_with=None):
    @contextmanager
    def open_catalog(store, name=""):
        if asked_with is not None:
            asked_with.append((store.path, name))
        yield catalog
    return open_catalog


def quiet(message):
    pass


class FetchStoreTests(unittest.TestCase):
    def fetch(self, entries, repository=None, when=JAN):
        catalog = FakeCatalog(entries)
        repository = repository or MemoryRepository()
        result = FetchStore(opener(catalog), repository, log=quiet, clock=lambda: when).execute(STORE_URL)
        return result, repository.load(result.location), catalog

    def assert_complete(self, listing, entries):
        skus = [p.sku for p in listing.products]
        self.assertEqual(len(skus), len(set(skus)), "duplicate products")
        self.assertEqual(sorted(skus), sorted(p.sku for p, _ in entries))

    def test_small_store_reads_every_page(self):
        entries = [(product(n), "a") for n in range(5)]
        result, listing, catalog = self.fetch(entries)
        self.assert_complete(listing, entries)
        self.assertEqual((result.store_name, result.product_count), ("Test Store", 5))
        self.assertEqual(catalog.requests, 2)

    def test_store_over_the_cap_is_split_by_price(self):
        entries = [(product(n, price=10 + n), "a") for n in range(20)]
        self.assert_complete(self.fetch(entries)[1], entries)

    def test_pages_are_requested_in_batches(self):
        entries = [(product(n, price=10 + n), "a") for n in range(20)]
        _, listing, catalog = self.fetch(entries)
        self.assert_complete(listing, entries)
        self.assertTrue(all(size <= FakeCatalog.batch_size for size in catalog.batches), catalog.batches)
        self.assertLess(len(catalog.batches), catalog.requests / 2, catalog.batches)

    def test_crowded_price_is_split_by_category(self):
        categories = ["a/x"] * 4 + ["a/y"] * 3 + ["b"] * 3
        entries = [(product(n, price=5), c) for n, c in enumerate(categories)]
        self.assert_complete(self.fetch(entries)[1], entries)

    def test_overflow_that_cannot_be_split_uses_other_sort_orders(self):
        entries = [(product(n, price=5), "a") for n in range(8)]
        self.assert_complete(self.fetch(entries)[1], entries)

    def test_crawl_that_never_narrows_is_stopped(self):
        catalog = PriceBlindCatalog([(product(n, price=10 + n), "a") for n in range(20)])
        repository = MemoryRepository()
        with self.assertRaises(StoreError) as caught:
            FetchStore(opener(catalog), repository, log=quiet).execute(STORE_URL)
        self.assertNotIsInstance(caught.exception, StopRequested)
        self.assertLess(catalog.requests, 130)
        self.assertEqual(repository.saved, {})

    def test_progress_reaches_the_store_total(self):
        reports = []
        use_case = FetchStore(opener(FakeCatalog([(product(n, price=n), "a") for n in range(9)])),
                              MemoryRepository(), log=quiet, on_progress=lambda done, total: reports.append((done, total)))
        use_case.execute(STORE_URL)
        self.assertEqual(reports[-1], (9, 9))

    def test_refetch_keeps_the_date_products_were_first_seen(self):
        repository = MemoryRepository()
        first = [(product(n), "a") for n in range(3)]
        self.fetch(first, repository, when=JAN)
        _, listing, _ = self.fetch([(product(9), "a")] + first, repository, when=FEB)
        self.assertEqual({p.sku: p.added_on for p in listing.products},
                         {"SKU9": "2026-02-05", "SKU0": "2026-01-05", "SKU1": "2026-01-05", "SKU2": "2026-01-05"})
        self.assertEqual(len(repository.saved), 1)

    def test_empty_store_is_an_error(self):
        with self.assertRaises(StoreError):
            self.fetch([])

    def test_a_partial_read_does_not_replace_the_saved_listing(self):
        """A read that comes up far short of noon's own count must leave the good workbook alone.

        Losing products the workbook already holds is worse than a failed fetch: the fetch says so and can be
        run again, while a silent overwrite is only discovered when a search stops finding things."""
        repository = MemoryRepository()
        self.fetch([(product(n), "a") for n in range(6)], repository, when=JAN)
        catalog = ShortCatalog([(product(n), "a") for n in range(2)])
        use_case = FetchStore(opener(catalog), repository, log=quiet, clock=lambda: FEB)
        with self.assertRaises(StoreError) as caught:
            use_case.execute(STORE_URL)
        self.assertIn("2", str(caught.exception))
        saved = repository.load(repository.find(StoreRef.parse(STORE_URL)))
        self.assertEqual(len(saved.products), 6, "the partial read replaced the saved listing")

    def test_a_partial_read_is_saved_with_a_warning_when_nothing_is_saved_yet(self):
        """With no workbook to protect, some products beat none -- but the log must not call it complete."""
        messages = []
        catalog = ShortCatalog([(product(n), "a") for n in range(2)])
        repository = MemoryRepository()
        result = FetchStore(opener(catalog), repository, log=messages.append,
                            clock=lambda: JAN).execute(STORE_URL)
        self.assertEqual(result.product_count, 2)
        self.assertTrue([m for m in messages if "incomplete" in m.lower()], messages)

    def test_a_store_that_genuinely_shrank_still_replaces_the_saved_listing(self):
        """Delisting is not a partial read: noon's count and what arrives agree, so the fetch stands."""
        repository = MemoryRepository()
        self.fetch([(product(n), "a") for n in range(6)], repository, when=JAN)
        _, listing, _ = self.fetch([(product(0), "a")], repository, when=FEB)
        self.assertEqual([p.sku for p in listing.products], ["SKU0"])

    def test_stop_request_ends_the_fetch_without_saving(self):
        repository = MemoryRepository()
        use_case = FetchStore(opener(FakeCatalog([(product(1), "a")])), repository, log=quiet,
                              should_stop=lambda: True)
        with self.assertRaises(StopRequested):
            use_case.execute(STORE_URL)
        self.assertEqual(repository.saved, {})


class RefreshStoreTests(unittest.TestCase):
    def setUp(self):
        self.old = [product(n) for n in range(10)]
        self.repository = MemoryRepository()
        self.repository.saved["listing"] = StoreListing.fetched(StoreRef.parse(STORE_URL), "Test Store", self.old, JAN)

    def refresh(self, newest):
        catalog = FakeCatalog([(p, "a") for p in newest + self.old])
        result = RefreshStore(opener(catalog), self.repository, log=quiet, clock=lambda: FEB).execute("listing")
        return result, self.repository.load("listing"), catalog

    def test_adds_new_arrivals_on_top_and_stops_at_known_products(self):
        result, listing, catalog = self.refresh([product(21), product(20)])
        self.assertEqual((result.added, result.product_count), (2, 12))
        self.assertEqual([p.sku for p in listing.products[:3]], ["SKU21", "SKU20", "SKU0"])
        self.assertEqual([p.added_on for p in listing.products[:3]], ["2026-02-05", "2026-02-05", "2026-01-05"])
        self.assertEqual(listing.refreshed_at, FEB)
        self.assertEqual(catalog.requests, 2)  # page 1 had new products, page 2 had none

    def test_nothing_new_costs_one_request(self):
        result, listing, catalog = self.refresh([])
        self.assertEqual((result.added, len(listing.products), catalog.requests), (0, 10, 1))

    def test_file_that_is_not_a_listing_is_refused(self):
        with self.assertRaises(StoreError):
            RefreshStore(opener(FakeCatalog([])), MemoryRepository(), log=quiet).execute("elsewhere")


class RefreshStoresTests(unittest.TestCase):
    """Several listings refreshed through one browser.

    Starting Chrome is almost the whole cost of a refresh -- about twelve seconds against under two for the
    requests themselves -- so refreshing seven stores must not pay for seven starts."""

    def setUp(self):
        from noon_store.use_cases import RefreshStores
        self.RefreshStores = RefreshStores
        self.repository = MemoryRepository()
        self.locations = ["one", "two", "three"]
        for n, location in enumerate(self.locations):
            store = StoreRef.parse(f"https://www.noon.com/uae-en/p-1000{n}/")
            self.repository.saved[location] = StoreListing.fetched(store, f"Store {n}", [product(n)], JAN)
        self.sessions = 0

    def session_over(self, catalog, asked_with=None):
        """A browser the use case opens once and reads every store through."""
        @contextmanager
        def open_session():
            self.sessions += 1
            yield opener(catalog, asked_with)
        return open_session

    def run_refresh(self, catalog):
        return self.RefreshStores(self.session_over(catalog), self.repository,
                                  log=quiet, clock=lambda: FEB).execute(self.locations)

    def test_refreshing_several_listings_starts_one_browser(self):
        self.run_refresh(FakeCatalog([(product(99), "a")]))
        self.assertEqual(self.sessions, 1, "each store started a browser of its own")

    def test_every_listing_is_still_refreshed(self):
        results = self.run_refresh(FakeCatalog([(product(99), "a")]))
        self.assertEqual([r.added for r in results], [1, 1, 1])
        for location in self.locations:
            self.assertIn("SKU99", [p.sku for p in self.repository.load(location).products])

    def test_one_store_failing_does_not_cost_the_others_their_refresh(self):
        """A single bad listing must not throw away the browser and the stores after it."""
        self.locations = ["one", "missing", "three"]
        results = self.run_refresh(FakeCatalog([(product(99), "a")]))
        self.assertEqual(self.sessions, 1)
        self.assertEqual([r.location for r in results], ["one", "three"])

    def test_a_refresh_says_the_name_the_listing_already_holds(self):
        # The workbook knows what the store is called, so asking noon for its name again is a second per
        # store spent learning what we already had written down.
        asked = []
        self.RefreshStores(self.session_over(FakeCatalog([]), asked), self.repository,
                           log=quiet, clock=lambda: FEB).execute(self.locations)
        self.assertEqual([name for _, name in asked], ["Store 0", "Store 1", "Store 2"])


class StoreRefTests(unittest.TestCase):
    def test_parses_a_store_link(self):
        store = StoreRef.parse("https://www.noon.com/uae-en/p-19740/?isCarouselView=false&limit=50")
        self.assertEqual((store.site, store.locale, store.path, store.key),
                         ("https://www.noon.com", "uae-en", "p-19740", "uae/p-19740"))

    def test_link_without_scheme(self):
        self.assertEqual(StoreRef.parse("www.noon.com/saudi-en/p-1").key, "saudi/p-1")

    def test_rejects_other_sites_and_non_store_links(self):
        for url in ("https://www.amazon.ae/uae-en/p-1", "https://evilnoon.com/uae-en/p-1", "https://www.noon.com/uae-en/"):
            with self.assertRaises(StoreError, msg=url):
                StoreRef.parse(url)


# A store page as noon serves it, cut down: the data sits in a script as a seroval-serialized JavaScript value.
# AD1 is another seller's sponsored product, which noon mixes into store pages.
STORE_PAGE = (
    r'<html><script>self.$R=self.$R||{};x={nav:$R[1]=[$R[2]={name:"Phones",filter:"category"}],'
    r'nbHits:3,nbHitsText:"3",facets:$R[3]=[$R[4]={code:"price",data:$R[5]={max:30.5,min:9}},'
    r'$R[6]={code:"category",data:$R[7]=[$R[8]={code:"home",children:$R[9]=[$R[10]={code:"home/cups",'
    r'children:$R[11]=[]}]}]},$R[12]={code:"partner",data:$R[13]=[$R[14]={name:"Tiger \x26 Co",code:"p_1",'
    r'count:3},{name:"Acme",code:"p_2",count:0}]}],'
    r'search:$R[15]={at:new Date("2026-01-01"),sort:$R[16]={by:"new_arrivals"}},'
    r'hits:$R[17]=[$R[18]={sku:"S1",name:"Café \"Mug\" 😀",brand:"Tiger",price:12,sale_price:9.5,'
    r'url:"cafe-mug",offer_code:"o1",plp_specifications:$R[19]={"Pack Quantity":"Single"},'
    r'image_keys:$R[20]=["k/1","k/2"],is_buyable:!0,show_3d:!1,listed:new Date("2026-01-01"),extra:void 0},'
    r'$R[21]={sku:"S2",name:"Plate",brand:"Tiger",price:30.5,sale_price:null,url:"plate",image_keys:$R[20]},'
    r'$R[22]={sku:"",name:"No SKU"},$R[23]={sku:"AD1",name:"Cable",brand:"Acme",price:4997,store_name:"Acme",'
    r'is_ad:!0}]}</script></html>'
)


class PageDataTests(unittest.TestCase):
    def test_reads_the_products_and_filters_of_a_store_page(self):
        page = read_page(STORE_PAGE, StoreRef.parse(STORE_URL))
        self.assertEqual((page.total, page.store_name, page.price_range), (3, "Tiger & Co", (9, 30.5)))
        self.assertEqual(page.categories, (Category("home", (Category("home/cups"),)),))
        images = (IMAGE_URL.format("k/1"), IMAGE_URL.format("k/2"))
        self.assertEqual(page.products, (
            Product("S1", 'Café "Mug" 😀', "Tiger", 9.5, "https://www.noon.com/uae-en/cafe-mug/S1/p/?o=o1", images),
            Product("S2", "Plate", "Tiger", 30.5, "https://www.noon.com/uae-en/plate/S2/p/", images),
        ))

    def test_page_without_catalog_data_is_an_error(self):
        store = StoreRef.parse(STORE_URL)
        for html in ("<html>Access Denied</html>", STORE_PAGE[:STORE_PAGE.index("$R[21]")]):
            with self.assertRaises(PageDataError, msg=html[-40:]):
                read_page(html, store)


class NoonBrowserCatalogTests(unittest.TestCase):
    def test_page_links_carry_the_query(self):
        catalog = NoonBrowserCatalog(StoreRef.parse(STORE_URL))
        self.assertEqual(catalog._page_url(CatalogQuery()),
                         "/uae-en/p-1/?limit=200&page=1&sort%5Bby%5D=new_arrivals&sort%5Bdir%5D=desc")
        self.assertEqual(
            catalog._page_url(CatalogQuery(page=3, category="electronics/cables", price_min=10, price_max=20)),
            "/uae-en/p-1/electronics/cables/?limit=200&page=3&sort%5Bby%5D=new_arrivals&sort%5Bdir%5D=desc"
            "&f%5Bprice%5D%5Bmin%5D=10&f%5Bprice%5D%5Bmax%5D=20")


class ExcelListingRepositoryTests(unittest.TestCase):
    def test_round_trip_in_a_file_the_search_pipeline_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            repository = ExcelListingRepository(folder)
            store = StoreRef.parse(STORE_URL)
            listing = StoreListing.fetched(store, "Test: Store", [product(1, price=12.5), product(2, price=None)], JAN)
            location = repository.save(listing)

            self.assertEqual(os.path.basename(location), "Noon - Test Store (uae p-1).xlsx")
            self.assertEqual(os.listdir(folder), [os.path.basename(location)])  # no temp file left behind
            self.assertEqual(repository.find(StoreRef.parse("https://www.noon.com/uae-ar/p-1/")), location)
            self.assertTrue(ExcelListingRepository.is_listing(location))

            loaded = repository.load(location)
            self.assertEqual(loaded.products, listing.products)
            self.assertEqual((loaded.store, loaded.name, loaded.fetched_at, loaded.refreshed_at),
                             (store, "Test: Store", JAN, JAN))

            first_sheet = pd.read_excel(location)  # what the search pipeline and report read
            for column in ("sku", "PartnerSKU", "Product Title", "Main Image URL", "Product Link",
                           "Combined_All_Image_URLs", "Price"):
                self.assertIn(column, first_sheet.columns)
            self.assertEqual(first_sheet.loc[0, "PartnerSKU"], "P1")  # the search pipeline reads it as psku
            self.assertEqual(first_sheet.loc[0, "Combined_All_Image_URLs"], "https://img.test/1a.jpg;https://img.test/1b.jpg")

    def test_plain_excel_file_is_not_a_listing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "plain.xlsx")
            pd.DataFrame({"sku": ["A"]}).to_excel(path, index=False)
            self.assertFalse(ExcelListingRepository.is_listing(path))
            with self.assertRaises(StoreError):
                ExcelListingRepository(folder).load(path)


if __name__ == "__main__":
    unittest.main()
