"""The signed-in session of each account, kept so noon is asked through a browser only once.

Opening Chrome costs about six seconds per account and every read used to pay it again, to be told
what the last read already learned. The session behind that read -- the cookies Chrome holds for the
profile, and the dozen locale headers the catalog sends -- is written down here instead, and every
later call goes straight to the API with no browser at all: seven stores across three projects come
back in under two seconds that way, against six for one account through Chrome.

The headers alone authenticate nothing. They carry no cookie, no authorization and no token -- twelve
locale, user-agent and project constants, which answer 401 on their own. What authenticates is the
cookie jar, so that is what is saved, and saving it is the whole reason this file exists.

Nothing here decides when a session has lapsed. A cookie that expired is simply one the request
context stops sending, and noon then answers 401 -- which is the signal, and the only signal, to open
Chrome again. Guessing at expiry instead would either open Chrome while the session was still good or
promise one that had already gone.

The file holds live session cookies: anything that can read it can act as the account until they
lapse. It is written to the user's home directory, readable only by them.
"""
from __future__ import annotations

import json
import os
import time
from typing import Mapping, Optional

SESSION_FILE = "~/.noon_seller_sessions.json"
DENIED = (401, 403)   # noon turning us away: the one thing that sends us back to Chrome


def _canonical(path: str, paths=os.path) -> str:
    """One spelling of a profile directory, so one account is never taken for two.

    Windows is why. There, expanduser substitutes the home directory but leaves the caller's forward
    slash alone, while glob rebuilds what it finds with backslashes; keyed on the raw strings the two
    spellings never match, and every account then starts out holding no projects at all.

    It lives here, depending on nothing, so the module that files sessions and the one that files
    accounts can share a single definition instead of importing each other in a circle. `paths` is
    injectable so the Windows behaviour can be reproduced on any machine by passing `ntpath`.
    """
    return paths.normpath(paths.expanduser(path))


def _all_sessions(path: str) -> dict:
    """Every session written down so far; nothing at all before the first account was read."""
    try:
        with open(os.path.expanduser(path)) as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _write(found: dict, path: str) -> None:
    """Replace the file, readable by its owner alone -- it holds live session cookies."""
    target = os.path.expanduser(path)
    # Created with the right mode from the start: writing first and chmod-ing after would leave the
    # cookies world-readable for however long the two calls are apart.
    handle = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w") as opened:
        json.dump(found, opened)
    try:
        os.chmod(target, 0o600)   # a file that already existed keeps its own mode, so say it again
    except OSError:
        pass                      # a filesystem with no modes to speak of; the session still works


def load_session(profile: str, path: str = SESSION_FILE) -> Optional[dict]:
    """The saved session for this profile, or None if it was never saved.

    None means "nobody has been through Chrome for this account yet", never "the session has lapsed":
    only noon can say that, by refusing a call.
    """
    saved = _all_sessions(path).get(_canonical(profile))
    if not isinstance(saved, dict):
        return None
    if not saved.get("state") or not saved.get("headers"):
        return None   # half a session is no session: it would fail the call it was trusted for
    return saved


def save_session(profile: str, state: Mapping, headers: Mapping[str, str],
                 body: Optional[Mapping] = None, user_agent: str = "",
                 path: str = SESSION_FILE) -> None:
    """Write down the session a browser just proved, so the next call needs no browser.

    The catalog needs more than the cookies to ask a question: `body` is the request template the
    account's own page sends, which the caller varies a page and a sort at a time, and `user_agent`
    is the browser's own -- sent so a request made outside the page still describes the client the
    session belongs to, rather than announcing itself as a different one.
    """
    found = _all_sessions(path)
    saved = {"state": dict(state), "headers": dict(headers), "saved_at": int(time.time())}
    if body is not None:
        saved["body"] = dict(body)
    if user_agent:
        saved["user_agent"] = user_agent
    # Merged over whatever is already filed: the stores path saves no body and the catalog path does,
    # and whichever ran last must not throw away what the other had learned about this account.
    existing = _all_sessions(path).get(_canonical(profile))
    if isinstance(existing, dict):
        merged = dict(existing)
        merged.update(saved)
        saved = merged
    found[_canonical(profile)] = saved
    _write(found, path)


def forget_session(profile: str, path: str = SESSION_FILE) -> None:
    """Drop an account's session, so nothing of it outlives the account itself.

    A session left behind would be inherited by whoever signs into that directory name next, and their
    stores would then be read through somebody else's cookies.
    """
    found = _all_sessions(path)
    if found.pop(_canonical(profile), None) is None:
        return
    _write(found, path)
