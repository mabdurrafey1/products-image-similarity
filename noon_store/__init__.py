"""Keep Excel listings of public noon.com stores for the duplicate finder to search.

Layers, with dependencies pointing inwards only:
    domain.py      entities and business rules (pure Python)
    ports.py       interfaces the use cases need from the outside world
    use_cases.py   FetchStore and RefreshStore
    adapters/      noon.com through a hidden browser; Excel files
This module wires the default adapters into the use cases for callers such as the GUI.
"""
import os
from contextlib import contextmanager

from .adapters.excel_repository import ExcelListingRepository
from .adapters.noon_seller_api import NoonSellerApiCatalog, SellerBrowser
from .domain import StopRequested, StoreError, StoreRef
from .use_cases import FetchResult, FetchStore, RefreshResult, RefreshStore, RefreshStores

__all__ = ["FetchResult", "RefreshResult", "StopRequested", "StoreError",
           "fetch_store", "refresh_store", "refresh_stores", "find_listing", "is_listing"]


def _catalog_opener(log):
    return lambda store, name="": NoonSellerApiCatalog(store, log, name=name)


@contextmanager
def _catalog_session(log):
    """One browser for a whole run: every store is read through it, and it closes on the way out."""
    with SellerBrowser() as browser:
        yield lambda store, name="": NoonSellerApiCatalog(store, log, browser=browser, name=name)


def fetch_store(store_url, directory, log=print, on_progress=None, should_stop=None) -> FetchResult:
    """Fetch every product of the store at `store_url` into an Excel listing in `directory`."""
    use_case = FetchStore(_catalog_opener(log), ExcelListingRepository(directory), log, on_progress, should_stop)
    return use_case.execute(store_url)


def refresh_store(location, log=print, should_stop=None) -> RefreshResult:
    """Add the new arrivals of a saved store to its Excel listing."""
    repository = ExcelListingRepository(os.path.dirname(location))
    return RefreshStore(_catalog_opener(log), repository, log, should_stop=should_stop).execute(location)


def refresh_stores(locations, log=print, should_stop=None) -> list:
    """Add the new arrivals of several saved listings, reading them all through one browser.

    Starting the browser is almost the whole cost of a refresh, so refreshing stores one at a time paid
    for a start each. Returns a RefreshResult per listing that could be read; one that could not is
    reported and skipped rather than costing the rest their refresh."""
    # A refresh loads and saves each workbook by its own full path, so the repository's folder is never used.
    repository = ExcelListingRepository("")
    return RefreshStores(lambda: _catalog_session(log), repository, log, should_stop=should_stop).execute(locations)


def find_listing(store_url, directory):
    """Path of the saved listing for this store link, or None. Raises StoreError for a link that isn't a store."""
    return ExcelListingRepository(directory).find(StoreRef.parse(store_url))


def is_listing(location) -> bool:
    """True if the Excel file was created by fetch_store, and so can be refreshed."""
    return ExcelListingRepository.is_listing(location)
