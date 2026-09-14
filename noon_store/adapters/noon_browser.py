"""noon.com catalog adapter: reads a store's pages through a hidden Chrome or Edge window.

noon's bot protection (Akamai) serves its pages only to a real browser, so the store is opened in the Chrome or Edge
already installed, in a window placed off-screen, and the store's other pages are loaded from inside that page,
several at a time. The products are read from the data noon embeds in each page (see page_data). Public pages only;
no login.
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Sequence
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

from ..domain import CatalogPage, CatalogQuery, StoreError, StoreRef
from .page_data import PageDataError, read_page

BROWSERS = (("chrome", "Google Chrome"), ("msedge", "Microsoft Edge"))
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--window-position=-32000,-32000",  # off-screen: noon rejects headless browsers, but nobody needs to see it
    "--window-size=1280,900",
    "--blink-settings=imagesEnabled=false",  # nothing looks at the page, so its images needn't load
]
CONCURRENCY = 8     # store pages loading at the same time
BATCH_SIZE = 24     # pages per fetch_pages call: a few rounds of CONCURRENCY
PAGE_TIMEOUT = 45   # seconds one page may take to load
READY_TIMEOUT = 30  # seconds to wait, after opening the store, for noon's bot check to let its pages through
ATTEMPTS = 3

# Runs inside the store page. Loads the given pages of the store CONCURRENCY at a time and returns, for each, the
# part from the catalog data to the end of its script: about a quarter of the page, to keep the transfer small.
_LOAD_JS = """
async ({urls, concurrency, timeout}) => {
    const results = new Array(urls.length);
    let next = 0;
    async function worker() {
        while (next < urls.length) {
            const i = next++;
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), timeout);
            try {
                const response = await fetch(urls[i], {credentials: 'include', signal: controller.signal});
                const text = await response.text();
                const start = text.search(/[{,]nbHits:/);
                const end = start < 0 ? -1 : text.indexOf('</script>', start);
                results[i] = {status: response.status, data: start < 0 ? '' : text.slice(start, end < 0 ? text.length : end)};
            } catch (e) {
                results[i] = {status: 0, error: String(e)};
            } finally {
                clearTimeout(timer);
            }
        }
    }
    await Promise.all(Array.from({length: Math.min(concurrency, urls.length)}, worker));
    return results;
}
"""


class NoonBrowserCatalog:
    """CatalogGateway for one noon store. Use it as a context manager; the browser closes on exit."""
    page_size = 200  # most products a store page lists
    max_pages = 10   # noon serves no deeper than page 10 of a listing
    batch_size = BATCH_SIZE

    def __init__(self, store: StoreRef, log: Optional[Callable[[str], None]] = None):
        self.store = store
        self.log = log or (lambda message: None)
        self._playwright = self._browser = self._page = None

    def __enter__(self) -> NoonBrowserCatalog:
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._launch()
            self._page = self._browser.new_page()
            self._open_store()
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc_info) -> None:
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass  # the window may already be gone
        finally:
            self._playwright.stop()

    def fetch_pages(self, queries: Sequence[CatalogQuery]) -> list[CatalogPage]:
        urls = [self._page_url(query) for query in queries]
        pages: list[Optional[CatalogPage]] = [None] * len(urls)
        missing = list(range(len(urls)))
        for attempt in range(1, ATTEMPTS + 1):
            problems = []
            for i, result in zip(missing, self._load([urls[i] for i in missing])):
                if result.get("status") != 200:
                    problems.append(result.get("error") or f"status {result.get('status')}")
                    continue
                try:
                    pages[i] = read_page(result.get("data") or "", self.store)
                except PageDataError as e:
                    problems.append(str(e))
            missing = [i for i in missing if pages[i] is None]
            if not missing:
                return pages
            if attempt < ATTEMPTS:
                self.log(f"  {len(missing)} page(s) didn't load ({problems[0]}); reopening the store and retrying...")
                time.sleep(3 * attempt)
                try:
                    self._open_store()
                except Exception:
                    pass  # the next attempt reports the failure
        raise StoreError(f"noon.com kept refusing to load the store's pages ({problems[0]}). Try again later.")

    def _launch(self):
        problems = []
        for channel, name in BROWSERS:
            try:
                return self._playwright.chromium.launch(channel=channel, headless=False, args=BROWSER_ARGS)
            except Exception as e:
                problems.append(f"{name}: {_first_line(e)}")
        raise StoreError("Fetching noon stores needs Google Chrome or Microsoft Edge installed.\n" + "\n".join(problems))

    def _open_store(self):
        """Open the store and wait until noon's bot check, which runs in the page, lets the store's pages through.
        Until then noon answers with pages that hold no products."""
        self._page.goto(f"{self.store.site}/{self.store.locale}/{self.store.path}/", wait_until="domcontentloaded",
                        timeout=60_000)
        probe = [self._page_url(CatalogQuery())]
        deadline = time.monotonic() + READY_TIMEOUT
        while not self._load(probe)[0].get("data") and time.monotonic() < deadline:
            self._page.wait_for_timeout(1000)

    def _load(self, urls: list[str]) -> list[dict]:
        try:
            return self._page.evaluate(_LOAD_JS, {"urls": urls, "concurrency": CONCURRENCY,
                                                  "timeout": PAGE_TIMEOUT * 1000})
        except Exception as e:  # the store page crashed or navigated away
            return [{"status": 0, "error": _first_line(e)}] * len(urls)

    def _page_url(self, query: CatalogQuery) -> str:
        """A page of the store, e.g. /uae-en/p-19740/electronics-and-mobiles/?limit=200&page=2&sort%5Bby%5D=..."""
        path = "/".join(part.strip("/") for part in (self.store.locale, self.store.path, query.category) if part)
        params = {"limit": self.page_size, "page": query.page, "sort[by]": query.sort_by, "sort[dir]": query.sort_dir}
        if query.price_min is not None:
            params["f[price][min]"] = query.price_min
        if query.price_max is not None:
            params["f[price][max]"] = query.price_max
        return f"/{path}/?{urlencode(params)}"


def _first_line(error: Exception) -> str:
    return (str(error).strip().splitlines() or [type(error).__name__])[0]
