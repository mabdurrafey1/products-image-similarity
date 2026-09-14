"""Store listing domain: entities, value objects and business rules. Pure Python, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Iterable
from urllib.parse import urlparse


class StoreError(Exception):
    """A problem the user can act on; the message is shown to them as-is."""


class StopRequested(StoreError):
    def __init__(self):
        super().__init__("Stopped by user.")


@dataclass(frozen=True)
class StoreRef:
    """A public noon store, identified by the link the user pasted."""
    url: str     # link as entered, e.g. https://www.noon.com/uae-en/p-19740/?limit=50
    site: str    # https://www.noon.com
    locale: str  # uae-en
    path: str    # p-19740

    @classmethod
    def parse(cls, url: str) -> StoreRef:
        url = str(url).strip()
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = parsed.netloc.lower()
        if host != "noon.com" and not host.endswith(".noon.com"):
            raise StoreError(f"Not a noon.com link: {url}")
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            raise StoreError(f"The link doesn't point to a noon store page: {url}")
        return cls(url=url, site=f"{parsed.scheme}://{parsed.netloc}", locale=parts[0], path="/".join(parts[1:]))

    @property
    def country(self) -> str:
        return self.locale.split("-")[0]  # "uae"

    @property
    def key(self) -> str:
        """Identifies the store regardless of the link's language or extra parameters."""
        return f"{self.country}/{self.path}"


@dataclass(frozen=True)
class Product:
    sku: str
    title: str
    brand: str
    price: float | None
    link: str
    image_urls: tuple[str, ...] = ()
    added_on: str = ""  # YYYY-MM-DD the product first appeared in the listing


@dataclass(frozen=True)
class Category:
    code: str  # full category path, e.g. "electronics-and-mobiles/mobiles-and-accessories"
    children: tuple[Category, ...] = ()


def subcategories(tree: Iterable[Category], code: str | None) -> tuple[Category, ...]:
    """Direct children of category `code` in the tree (the top level when `code` is None)."""
    if code is None:
        return tuple(tree)
    for node in tree:
        if node.code == code:
            return node.children
        found = subcategories(node.children, code)
        if found:
            return found
    return ()


@dataclass(frozen=True)
class CatalogQuery:
    """One page of products from a store, optionally narrowed to a category and price range."""
    category: str | None = None
    price_min: int | None = None  # inclusive
    price_max: int | None = None  # inclusive
    sort_by: str = "new_arrivals"
    sort_dir: str = "desc"
    page: int = 1


@dataclass(frozen=True)
class CatalogPage:
    total: int                        # products matching the query across all pages
    products: tuple[Product, ...]     # products on this page
    store_name: str = ""
    price_range: tuple[float, float] | None = None  # cheapest and dearest matching product
    categories: tuple[Category, ...] = ()           # categories of the matching products


@dataclass
class StoreListing:
    """The saved products of one store, newest arrivals first."""
    store: StoreRef
    name: str
    products: list[Product]
    fetched_at: datetime    # last full fetch
    refreshed_at: datetime  # last full fetch or new-arrivals refresh

    @classmethod
    def fetched(cls, store: StoreRef, name: str, products: Iterable[Product], when: datetime,
                previous: StoreListing | None = None) -> StoreListing:
        """A listing from a full fetch. Products already in `previous` keep the date they were first seen."""
        first_seen = {p.sku: p.added_on for p in previous.products} if previous else {}
        today = when.date().isoformat()
        stamped = [replace(p, added_on=first_seen.get(p.sku) or today) for p in products]
        return cls(store, name, stamped, fetched_at=when, refreshed_at=when)

    def skus(self) -> set[str]:
        return {p.sku for p in self.products}

    def add_new_arrivals(self, products: Iterable[Product], when: datetime) -> list[Product]:
        """Put products that aren't listed yet at the top, dated `when`. Returns the ones added."""
        known = self.skus()
        today = when.date().isoformat()
        added = []
        for product in products:
            if product.sku not in known:
                known.add(product.sku)
                added.append(replace(product, added_on=today))
        self.products[:0] = added
        self.refreshed_at = when
        return added
