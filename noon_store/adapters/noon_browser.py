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
from .window import hide_window

BROWSERS = (("chrome", "Google Chrome"), ("msedge", "Microsoft Edge"))
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    # noon rejects headless browsers, so the window is hidden instead of done without: off the edge of the
    # screen on Windows and Linux, and minimised on macOS, which ignores this flag (see window.hide_window)
    "--window-position=-32000,-32000",
    "--window-size=1280,900",
    "--blink-settings=imagesEnabled=false",  # nothing looks at the page, so its images needn't load
    # Chrome slows the timers and work of windows nobody sees, which would stall the off-screen page
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]
CONCURRENCY = 8     # store pages loading at the same time; noon rate-limits (429) at 12
BATCH_SIZE = 24     # pages per fetch_pages call: a few rounds of CONCURRENCY
PAGE_TIMEOUT = 45   # seconds one page may take to load
READY_TIMEOUT = 30  # seconds to wait, after opening the store, for noon's bot check to let its pages through
ATTEMPTS = 6
MIN_CONCURRENCY = 2
RATE_LIMIT_WAITS = (5, 10, 20, 30, 45)  # seconds to wait after noon refuses pages for coming too fast (429)

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
    // A page that stalls must not hang the whole fetch: stop waiting after every round could have timed out
    const deadline = timeout * Math.ceil(urls.length / concurrency) + 5000;
    await Promise.race([
        Promise.all(Array.from({length: Math.min(concurrency, urls.length)}, worker)),
        new Promise(resolve => setTimeout(resolve, deadline)),
    ]);
    next = urls.length;  // stop the workers from starting more pages
    return Array.from(results, r => r || {status: 0, error: 'timed out'});
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
        self._concurrency = CONCURRENCY  # lowered while noon rate-limits

    def __enter__(self) -> NoonBrowserCatalog:
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._launch()
            self._page = self._browser.new_page()
            self._minimize()
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
            problems, rate_limited = [], False
            for i, result in zip(missing, self._load([urls[i] for i in missing])):
                if result.get("status") != 200:
                    rate_limited = rate_limited or result.get("status") == 429
                    problems.append(result.get("error") or f"status {result.get('status')}")
                    continue
                try:
                    pages[i] = read_page(result.get("data") or "", self.store)
                except PageDataError as e:
                    problems.append(str(e))
            missing = [i for i in missing if pages[i] is None]
            if not missing:
                return pages
            if attempt == ATTEMPTS:
                break
            if rate_limited:
                # noon is asking for fewer requests: load fewer pages at once from now on and wait before retrying
                self._concurrency = max(MIN_CONCURRENCY, self._concurrency // 2)
                wait = RATE_LIMIT_WAITS[min(attempt, len(RATE_LIMIT_WAITS)) - 1]
                self.log(f"  noon asked to slow down ({len(missing)} page(s) refused); waiting {wait}s and "
                         f"continuing {self._concurrency} pages at a time...")
                time.sleep(wait)
                continue
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

    def _minimize(self):
        """Minimize the window: macOS moves windows placed off-screen back into view."""
        hide_window(self._page)

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
            return self._page.evaluate(_LOAD_JS, {"urls": urls, "concurrency": self._concurrency,
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
