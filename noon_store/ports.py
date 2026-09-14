"""Interfaces the use cases need from the outside world. Adapters implement them; use cases never import adapters."""
from __future__ import annotations

from typing import Callable, ContextManager, Optional, Protocol, Sequence

from .domain import CatalogPage, CatalogQuery, StoreListing, StoreRef


class CatalogGateway(Protocol):
    """Read access to one store's product catalog."""
    page_size: int   # products per page
    max_pages: int   # deepest page the catalog serves for a single query
    batch_size: int  # how many pages are worth requesting in one fetch_pages call

    def fetch_pages(self, queries: Sequence[CatalogQuery]) -> list[CatalogPage]:
        """The page for each query, in the same order. The pages may be fetched concurrently."""


# Opens a catalog session for a store; the session is released when the `with` block ends.
OpenCatalog = Callable[[StoreRef], ContextManager[CatalogGateway]]


class ListingRepository(Protocol):
    """Where store listings are kept. Locations are opaque strings owned by the repository."""

    def find(self, store: StoreRef) -> Optional[str]:
        """Location of the saved listing for `store`, or None."""

    def load(self, location: str) -> StoreListing:
        """Raises StoreError if `location` isn't a saved store listing."""

    def save(self, listing: StoreListing, location: Optional[str] = None) -> str:
        """Save to `location` (or a new location when None) and return where it was saved."""
