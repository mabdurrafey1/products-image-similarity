"""noon Seller Center catalog adapter: reads a store's own products from noon's JSON APIs.

The public store pages serve at most 10 pages of 200 products and carry no partner SKU. Seller Center
answers the same catalog as JSON, 100 records a page and 100 pages deep, with the partner SKU on every
record -- so a store is read from the account that owns it rather than scraped from its public pages.

Each account is signed into once, by hand, in a Chrome profile of its own (noon_seller_stores lists them,
one account per profile directory). A store is read through the account that owns it, which the store's
project code names.

Chrome is the fallback, not the way in. The session a sign-in proves is written down once (see
`seller_session`) and every fetch and refresh after that runs on a plain HTTP client carrying those
cookies -- no browser, no window, nothing to hide. Chrome opens only when no session has been written
down for the account, or when noon refuses the one that was: a 401 or 403 is the single signal that it
has lapsed, and nothing here tries to guess at that in advance. What is saved is the user's own session,
sent back to the host that issued it and to no other.

When Chrome is opened it is headed and hidden -- off the edge of the screen where that works, and
minimised on macOS, which ignores where a window is put. Headless is not an option: Seller Center drops
the connection outright (ERR_HTTP2_PROTOCOL_ERROR) rather than answer a browser that announces itself as
headless, and the only way around that would be to lie about what it is. Signing in stays visible,
because only a person can do it.

The cap on one query is worked around exactly as the use cases already do it, by narrowing: `family` is
offered to the crawler as a top-level category and each family's `brand` facet as its children. Prices
are not offered (`price_range` is None), so the crawler narrows by category alone.
"""
from __future__ import annotations

import glob
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence
from urllib.parse import quote

from ..domain import CatalogPage, CatalogQuery, Category, Product, StoreError, StoreRef
from .window import hide_window
# One spelling of a profile directory, and one saved session per account. Both live in seller_session,
# which imports nothing from here: a profile is keyed the same way whether an account or a session is
# being filed under it, and two definitions of that would drift apart exactly once and cost an account
# its projects. Re-exported, since this module is where callers have always found it.
from .seller_session import DENIED, _canonical, forget_session, load_session, save_session

PROFILE_DIR_ENV = "NOON_PROFILE"
HEADED_ENV = "NOON_HEADED"   # set to watch a fetch on-screen; otherwise its window is hidden as it opens
OFFSCREEN = ["--window-position=-32000,-32000", "--window-size=1440,900"]
DEFAULT_PROFILE = "~/noon_seller_profile"
# One Chrome profile directory per noon account, however many there are: a profile is one cookie jar, so
# two accounts cannot share one. Signing into a new directory is the whole of adding an account.
PROFILE_GLOB = "~/noon_seller_profile*"
ACCOUNTS_FILE = "~/.noon_seller_accounts.json"   # which projects each profile held when it was last read
HOST = "https://noon-catalog.noon.partners"
BASE = HOST + "/_vs/mp/mp-noon-catalog-api-rocket/"
LIST_API = BASE + "offer/list/noon"
FACETS_API = BASE + "offer/facets/noon"
STORES_API = HOST + "/_vs/mp/mp-noon-merchant-api/noon-store/list"
# Seller Center's own project directory, the call its toolbar makes to draw the project picker: posted
# with no payload and no project scope, it answers with every project the signed-in account holds.
PROJECTS_API = "https://toolbar.noon.partners/_svc/mp-partner-platform/project/list"
ENDPOINT = "offer/list/noon"      # the request whose headers and body carry the app's context
CDN = "https://f.nooncdn.com/p/"

PAGE_SIZE = 100    # the API's own maximum; more is silently served as 20
MAX_PAGES = 100    # page 100 already returns nothing: the 10,000-record cap
BATCH_SIZE = 20    # queries per fetch_pages call; a batch is also how often a fetch can report progress
# Measured against the account: noon answers about 74 requests at full speed (roughly 6 a second) and then
# grants about 2 a second. It limits the rate, not the number in flight -- asking for fewer at a time once
# it starts refusing only reads slower, because the requests already in flight are what take up the rate as
# noon frees it. So the number in flight never changes; a refused request waits a moment and asks again.
CONCURRENCY = 8        # requests in flight, before and after the fast allowance is spent
GAP = 0                # milliseconds a worker waits after a request; none is needed at this concurrency
PAUSE = 1000           # milliseconds to wait before asking again for whatever was refused
ATTEMPTS = 15          # tries at one refused request before giving up on it
REQUEST_TIMEOUT = 60  # seconds one request may take
READY_TIMEOUT = 45    # seconds to wait for the catalog page to make its first request

# Only headers a page is allowed to set are worth sending; the rest are the browser's own.
SEND_HEADERS = re.compile(r"^(x-|content-type$|accept$)")
# The crawler's sort names, mapped to the orderings the API actually honours. Measured: offer_price,
# stock, gmv and units_sold reorder results; views, price and net_stock are ignored by the API.
SORTS = {("new_arrivals", "desc"): ("", ""), ("new_arrivals", "asc"): ("units_sold", "desc"),
         ("price", "asc"): ("offer_price", "asc"), ("price", "desc"): ("offer_price", "desc")}
SEPARATOR = "||"   # in a category code: "family||brand", or "family||brand<US>brand" for a group of brands
BRAND_SEPARATOR = "\x1f"   # between the brands of one group; no brand name contains it
GROUP_FILL = 0.9   # how full a group of brands may be packed, as a share of the cap
COUNTRIES = {"uae": "AE", "ksa": "SA", "egypt": "EG"}

# Runs inside the signed-in Seller Center page: posts the bodies, `concurrency` at a time.
_POST_JS = """
async ({url, bodies, headers, concurrency, timeout, gap}) => {
    const results = new Array(bodies.length);
    let next = 0;
    async function worker() {
        while (next < bodies.length) {
            const i = next++;
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), timeout);
            try {
                const response = await fetch(url, {
                    method: 'POST', credentials: 'include', headers: headers,
                    body: JSON.stringify(bodies[i]), signal: controller.signal});
                results[i] = {status: response.status,
                              data: response.status === 200 ? await response.json() : null};
            } catch (e) {
                results[i] = {status: 0, error: String(e)};
            } finally {
                clearTimeout(timer);
            }
            if (gap) await new Promise(r => setTimeout(r, gap));
        }
    }
    await Promise.all(Array.from({length: Math.min(concurrency, bodies.length)}, worker));
    return Array.from(results, r => r || {status: 0, error: 'no result'});
}
"""


@dataclass(frozen=True)
class Account:
    """One noon account: the Chrome profile it is signed into, and the projects known to live in it."""
    profile: str                     # the profile directory; one account per directory
    projects: tuple[str, ...] = ()   # empty until a session of its own says what it owns

    @property
    def label(self) -> str:
        """What the account is called in a message: the name of its profile directory."""
        return os.path.basename(self.profile.rstrip("/"))


def _remembered(path: str) -> dict:
    """The projects discovered in each profile so far; nothing at all before the first discovery."""
    try:
        with open(os.path.expanduser(path)) as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(found, dict):
        return {}
    # One spelling per profile, whatever spelling it was written down in: a file written on Windows
    # keys its profiles the way that run happened to spell them.
    return {_canonical(profile): projects for profile, projects in found.items()}


def load_accounts(path: str = ACCOUNTS_FILE, pattern: str = PROFILE_GLOB) -> tuple[Account, ...]:
    """Every account somebody has signed into, in directory order.

    No account is written into the source. An account with no projects yet is still an account: what it
    holds is asked of noon the next time it is read, and written down only so a fetch between reads
    knows which profile owns a store.
    """
    known = {_canonical(profile): tuple(projects)
             for profile, projects in _remembered(path).items()}
    return tuple(Account(profile=directory, projects=known.get(_canonical(directory), ()))
                 for directory in sorted(glob.glob(os.path.expanduser(pattern)))
                 if os.path.isdir(directory))


def remember_projects(profile: str, projects: Sequence[str], path: str = ACCOUNTS_FILE) -> None:
    """Write down which projects a profile holds, so a later fetch knows which account owns a store."""
    found = _remembered(path)
    found[_canonical(profile)] = list(projects)
    with open(os.path.expanduser(path), "w") as handle:
        json.dump(found, handle, indent=2)


def forget_profile(profile: str, path: str = ACCOUNTS_FILE) -> None:
    """Drop what was written down about a profile that turned out not to be an account of its own.

    A note left behind would be inherited by whoever signs into that directory name next, and a store
    of theirs would then be fetched through somebody else's session.
    """
    found = _remembered(path)
    if found.pop(_canonical(profile), None) is None:
        return
    with open(os.path.expanduser(path), "w") as handle:
        json.dump(found, handle, indent=2)


def profile_for_project(project: str, accounts: Sequence[Account] = ()) -> str:
    """The profile signed into the account that owns a project.

    Refusing beats guessing: fetching a store from the wrong account reads somebody else's catalog.
    """
    for account in accounts or load_accounts():
        if project in account.projects:
            return account.profile
    raise StoreError(f"No signed-in noon account holds {project}. Use Load Stores to sign into the "
                     f"account that owns this store.")


def landed_project(headers: Mapping[str, str]) -> str:
    """The project an account's own catalog asked for -- how an account nobody configured names itself."""
    for name, value in headers.items():
        if name.lower() == "x-project":
            return value
    return ""


def account_projects(ask: Callable[[], object]) -> tuple[str, ...]:
    """Every project the signed-in account holds, as noon itself lists them.

    Only the code field is read. Project names are free text, and a real account holds one named after
    another project's code and one named after an email address -- so matching codes out of the text
    would attach a store to the wrong account and read its catalog through somebody else's session.
    """
    answer = ask() or {}
    listed = answer.get("projects") if isinstance(answer, Mapping) else None
    return tuple(str(project["projectCode"]) for project in (listed or [])
                 if isinstance(project, Mapping) and project.get("projectCode"))


def projects_held(account: Account, ask: Callable[[], object], headers: Mapping[str, str],
                  remember: Callable[[str, Sequence[str]], None] = remember_projects) -> tuple[str, ...]:
    """Which projects an account reads, and writing them down for the fetches that follow.

    noon's own listing is the answer, asked afresh every time: what was written down last time can only
    be out of date, and an account that gained a project would otherwise never be seen to hold it. The
    project the catalog landed on is the fallback, for the account whose listing is unavailable -- it
    yields the one project already known to work rather than nothing at all.
    """
    try:
        listed = account_projects(ask)
    except Exception:
        listed = ()                      # an account that won't list is not an account without projects
    landed = landed_project(headers)
    projects = listed or ((landed,) if landed else ())
    if projects:
        remember(account.profile, projects)
    return projects


def _store_code(store: StoreRef) -> str:
    """The Seller Center code of a public store link: p-19740 in the UAE is STR19740-NAE."""
    digits = re.search(r"\d+", store.path or "")
    if not digits:
        raise StoreError(f"'{store.path}' doesn't look like a noon store page.")
    return f"STR{digits.group(0)}-N{COUNTRIES.get(store.country, 'AE')}"


def _project(store: StoreRef) -> str:
    """A store's project shares its number: p-19740 belongs to PRJ19740."""
    digits = re.search(r"\d+", store.path or "")
    return f"PRJ{digits.group(0)}" if digits else ""


def _fetch_profile(store: StoreRef, override: str = "", accounts: Sequence[Account] = ()) -> str:
    """The profile a fetch opens: the one signed into the account that owns the store.

    A store is read through its owner's session. Opening whichever account came first would read a
    catalog that isn't this store's, so an unowned store is refused rather than guessed at.
    """
    if override:
        return os.path.expanduser(override)
    return profile_for_project(_project(store), accounts)


def _image_url(raw: str) -> str:
    return CDN + quote(raw, safe="/") + ".jpg?format=avif&width=800" if raw else ""


def _filters(base: dict, category: Optional[str]) -> dict:
    """The API filters for a category code: nothing, a family, or a family and one of its brands."""
    filters = dict(base)
    if not category:
        return filters
    family, _, brand = category.partition(SEPARATOR)
    if family:
        filters["family"] = [family]
    if brand:
        filters["brand"] = brand.split(BRAND_SEPARATOR)   # the API takes a list, so a group costs one request
    return filters


def _sort(query: CatalogQuery) -> tuple[str, str]:
    return SORTS.get((query.sort_by, query.sort_dir), ("", ""))


def _to_product(hit: dict, locale: str) -> Optional[Product]:
    sku = hit.get("csku_parent") or hit.get("zsku_child") or hit.get("catalog_sku") or ""
    if not sku:
        return None
    content = hit.get("content") or {}
    offer = hit.get("offer_code") or ""
    image = _image_url(content.get("image") or "")
    price = hit.get("price")
    return Product(
        sku=sku,
        title=content.get("title") or "",
        brand=content.get("brand") or "",
        price=float(price) if price is not None else None,
        link=f"https://www.noon.com/{locale}/{sku}/p/" + (f"?o={offer}" if offer else ""),
        image_urls=(image,) if image else (),
        psku=str(hit.get("partner_sku") or ""),
    )


def _categories(category: Optional[str], facets: dict) -> tuple[Category, ...]:
    """The narrowings offered for a page: the families of the store, or the brands within a family."""
    if not category:
        return tuple(Category(bucket["key"]) for bucket in facets.get("family") or [] if bucket.get("key"))
    family = category.partition(SEPARATOR)[0]
    groups = _brand_groups(facets.get("brand") or [], PAGE_SIZE * MAX_PAGES)
    brands = tuple(Category(f"{family}{SEPARATOR}{BRAND_SEPARATOR.join(group)}") for group in groups)
    return (Category(family, brands),) if brands else ()


def _brand_groups(buckets: list, cap: int) -> list[list[str]]:
    """Brands packed into as few groups as fit under the cap, largest first.

    A family over the cap is split by brand, but asking for one brand at a time costs a request per brand
    even where a hundred brands share a few hundred products. The API filters on a list of brands, so the
    brands are packed into groups instead: a family of 129 brands becomes two or three requests."""
    named = [(b["key"], int(b.get("doc_count") or 0)) for b in buckets if b.get("key")]
    named.sort(key=lambda pair: pair[1], reverse=True)
    room = max(1, int(cap * GROUP_FILL))
    groups: list[list[str]] = []
    sizes: list[int] = []
    for brand, count in named:
        for i, size in enumerate(sizes):
            if size + count <= room:
                groups[i].append(brand)
                sizes[i] = size + count
                break
        else:
            groups.append([brand])
            sizes.append(count)
    return groups


class _Denied(StoreError):
    """noon refused the saved session. Answerable: Chrome opens once and replaces it."""


class _SavedSessionCaller:
    """Makes the account's own requests with no browser at all, carrying the saved session's cookies.

    The page used to make these itself, which meant a fetch could not start without twelve seconds of
    Chrome. What the page actually contributed was its cookies: the requests are ordinary POSTs. So they
    are made here instead, from the same cookies, and Chrome is not involved in a fetch at all.

    The user agent is the one the browser sent when the session was saved. That is not a disguise: it is
    the client the session belongs to, and sending a different one would describe the request falsely.
    """

    def __init__(self, cookies: Sequence[Mapping], user_agent: str = ""):
        import requests   # imported late, like playwright: only a fetch needs it

        self._session = requests.Session()
        for cookie in cookies:
            name, value = cookie.get("name"), cookie.get("value")
            if name and value is not None:
                self._session.cookies.set(name, value, domain=cookie.get("domain") or "",
                                          path=cookie.get("path") or "/")
        if user_agent:
            self._session.headers["User-Agent"] = user_agent

    def post_all(self, url: str, bodies: list, headers: Mapping[str, str],
                 concurrency: int, timeout: int) -> list:
        """Post every body, `concurrency` in flight, in the shape the in-page version answered in.

        noon limits the rate, not the number in flight, so the requests go out together exactly as they
        did from the page; a refusal comes back as its status for the caller to ask again about.
        """
        def post(body):
            try:
                answer = self._session.post(url, json=body, headers=dict(headers), timeout=timeout)
                return {"status": answer.status_code,
                        "data": answer.json() if answer.status_code == 200 else None}
            except Exception as error:
                return {"status": 0, "error": _first_line(error)}

        if not bodies:
            return []
        with ThreadPoolExecutor(max_workers=min(concurrency, len(bodies))) as pool:
            return list(pool.map(post, bodies))

    def get_json(self, url: str, headers: Mapping[str, str], timeout: int) -> tuple:
        answer = self._session.get(url, headers=dict(headers), timeout=timeout)
        if answer.status_code in DENIED:
            raise _Denied("The saved noon session has lapsed.")
        return answer.status_code, (answer.json() if answer.status_code == 200 else None)

    def close(self) -> None:
        self._session.close()


class SellerBrowser:
    """One Chrome per signed-in account, shared by every store read through that account.

    Starting Chrome is almost the whole cost of reading a store: the requests themselves take under two
    seconds, the launch about twelve. A caller that reads several stores holds one of these for the whole
    run, so each account's browser is started once and every one of its stores is read through the page
    that is already signed in. A profile is one cookie jar, so accounts still get a browser each.
    """

    def __init__(self, launch: Optional[Callable[[str], tuple]] = None):
        self._launch = launch or self._start_chrome
        self._open: dict[str, tuple] = {}
        self._context_of: dict[str, dict] = {}
        self._playwright = None

    def __enter__(self) -> "SellerBrowser":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def page_for(self, profile: str) -> tuple:
        """The signed-in context and page for this profile, starting Chrome only the first time."""
        if profile not in self._open:
            self._open[profile] = self._launch(profile)
        return self._open[profile]

    def context_for(self, profile: str, capture: Callable[[], dict]) -> dict:
        """The headers and body template this account's stores send, captured only the first time.

        Opening the catalog to capture them costs about six seconds, and the only thing that differs
        between two stores of one account is the project the headers name -- which the caller swaps in
        for itself. So the account captures once and every store after the first is read at once.
        """
        if profile not in self._context_of:
            # Stored only once the capture has worked: a failed one must not leave the stores after it
            # holding an empty context, which would fail the whole account rather than the one store.
            self._context_of[profile] = capture()
        return self._context_of[profile]

    def _start_chrome(self, profile: str) -> tuple:
        from playwright.sync_api import sync_playwright

        if not os.path.isdir(profile) or not os.listdir(profile):
            raise StoreError("No noon Seller Center session yet. Use Load Stores to sign in once, "
                             "then fetch the store again.")
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        headed = bool(os.environ.get(HEADED_ENV))
        try:
            # A real browser, off the edge of the screen -- see the note at the top on why not headless.
            context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=profile, channel="chrome", headless=False, viewport=None,
                args=["--start-maximized"] if headed else OFFSCREEN)
        except Exception as e:
            # Chrome refuses a profile a second browser already has, rather than share it.
            if "ProcessSingleton" in str(e) or "SingletonLock" in str(e):
                raise StoreError("A noon fetch is already running. Wait for it to finish and try again "
                                 "-- two at once would corrupt the signed-in profile.") from e
            raise
        page = context.pages[0] if context.pages else context.new_page()
        if not headed:
            # macOS ignores --window-position, so an unwanted window has to be minimised to go away
            hide_window(page)
        return context, page

    def close(self) -> None:
        """Let go of every browser, so each profile keeps the session it was holding.

        Chrome writes the session back to the profile only on a clean exit, so one browser that refuses
        to close must not strand the rest -- that would cost those accounts their sign-in."""
        try:
            for context, _ in self._open.values():
                try:
                    context.close()
                except Exception:
                    pass
            self._open.clear()
        finally:
            if self._playwright:
                self._playwright.stop()
                self._playwright = None


class NoonSellerApiCatalog:
    """CatalogGateway reading one store from the signed-in Seller Center account.

    Use it as a context manager. Given a `browser`, it reads the store through that -- the caller owns it
    and every other store it reads shares the one Chrome. Given none, it opens a browser of its own and
    closes it, keeping the session, on exit.
    """
    page_size = PAGE_SIZE
    max_pages = MAX_PAGES
    batch_size = BATCH_SIZE

    def __init__(self, store: StoreRef, log: Optional[Callable[[str], None]] = None,
                 browser: Optional[SellerBrowser] = None, name: str = ""):
        self.store = store
        self.log = log or (lambda message: None)
        # The name the account already gave for this store, if the caller has it: asking noon again costs
        # about a second per store to learn what Load Stores has already read and saved.
        self.known_name = name
        self.store_name = ""
        self._browser = browser        # the caller's, to be left open
        self._own_browser = None       # this store's own, to be closed with it
        self._context = self._page = None
        self._caller = None            # the saved session's client, when no browser was needed
        self._concurrency = CONCURRENCY
        self._gap = GAP
        self._throttled = False   # whether the fast allowance has already been reported as spent
        self._headers: dict = {}
        self._base_body: dict = {}
        self._base_filters: dict = {}

    def __enter__(self) -> "NoonSellerApiCatalog":
        profile = _fetch_profile(self.store, os.environ.get(PROFILE_DIR_ENV, ""))

        # The saved session first, always: a fetch that can be served from it never opens Chrome.
        if self._start_from_saved(profile):
            return self

        # Either the caller's browser, shared with every other store it reads, or one just for this store.
        # Going through a SellerBrowser either way keeps starting Chrome in one place.
        self._own_browser = None if self._browser else SellerBrowser()
        browser = self._browser or self._own_browser
        try:
            self._context, self._page = browser.page_for(profile)
            self._open_catalog()
            # Asked for only when nobody could tell us: a blank name is missing information, not an answer.
            self.store_name = self.known_name or self._name_of_store()
        except BaseException:
            self.__exit__()
            raise
        return self

    def _start_from_saved(self, profile: str) -> bool:
        """Set this store up to read through the saved session, or say that it cannot be.

        The session is proved before it is trusted, by the one call that names the store: a session
        checked only when the first page of products is asked for would fail a fetch already under way
        instead of quietly falling back to Chrome here.
        """
        saved = load_session(profile) or {}
        if not saved.get("body") or not (saved.get("state") or {}).get("cookies"):
            return False   # nothing saved, or saved by the stores path, which captures no request body

        caller = _SavedSessionCaller((saved["state"] or {}).get("cookies") or [],
                                     saved.get("user_agent") or "")
        try:
            self._caller = caller
            self._headers = {**saved["headers"], "x-project": _project(self.store)}
            self._base_body = dict(saved["body"])
            self._base_body["noon_store_code"] = _store_code(self.store)
            self._base_filters = dict(self._base_body.get("filters") or {})
            self.store_name = self._name_of_store()
            return True
        except _Denied:
            # Expected: noon's cookies last about an hour. Not reported as trouble -- Chrome opens once
            # below, and saves a session in its place.
            forget_session(profile)
        except Exception:
            # A saved session that cannot be used for any other reason is not worth failing over either.
            forget_session(profile)
        self._caller = None
        caller.close()
        self._headers, self._base_body, self._base_filters = {}, {}, {}
        return False

    def __exit__(self, *exc_info) -> None:
        self._headers.clear()   # drop the captured context as soon as it is done with
        self._context = self._page = None
        if self._caller:
            self._caller.close()
            self._caller = None
        # A borrowed browser belongs to the caller and stays open for the next store; only one opened
        # for this store alone is closed here, which is what lets its profile keep the session.
        if self._own_browser:
            self._own_browser.close()
            self._own_browser = None

    def fetch_pages(self, queries: Sequence[CatalogQuery]) -> list[CatalogPage]:
        """The page for each query. The requests are made concurrently from inside the page."""
        totals = self._post_all(LIST_API, [self._body(query) for query in queries])

        # Facets are only worth asking for where the crawler will have to narrow: a page over the cap.
        over = [i for i, data in enumerate(totals) if (data.get("total") or 0) > self.page_size * self.max_pages]
        facets: dict[int, dict] = {}
        if over:
            replies = self._post_all(FACETS_API, [self._body(queries[i], page=1) for i in over])
            facets = dict(zip(over, replies))

        pages = []
        for i, (query, data) in enumerate(zip(queries, totals)):
            products = tuple(p for p in (_to_product(hit, self.store.locale) for hit in data.get("hits") or []) if p)
            pages.append(CatalogPage(
                total=int(data.get("total") or 0),
                products=products,
                store_name=self.store_name,
                price_range=None,   # the API narrows by family and brand, not by price
                categories=_categories(query.category, facets.get(i, {})),
            ))
        return pages

    def _post_all(self, url: str, bodies: list[dict]) -> list[dict]:
        """Post every body and return each answer's data, asking again for the ones noon refuses.

        noon refuses requests that come too fast (429, and 403 when it has been pushed harder), so a
        refusal is not a failure: the store is read more slowly instead of less completely."""
        results: list[dict] = [{}] * len(bodies)
        pending = list(range(len(bodies)))
        refusals = 0
        for attempt in range(ATTEMPTS):
            answers = self._post(url, [bodies[i] for i in pending])
            refused, denied = [], 0
            for i, answer in zip(pending, answers):
                status = int(answer.get("status") or 0)
                if status == 200:
                    results[i] = ((answer.get("data") or {}).get("data") or {})
                else:
                    refused.append(i)
                    denied += status in DENIED
            # A saved session can lapse in the middle of a long store: noon's cookies last about an hour
            # and a big store takes minutes. Refusing everything with a 401 is not throttling, and asking
            # again fifteen times would end in a message blaming a rate limit for an expired login. The
            # session is dropped instead, so the next fetch opens Chrome and signs back in.
            if self._caller and refused and denied == len(refused):
                forget_session(_fetch_profile(self.store, os.environ.get(PROFILE_DIR_ENV, "")))
                raise StoreError("The saved noon session expired while the store was being read. "
                                 "Fetch again -- it will sign in and carry on.")
            pending = refused
            if not pending:
                if refusals and not self._throttled:
                    self._throttled = True   # it stays spent for the rest of the store; saying so once is enough
                    self.log("  (noon's fast allowance is spent; the rest is read at the pace noon grants)")
                return results
            refusals += len(pending)
            # The fast allowance is spent, so noon is granting only a couple a second now. Pause briefly and
            # ask again for what it refused: a refusal returns at once and costs nothing, and keeping the
            # requests in flight is what takes up the rate as noon frees it.
            self._pause(PAUSE)
        raise StoreError(f"noon kept refusing {len(pending)} request(s) even after slowing right down. "
                         f"Try again in a few minutes; if it persists, use Load Stores to sign in again.")

    def _body(self, query: CatalogQuery, page: Optional[int] = None) -> dict:
        sort, direction = _sort(query)
        body = dict(self._base_body)
        body.update({"page": page or query.page, "per_page": self.page_size,
                     "filters": _filters(self._base_filters, query.category),
                     "sort": sort, "direction": direction})
        return body

    def _pause(self, milliseconds: int) -> None:
        """Wait, whether or not there is a page to wait in."""
        if self._page:
            self._page.wait_for_timeout(milliseconds)
        else:
            time.sleep(milliseconds / 1000)

    def _post(self, url: str, bodies: list[dict]) -> list[dict]:
        if not bodies:
            return []
        if self._caller:
            return self._caller.post_all(url, bodies, self._headers,
                                         self._concurrency, REQUEST_TIMEOUT)
        try:
            return self._page.evaluate(_POST_JS, {
                "url": url, "bodies": bodies, "headers": self._headers,
                "concurrency": self._concurrency, "timeout": REQUEST_TIMEOUT * 1000, "gap": self._gap})
        except Exception as e:
            raise StoreError(f"The Seller Center page stopped answering ({_first_line(e)}). Try again.") from e

    def _open_catalog(self) -> None:
        """Give this store the context its account's stores send, capturing it if nobody has yet.

        Capturing costs about six seconds and every store of an account used to pay it, though the only
        thing that differs between them is the project -- which is swapped in here. The account captures
        once; every store after the first starts reading at once.
        """
        profile = _fetch_profile(self.store, os.environ.get(PROFILE_DIR_ENV, ""))
        captured = (self._browser or self._own_browser).context_for(profile, self._load_context)

        self._headers = {**captured["headers"], "x-project": _project(self.store)}
        self._base_body = dict(captured["body"])
        self._base_body["noon_store_code"] = _store_code(self.store)   # the store asked for, not the page's own
        self._base_filters = dict(self._base_body.get("filters") or {})

    def _load_context(self) -> dict:
        """Open the account's catalog and capture the context its own requests carry."""
        captured: dict = {}

        def on_request(request):
            if ENDPOINT in request.url and request.method == "POST" and "headers" not in captured:
                import json
                captured["headers"] = {k: v for k, v in request.headers.items() if SEND_HEADERS.match(k.lower())}
                try:
                    captured["body"] = json.loads(request.post_data or "{}")
                except Exception:
                    captured["body"] = {}

        self._page.on("request", on_request)
        url = (f"{HOST}/en/catalog?project={_project(self.store)}"
               f"&tab=noon&live_status=true&page=1&limit={self.page_size}")
        def load():
            """Open the catalog and wait for the app to ask for its first page of offers."""
            try:
                self._page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            except Exception:
                pass   # a slow load still fires the request the context comes from
            deadline = self._page.evaluate("Date.now()") + READY_TIMEOUT * 1000
            while "headers" not in captured and self._page.evaluate("Date.now()") < deadline:
                self._page.wait_for_timeout(500)

        try:
            load()
            if "login" in self._page.url:
                raise StoreError("The noon Seller Center session has expired. Use Load Stores to sign in again.")
            if "headers" not in captured:
                # The catalog is a single-page app and now and then it just doesn't start -- seen on the first
                # fetch after another browser had only moments earlier let go of the profile. A second load
                # costs seconds; giving up throws away the whole store for a slow boot.
                load()
            if "headers" not in captured:
                raise StoreError("Seller Center didn't load its catalog, so the store couldn't be read.")
        finally:
            # The page outlives this store now that a whole account shares one, so the listener has to go
            # with the store that added it -- left on, every later store would add another.
            self._page.remove_listener("request", on_request)

        # Written down the moment it is known good, so this is the last fetch that needs a browser.
        try:
            save_session(_fetch_profile(self.store, os.environ.get(PROFILE_DIR_ENV, "")),
                         self._context.storage_state(), captured["headers"], captured["body"],
                         self._page.evaluate("navigator.userAgent"))
        except Exception as error:
            self.log(f"  (the session couldn't be saved: {_first_line(error)})")

        return {"headers": captured["headers"], "body": captured["body"]}

    def _name_of_store(self) -> str:
        """The account's own name for this store, used for the listing's file name.

        On the saved-session path this is also what proves the session: a refusal here is raised rather
        than swallowed, so the caller can open Chrome instead. Any other trouble still falls back to the
        store's own path, which names the file well enough.
        """
        code = _store_code(self.store)
        headers = {**self._headers, "x-project": _project(self.store)}
        try:
            if self._caller:
                status, body = self._caller.get_json(STORES_API, headers, REQUEST_TIMEOUT)
            else:
                response = self._context.request.get(STORES_API, headers=headers, timeout=60_000)
                status, body = response.status, (response.json() if response.status == 200 else None)
            if status != 200:
                return self.store.path
            for raw in (body or {}).get("noon_stores") or []:
                if raw.get("noon_store_code") == code:
                    return ((raw.get("name_locale") or {}).get("name_en") or "").strip() or self.store.path
        except _Denied:
            raise
        except Exception:
            pass
        return self.store.path


def _first_line(error: Exception) -> str:
    return (str(error).strip().splitlines() or [type(error).__name__])[0]
