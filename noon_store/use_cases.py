"""Use cases: fetch a whole store, or add its new arrivals. They reach noon and storage only through ports."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Callable, Iterable, Optional, Sequence

from .domain import CatalogPage, CatalogQuery, Product, StopRequested, StoreError, StoreListing, StoreRef, subcategories
from .ports import CatalogGateway, ListingRepository, OpenCatalog

# Extra orderings that reach more products when one category at one price exceeds the per-query cap.
# Cheapest-first and dearest-first come first because they are opposites: between them they reach everything
# up to two capfuls, so nothing further is asked for.
FALLBACK_SORTS = (("price", "asc"), ("price", "desc"), ("new_arrivals", "asc"))
MIN_BATCH = 10  # pages to fetch at once when hunting the last few products


@dataclass(frozen=True)
class FetchResult:
    location: str
    store_name: str
    product_count: int


@dataclass(frozen=True)
class RefreshResult:
    location: str
    store_name: str
    added: int
    product_count: int


class _Crawler:
    """Pages through a store's catalog, working around the cap on how many results one query can reach.
    Pages are requested in batches, so the catalog can fetch several at once."""

    def __init__(self, catalog: CatalogGateway, log: Callable[[str], None], should_stop: Callable[[], bool],
                 on_progress: Optional[Callable[[int, int], None]] = None, known: Iterable[str] = ()):
        self.catalog = catalog
        self.log = log
        self.should_stop = should_stop
        self.on_progress = on_progress
        self.expected_total = 0
        self.requests = 0
        self.budget: Optional[int] = None  # most pages a crawl may request
        self.seen = set(known)
        self.found: list[Product] = []  # products not seen before, in the order received

    @property
    def cap(self) -> int:
        return self.catalog.page_size * self.catalog.max_pages

    def fetch(self, queries: Sequence[CatalogQuery]) -> list[CatalogPage]:
        """Fetch the pages batch by batch, keeping their products as they arrive."""
        pages: list[CatalogPage] = []
        size = max(1, self.catalog.batch_size)
        for start in range(0, len(queries), size):
            if self.should_stop():
                raise StopRequested()
            batch = queries[start:start + size]
            self.requests += len(batch)
            if self.budget is not None and self.requests > self.budget:
                # A guard against hammering noon if its store pages stop narrowing by price or category
                raise StoreError("Stopped: this store needs far more page loads than expected. noon may have "
                                 "changed its store pages; please report this.")
            for page in self.catalog.fetch_pages(batch):
                self.keep(page)
                pages.append(page)
            if self.expected_total and len(batch) > 1:
                self.log(f"  {len(self.found):,} of {self.expected_total:,} products "
                         f"({self.requests} pages read)")
        return pages

    def keep(self, page: CatalogPage) -> None:
        """Keep the page's products that weren't seen before."""
        for product in page.products:
            if product.sku not in self.seen:
                self.seen.add(product.sku)
                self.found.append(product)
        if self.on_progress and self.expected_total:
            self.on_progress(len(self.found), self.expected_total)

    def page_count(self, page: CatalogPage) -> int:
        return min(math.ceil(page.total / self.catalog.page_size), self.catalog.max_pages)

    def by_orders(self, query: CatalogQuery, first: CatalogPage) -> bool:
        """Read a store that is bigger than one query reaches, without narrowing it.

        Cheapest-first and dearest-first each reach their own capful from opposite ends, so together they
        cover anything up to two capfuls. That costs about one request per page of products, where narrowing
        by category costs a first page per category on top. Returns whether everything was found."""
        if first.total > 2 * self.cap:
            return False  # too big for two orderings to meet in the middle; it has to be narrowed instead
        self.log(f"  Reading all {first.total:,} products in price order "
                 f"(~{math.ceil(first.total / self.catalog.page_size)} pages)...")
        self.budget = None  # bounded by the orderings and the cap, not by the store's total
        self.sweep(query)
        return bool(self.expected_total) and len(self.found) >= self.expected_total

    def crawl(self, query: CatalogQuery, first: CatalogPage) -> None:
        """Keep every product matching `query`, whose first page is `first`.

        While a query matches more products than the cap it is split, by price and then by category. Each round
        fetches in one go the first pages of the new parts and the remaining pages of the parts that fit."""
        if first.total > self.cap and self.by_orders(query, first):
            return  # read straight through; narrowing it would only cost more requests
        self.budget = self.requests + 3 * math.ceil(first.total / self.catalog.page_size) + 100
        level = [(query, first)]
        while level:
            parts: list[CatalogQuery] = []
            remaining: list[CatalogQuery] = []
            for part, page in level:
                if page.total <= self.cap:
                    if part.price_min is not None or part.category:
                        self.log(f"  {_describe(part)}: {page.total:,} products")
                    remaining += [replace(part, page=n) for n in range(2, self.page_count(page) + 1)]
                    continue
                split = self.split(part, page)
                if split:
                    parts += split
                else:
                    self.log(f"  Warning: {_describe(part)} has {page.total:,} products, more than noon serves for "
                             f"one search; fetching as many as other sort orders reach")
                    remaining += self.every_order(part)
            pages = self.fetch(parts + remaining)
            level = list(zip(parts, pages))

    def split(self, query: CatalogQuery, first: CatalogPage) -> list[CatalogQuery]:
        """Narrower queries that together match what `query` matches: price ranges while its prices can be divided,
        then its subcategories. Empty when it can't be narrowed."""
        low, high = query.price_min, query.price_max
        if first.price_range:  # skip the prices nothing is listed at
            cheapest, dearest = math.floor(first.price_range[0]), math.ceil(first.price_range[1])
            low = cheapest if low is None else max(low, cheapest)
            high = dearest if high is None else min(high, dearest)
        if low is not None and high is not None and high - low >= 2:
            count = min(high - low, 2 * math.ceil(first.total / self.cap))
            edges = [low + (high - low) * i // count for i in range(count + 1)]
            # Neighbouring ranges share their edge price so fractional prices are never skipped; repeats are
            # dropped by SKU
            return [replace(query, price_min=a, price_max=b) for a, b in zip(edges, edges[1:])]
        narrowed = replace(query, price_min=low, price_max=high)
        return [replace(narrowed, category=child.code) for child in subcategories(first.categories, query.category)]

    def every_order(self, query: CatalogQuery) -> list[CatalogQuery]:
        """The remaining pages of `query` plus every page of it in the fallback orders."""
        pages = [replace(query, page=n) for n in range(2, self.catalog.max_pages + 1)]
        for sort_by, sort_dir in FALLBACK_SORTS:
            pages += [replace(query, sort_by=sort_by, sort_dir=sort_dir, page=n)
                      for n in range(1, self.catalog.max_pages + 1)]
        return pages

    def sweep(self, query: CatalogQuery) -> None:
        """Reach products that narrowing never returned, by reading the store again in other orders.

        A split can come up short: noon files some products under no category of the facet the store was
        split by, so they sit in none of the parts. Reading the whole store in another order reaches them,
        each order serving its own capful. Stops as soon as the store's own count is accounted for."""
        if not self.expected_total or len(self.found) >= self.expected_total:
            return
        self.budget = None  # bounded by the orders and the cap, not by the store's total
        size = max(1, self.catalog.batch_size)
        for sort_by, sort_dir in FALLBACK_SORTS:
            ordered = replace(query, sort_by=sort_by, sort_dir=sort_dir)
            number = 1
            while number <= self.catalog.max_pages:
                missing = self.expected_total - len(self.found)
                if missing <= 0:
                    return
                # Never ask for more pages than the missing products could fill, but keep batches worth
                # fetching at once when only a handful are missing and they could be on any page
                pages = max(MIN_BATCH, math.ceil(missing / self.catalog.page_size))
                last = min(number + min(size, pages), self.catalog.max_pages + 1)
                self.fetch([replace(ordered, page=n) for n in range(number, last)])
                number = last

    def new_arrivals(self) -> None:
        """Keep products newer than the known ones, stopping at the first page with nothing new."""
        number = pages = 1
        while number <= pages:
            before = len(self.found)
            page = self.fetch([CatalogQuery(page=number)])[0]  # newest first
            pages = self.page_count(page)
            self.log(f"  Page {number}: {len(self.found) - before} new")
            if len(self.found) == before:
                return
            number += 1
        if page.total > self.cap:
            self.log(f"Warning: more than {self.cap:,} products are new since the last refresh; only the newest "
                     f"{self.cap:,} were added. Use Fetch Store to fetch everything.")


def _describe(query: CatalogQuery) -> str:
    parts = []
    if query.category:
        parts.append(query.category)
    if query.price_min is not None:
        parts.append(f"price {query.price_min}-{query.price_max}")
    return ", ".join(parts) or "the store"


def _duration(elapsed: timedelta) -> str:
    seconds = round(elapsed.total_seconds())
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


class _StoreUseCase:
    def __init__(self, open_catalog: OpenCatalog, repository: ListingRepository,
                 log: Callable[[str], None] = print,
                 on_progress: Optional[Callable[[int, int], None]] = None,
                 should_stop: Optional[Callable[[], bool]] = None,
                 clock: Callable[[], datetime] = datetime.now):
        self.open_catalog = open_catalog
        self.repository = repository
        self.log = log
        self.on_progress = on_progress
        self.should_stop = should_stop or (lambda: False)
        self.clock = clock


class FetchStore(_StoreUseCase):
    """Fetch every product of a store and save it as its listing (replacing a previously saved one)."""

    def execute(self, store_url: str) -> FetchResult:
        store = StoreRef.parse(store_url)
        started = self.clock()
        self.log(f"Opening noon store {store.path}...")
        with self.open_catalog(store) as catalog:
            crawler = _Crawler(catalog, self.log, self.should_stop, self.on_progress)
            first = crawler.fetch([CatalogQuery()])[0]
            if first.total == 0:
                raise StoreError("The store link returned no products.")
            name = first.store_name or store.path
            self.log(f"'{name}' lists {first.total:,} products.")
            crawler.expected_total = first.total
            crawler.crawl(CatalogQuery(), first)
            crawler.sweep(CatalogQuery())

        location = self.repository.find(store)
        listing = StoreListing.fetched(store, name, crawler.found, self.clock(), self._previous(location))
        location = self.repository.save(listing, location)
        self.log(f"Saved {len(listing.products):,} products to {location} "
                 f"({crawler.requests} pages in {_duration(self.clock() - started)}).")
        if len(crawler.found) > first.total:
            self.log("  (noon shows only some of a store's near-identical listings at a time, so a fetch can find "
                     "more products than the store's count)")
        return FetchResult(location, name, len(listing.products))

    def _previous(self, location: Optional[str]) -> Optional[StoreListing]:
        if not location:
            return None
        try:
            return self.repository.load(location)
        except StoreError:
            return None


class RefreshStore(_StoreUseCase):
    """Add the products a store listed since its listing was last fetched or refreshed."""

    def execute(self, location: str) -> RefreshResult:
        listing = self.repository.load(location)
        self.log(f"Checking '{listing.name}' for new arrivals ({len(listing.products):,} products saved)...")
        with self.open_catalog(listing.store) as catalog:
            crawler = _Crawler(catalog, self.log, self.should_stop, known=listing.skus())
            crawler.new_arrivals()

        added = listing.add_new_arrivals(crawler.found, self.clock())
        self.repository.save(listing, location)
        self.log(f"Added {len(added):,} new products to '{listing.name}' ({len(listing.products):,} total).")
        return RefreshResult(location, listing.name, len(added), len(listing.products))
