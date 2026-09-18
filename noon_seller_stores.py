"""The stores of the signed-in noon Seller Center account, read from its own API.

The store box used to offer three links typed into the source. This asks the account instead: Seller
Center lists a project's stores at `noon-store/list`, and the store's number is the same one the public
store page uses (STR19740-NAE -> p-19740), so each store still resolves to the listing files the rest
of the program already keeps.

Signing in happens once per account, by hand, in that account's own Chrome profile; the session then
persists there. A profile is one cookie jar, so no two accounts can share one: each keeps its own
directory, any number of them may exist, and making a directory and signing into it is the whole of
adding an account -- which is all Add Account does.

Chrome is opened once per account and not again. The session it proves is written down (see
`seller_session`) and every later read goes straight to the API with no browser at all -- seconds
instead of the six a launch costs, per account, every time. Chrome comes back only when there is
nothing written down for that account, or when noon refuses the saved session: a refusal is the one
signal that it has lapsed, and nothing here tries to guess at that ahead of time. What is written
down is the account's own session, and it is the user's own account: no cookie of anyone else's is
read, and none is replayed at a host that did not issue it.

No store or project is written into this source. Each account is asked what it holds, every time it is
read: Seller Center's own toolbar lists an account's projects at `project/list`, posted with no project
scope, and that listing is the only source of truth. It is written down afterwards so a later fetch of
one store knows which profile to open without a browser; an account whose listing is unavailable falls
back to the project its catalog landed on, which is the one project already known to work.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Optional, Sequence

# One definition of the accounts, of what each holds, and of "a window nobody has to see".
from noon_store.adapters.window import hide_window
from noon_store.adapters.noon_seller_api import (ACCOUNTS_FILE, OFFSCREEN, PROFILE_GLOB, PROJECTS_API,
                                                 Account, _canonical, forget_profile, load_accounts,
                                                 projects_held)
from noon_store.adapters.seller_session import DENIED, forget_session, load_session, save_session

COUNTRY = os.environ.get("NOON_COUNTRY", "AE")   # the accounts trade in the UAE only

CATALOG_HOST = "https://noon-catalog.noon.partners"
STORES_API = CATALOG_HOST + "/_vs/mp/mp-noon-merchant-api/noon-store/list"
ENDPOINT = "offer/list/noon"     # the request whose headers carry the app's context
DROP = {"content-length", "host", "connection", "accept-encoding"}
LOGIN_WAIT_MINUTES = float(os.environ.get("NOON_LOGIN_WAIT", "10"))
SEPARATOR = " — "   # between a store's name and the store it points at, and in an account's title
# noon's public store pages use a country slug; the account is UAE-only, the rest are here for safety.
LOCALES = {"AE": "uae-en", "SA": "ksa-en", "EG": "egypt-en"}


class SellerSessionError(Exception):
    """Raised when the Chrome profile has no usable Seller Center session."""


class SessionDenied(SellerSessionError):
    """noon turned the session away -- the one signal that it has lapsed.

    Told apart from its parent because it is answerable: a saved session that is refused sends us to
    Chrome once and is replaced, where any other trouble is reported as it stands.
    """


@dataclass(frozen=True)
class SellerStore:
    """One store of the account, as Seller Center describes it."""
    name: str          # TIGER
    code: str          # STR19740-NAE
    project: str       # PRJ19740
    country: str       # AE
    url: str           # https://www.noon.com/uae-en/p-19740/

    @property
    def label(self) -> str:
        """What the store box shows: the name the account gives it, and the store it points at."""
        return f"{self.name}{SEPARATOR}{self.path}"

    @property
    def path(self) -> str:
        return f"p-{_digits(self.code)}"


def _digits(store_code: str) -> str:
    match = re.search(r"\d+", store_code or "")
    return match.group(0) if match else ""


def _store_url(store_code: str, country: str) -> str:
    """The public store link for a Seller Center store code; its number is the store's number."""
    number = _digits(store_code)
    locale = LOCALES.get((country or "").upper(), "uae-en")
    return f"https://www.noon.com/{locale}/p-{number}/" if number else ""


def _to_store(raw: dict) -> Optional[SellerStore]:
    code = raw.get("noon_store_code") or ""
    country = (raw.get("country_code") or "").upper()
    url = _store_url(code, country)
    if not url:
        return None
    return SellerStore(
        name=((raw.get("name_locale") or {}).get("name_en") or "").strip() or f"p-{_digits(code)}",
        code=code,
        project=raw.get("project_code") or "",
        country=country,
        url=url,
    )


def fetch_stores(accounts: Optional[Sequence[Account]] = None, country: str = COUNTRY,
                 log: Callable[[str], None] = print,
                 wait_for_login: bool = True) -> list[SellerStore]:
    """Every active store of every signed-in account, by name.

    The accounts are read one at a time: a Chrome profile admits one browser, and two at once would
    corrupt the signed-in session. One account's trouble costs only its own stores.

    Raises SellerSessionError when nobody has signed in anywhere.
    """
    from playwright.sync_api import sync_playwright   # imported late: the GUI starts without it

    signed_in = tuple(accounts) if accounts is not None else load_accounts()
    if not signed_in:
        raise SellerSessionError(
            "No noon Seller Center account yet. Make the folder ~/noon_seller_profile -- one folder per "
            "account, so ~/noon_seller_profile_2 for a second -- and Load Stores opens a window to sign "
            "into each one.")

    groups: list[list[SellerStore]] = []
    with sync_playwright() as p:
        for account in signed_in:
            try:
                groups.append(_account_stores(p, account, country, log, wait_for_login))
            except SellerSessionError as error:
                if len(signed_in) == 1:
                    raise
                log(f"  {account.label}: {error}")   # the other accounts still have stores to give

    stores = _merge(groups)
    if not stores:
        raise SellerSessionError("No signed-in account listed an active store.")
    return stores


def _account_stores(playwright, account: Account, country: str,
                    log: Callable[[str], None], wait_for_login: bool) -> list[SellerStore]:
    """The active stores of one account -- from its saved session if it still holds, else Chrome."""
    saved = load_session(account.profile)
    if saved:
        api = playwright.request.new_context(storage_state=saved["state"])
        try:
            return _read_stores(api, saved["headers"], account, country, log)
        except SessionDenied:
            # The session lapsed. This is expected -- noon's cookies last about an hour -- so it is
            # not reported as trouble: Chrome opens once below and the saved session is replaced.
            forget_session(account.profile)
        finally:
            api.dispose()
    return _browser_stores(playwright, account, country, log, wait_for_login)


def _read_stores(api, headers: Mapping[str, str], account: Account, country: str,
                 log: Callable[[str], None]) -> list[SellerStore]:
    """Every active store the account holds, asked of the APIs through whatever context is given.

    The context is the whole difference between the two paths: a browser's, or one built from the
    saved session with no browser behind it. The questions asked of noon are identical.
    """
    stores: list[SellerStore] = []

    def list_projects():
        """Seller Center's own project directory: no payload and no project scope, so it answers
        with every project this account holds rather than the one the catalog happened to open."""
        answer = api.post(PROJECTS_API, headers=dict(headers), timeout=60_000)
        if answer.status in DENIED:
            # Checked here because this is the first call a lapsed session reaches: left to the
            # store list below, an expired cookie would be reported as an account that named no
            # projects, and Chrome would never be opened to put it right.
            raise SessionDenied(f"{account.label} is signed out. Sign in again to refresh its stores.")
        return answer.json() if answer.status == 200 else {}

    # Asked of noon every time, so an account that gained a project since last run holds it now.
    projects = projects_held(account, list_projects, headers)
    if not projects:
        raise SellerSessionError(f"{account.label} didn't say which projects it holds.")
    log(f"  {account.label} holds {', '.join(projects)}.")

    wanted = (country or "").upper()
    for project in projects:
        scoped = dict(headers)
        scoped["x-project"] = project     # the store list is scoped to one project at a time
        try:
            response = api.get(STORES_API, headers=scoped, timeout=60_000)
        except Exception as error:
            log(f"  {project}: {str(error)[:70]}")
            continue
        if response.status in DENIED:
            raise SessionDenied(f"{account.label} is signed out. Sign in again to refresh its stores.")
        if response.status != 200:
            log(f"  {project}: the store list answered {response.status}")
            continue
        for raw in (response.json() or {}).get("noon_stores") or []:
            if (raw.get("status_code") or "").upper() != "ACTIVE":
                continue
            if wanted and (raw.get("country_code") or "").upper() != wanted:
                continue
            store = _to_store(raw)
            if store:
                stores.append(store)
    return stores


def _browser_stores(playwright, account: Account, country: str,
                    log: Callable[[str], None], wait_for_login: bool) -> list[SellerStore]:
    """The active stores of one account, read through the profile that account is signed into.

    The one path that opens Chrome, and the only place a session is written down: whatever this
    proves is saved, so the next read of this account needs no browser.
    """
    captured: dict = {}      # in memory for this account only
    stores: list[SellerStore] = []

    def on_request(request):
        if ENDPOINT in request.url and request.method == "POST" and "headers" not in captured:
            captured["headers"] = {k: v for k, v in request.headers.items()
                                   if k.lower() not in DROP}

    # An account whose project nobody wrote down is opened bare: Seller Center then lands on a project
    # of its own, and the catalog names it in the requests it makes.
    url = (f"{CATALOG_HOST}/en/catalog?tab=noon&live_status=true&page=1&limit=100"
           + (f"&project={account.projects[0]}" if account.projects else ""))

    def open_browser(args):
        """A browser on this account's profile, pointed at the catalog. Off-screen unless somebody must see it."""
        browser = playwright.chromium.launch_persistent_context(
            user_data_dir=account.profile, channel="chrome", headless=False,
            viewport=None, args=list(args))
        opened = browser.pages[0] if browser.pages else browser.new_page()
        if "--start-maximized" not in args:
            # macOS ignores --window-position, so an unwanted window has to be minimised to go away
            hide_window(opened)
        opened.on("request", on_request)
        try:
            opened.goto(url, wait_until="domcontentloaded", timeout=90_000)
        except Exception:
            pass   # a slow load still fires the request the headers come from
        return browser, opened

    # An account that is already signed in needs no window; reading its stores shouldn't take over the screen.
    context, page = open_browser(OFFSCREEN)

    try:
        if "login" in page.url:
            if not wait_for_login:
                raise SellerSessionError(
                    f"{account.label} is signed out. Sign in again to refresh its stores.")
            context.close()   # nobody can sign into a window parked off the screen
            context, page = open_browser(["--start-maximized"])
            log(f"Sign into noon Seller Center for {account.label} in the window that opened "
                f"(waiting up to {LOGIN_WAIT_MINUTES:.0f} minutes).")
            deadline = time.time() + LOGIN_WAIT_MINUTES * 60
            while time.time() < deadline and "login" in page.url:
                page.wait_for_timeout(2000)
            if "login" in page.url:
                raise SellerSessionError(f"Nobody signed into {account.label}, so its stores were skipped.")
            log(f"Signed into {account.label}.")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            except Exception:
                pass

        deadline_headers = time.time() + 45
        while time.time() < deadline_headers and "headers" not in captured:
            page.wait_for_timeout(500)
        if "headers" not in captured:
            raise SellerSessionError(
                f"Seller Center didn't load its catalog for {account.label}, so its stores were skipped.")

        # Written down before the stores are read: this is the moment the session is known good, and
        # a project that answers badly afterwards is no reason to make the next run open Chrome again.
        try:
            save_session(account.profile, context.storage_state(), captured["headers"])
        except Exception as error:
            log(f"  {account.label}: the session couldn't be saved ({str(error)[:50]}).")

        stores = _read_stores(context.request, captured["headers"], account, country, log)
    finally:
        captured.clear()      # drop the captured context as soon as it is done with
        try:
            context.close()   # clean exit, so the profile keeps the session for next time
        except Exception:
            pass
    return stores


def _merge(groups: Iterable[Iterable[SellerStore]]) -> list[SellerStore]:
    """Every account's stores in one list, each store once, by name.

    The box offers stores, not accounts: which account owns one is the program's problem, not the user's.
    """
    merged: dict[str, SellerStore] = {}
    for group in groups:
        for store in group:
            merged.setdefault(store.code, store)
    return sorted(merged.values(), key=lambda store: store.name.lower())


def next_profile(pattern: str = PROFILE_GLOB) -> str:
    """The directory the next account signs into: the first one holding no session.

    A directory somebody made but never signed into is already asking for a sign-in on every Load
    Stores, so it is filled rather than stepped over.
    """
    base = os.path.expanduser(pattern.rstrip("*"))
    suffix, number = "", 1
    while _has_session(base + suffix):
        number += 1
        suffix = f"_{number}"
    return base + suffix


def _has_session(profile: str) -> bool:
    """Whether anything has been signed into this profile -- not whether that session still works."""
    return os.path.isdir(profile) and bool(os.listdir(profile))


def add_account(country: str = COUNTRY, log: Callable[[str], None] = print,
                read: Optional[Callable[..., list[SellerStore]]] = None,
                pattern: str = PROFILE_GLOB, accounts_file: str = ACCOUNTS_FILE) -> list[SellerStore]:
    """Sign into one more noon account, and return the stores it brings.

    The directory is kept only if the sign-in finished and reached an account nobody had added: a
    half-made one would ask to be signed into for ever after, and a second profile on an account
    already added would offer that account's stores a second time.
    """
    read = read or _read_account
    profile = next_profile(pattern)
    os.makedirs(profile, exist_ok=True)
    account = Account(profile=profile)
    try:
        stores = read(account, country, log)
        already = _added_before(profile, accounts_file, pattern)
        if already:
            raise SellerSessionError(f"That is the account already added as {already}. To add another, "
                                     f"sign into a different noon account.")
    except Exception:
        remove_account(profile, accounts_file)
        raise
    log(f"Added {account.label}, holding {len(stores)} stores.")
    return stores


def sign_in_account(profile: str, country: str = COUNTRY, log: Callable[[str], None] = print,
                    read: Optional[Callable[..., list[SellerStore]]] = None,
                    path: str = ACCOUNTS_FILE, pattern: str = PROFILE_GLOB) -> list[SellerStore]:
    """Open the window that signs an account already added back in, and read the stores it holds.

    The account asked for is the account opened, or none is: falling back to another profile would
    sign in as somebody else and then offer their stores under this account's name.
    """
    wanted = _canonical(profile)
    account = next((known for known in load_accounts(path, pattern)
                    if _canonical(known.profile) == wanted), None)
    if account is None:
        raise SellerSessionError("That account is no longer on this computer. Add it again to sign in.")
    return (read or _read_account)(account, country, log)


def _read_account(account: Account, country: str, log: Callable[[str], None]) -> list[SellerStore]:
    """One account's stores, in a browser of its own -- the window its sign-in happens in."""
    from playwright.sync_api import sync_playwright   # imported late: the GUI starts without it

    with sync_playwright() as playwright:
        return _account_stores(playwright, account, country, log, wait_for_login=True)


def _added_before(profile: str, accounts_file: str, pattern: str) -> str:
    """The account already added that holds the same project, when a sign-in repeated one."""
    accounts = {account.profile: set(account.projects)
                for account in load_accounts(accounts_file, pattern)}
    directory = os.path.expanduser(profile)
    mine = accounts.get(directory, set())
    for other, projects in accounts.items():
        if other != directory and mine & projects:
            return os.path.basename(other.rstrip("/"))
    return ""


def remove_account(profile: str, accounts_file: str = ACCOUNTS_FILE) -> None:
    """Remove an account for good: its Chrome profile directory, and the note of what it held.

    The directory has to go. Forgetting the note alone leaves a directory that still matches the
    profile glob, so the account comes back knowing nothing about itself and asks to be signed into
    on every Load Stores.
    """
    shutil.rmtree(os.path.expanduser(profile), ignore_errors=True)
    forget_profile(profile, accounts_file)
    # The saved session has to go with it, or it would be inherited by whoever signs into that
    # directory name next, and their stores read through cookies belonging to the account just removed.
    forget_session(profile)


@dataclass(frozen=True)
class AccountView:
    """One account as the Accounts dialog shows it."""
    profile: str
    number: int                             # its place in the list: "Account 2"
    projects: tuple[str, ...]
    stores: tuple[tuple[str, str], ...]     # the (label, link) of each store it holds
    ready: bool                             # a session is stored here; not that it still works

    @property
    def title(self) -> str:
        """What the account is called: its place in the list, and the stores that identify it."""
        names = ", ".join(label.split(SEPARATOR)[0] for label, _ in self.stores)
        return f"Account {self.number}" + (f"{SEPARATOR}{names}" if names else "")

    @property
    def status(self) -> str:
        """The most that can be said without opening a browser: a session is stored here, or none is."""
        return "Ready" if self.ready else "Not signed in"


def _project_of(link: str) -> str:
    """The project a saved store link belongs to; its number is the store's own number."""
    match = re.search(r"/p-(\d+)", link or "")
    return f"PRJ{match.group(1)}" if match else ""


def account_overview(stores: Mapping[str, str], accounts: Optional[Sequence[Account]] = None,
                     path: str = ACCOUNTS_FILE,
                     pattern: str = PROFILE_GLOB) -> tuple[list[AccountView], list[str]]:
    """Every account with the stores it holds, and the saved stores no account holds.

    An account holding nothing is still listed: somebody added it, and leaving it out would leave no
    way to sign into it or remove it. A store whose owner is gone is reported rather than attached to
    whichever account came first, which would read it through somebody else's catalog.
    """
    listed = list(accounts if accounts is not None else load_accounts(path, pattern))
    owner = {project: account.profile for account in listed for project in account.projects}
    held: dict[str, list[tuple[str, str]]] = {account.profile: [] for account in listed}
    orphans: list[str] = []
    for label, link in stores.items():
        profile = owner.get(_project_of(link), "")
        if profile:
            held[profile].append((label, link))
        else:
            orphans.append(label)
    views = [AccountView(profile=account.profile, number=number, projects=account.projects,
                         stores=tuple(held[account.profile]), ready=_has_session(account.profile))
             for number, account in enumerate(listed, start=1)]
    return views, orphans


def store_urls(stores: Iterable[SellerStore]) -> list[str]:
    """The links, in the order the stores were listed."""
    return [store.url for store in stores]
