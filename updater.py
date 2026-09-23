"""Check GitHub for a newer build and install it over this one.

The app is published as a zipped PyInstaller folder, so updating means replacing the folder it is
running out of -- which Windows will not let a program do to itself while its exe is open. So the
swap is handed to a small batch script: it waits for this process to exit, copies the new files in,
starts the app again and deletes itself.

The copy deliberately does not mirror. A mirror would delete anything in the app folder that is not
in the zip, and the store listings the user has fetched live there, in input_data, alongside the
build's own copy of it. That folder is skipped outright: an update must never cost somebody the
catalogues they spent an afternoon fetching.

The releases repository is public, so the check needs no token and no sign-in. Nothing here sends
anything anywhere -- it asks GitHub what the latest tag is and downloads the asset it names.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import zipfile

RELEASES_API = "https://api.github.com/repos/mabdurrafey1/products-image-similarity-releases/releases/latest"
RELEASES_PAGE = "https://github.com/mabdurrafey1/products-image-similarity-releases/releases/latest"
ASSET_NAME = "AI_Product_Duplicate_Finder_dist.zip"
TIMEOUT = 30


def current_version() -> str:
    """The tag this build was made from, or "dev" when running from a checkout.

    _version.py is written by build_dist.py from the CI tag and is not committed: the tag stays the
    only place a version is declared, and a development run truthfully says it has no version rather
    than claiming one it hasn't got.
    """
    try:
        from _version import VERSION
        return str(VERSION).strip() or "dev"
    except Exception:
        return "dev"


def _parts(tag: str):
    """The numbers in a tag, so v1.0.40 sorts after v1.0.9 instead of before it as text would."""
    return [int(piece) for piece in re.findall(r"\d+", tag or "")]


def is_newer(latest: str, installed: str) -> bool:
    """Whether `latest` is a later release than what is installed.

    A development build is never told it is out of date: it isn't a release, and overwriting a
    checkout with a packaged build would replace the source somebody is working on.
    """
    if not latest or installed == "dev":
        return False
    mine, theirs = _parts(installed), _parts(latest)
    return bool(theirs) and theirs > mine


def latest_release() -> dict:
    """Ask GitHub for the newest published release. Raises on network or API trouble."""
    import requests
    answer = requests.get(RELEASES_API, timeout=TIMEOUT,
                          headers={"Accept": "application/vnd.github+json"})
    answer.raise_for_status()
    found = answer.json()
    asset = next((a for a in found.get("assets") or [] if a.get("name") == ASSET_NAME), None)
    return {
        "tag": str(found.get("tag_name") or ""),
        "url": str((asset or {}).get("browser_download_url") or ""),
        "size": int((asset or {}).get("size") or 0),
        "notes": str(found.get("body") or ""),
    }


def app_folder() -> str:
    """The folder that would be replaced: where the packaged exe lives."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def download(url: str, target: str, progress=None, should_stop=None) -> str:
    """Fetch the release zip, reporting how far along it is as it goes.

    Written to a temporary file and only moved into place once it is whole, so a download that is
    interrupted cannot be mistaken for a finished one.
    """
    import requests
    partial = target + ".part"
    with requests.get(url, stream=True, timeout=TIMEOUT) as answer:
        answer.raise_for_status()
        total = int(answer.headers.get("Content-Length") or 0)
        done = 0
        with open(partial, "wb") as handle:
            for chunk in answer.iter_content(chunk_size=1 << 20):
                if should_stop is not None and should_stop():
                    raise RuntimeError("Update cancelled.")
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    os.replace(partial, target)
    return target


def unpack(zip_path: str, into: str, progress=None) -> str:
    """Extract the release beside the download, so the swap is a copy and not a decompression."""
    os.makedirs(into, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.namelist()
        for number, member in enumerate(members, start=1):
            archive.extract(member, into)
            if progress is not None and number % 25 == 0:
                progress(number, len(members))
    if progress is not None:
        progress(len(members), len(members))
    return into


SWAP_SCRIPT = """@echo off
rem Waits for the app to close, puts the new files in place, starts it again, then removes itself.
rem Everything it does is written to a log beside the app: this runs after the window has closed,
rem so a failure here has nothing to report it and would otherwise be silent.
setlocal
set LOG={log}
set NEW={new}
set APP={app}
echo [%DATE% %TIME%] update starting, waiting for pid {pid} >> "%LOG%"
rem Wait-Process is built for this: no text to parse, it returns at once if the process has already
rem gone, and the timeout means a wait that goes wrong costs two minutes rather than forever.
powershell -NoProfile -NonInteractive -Command "try {{ Wait-Process -Id {pid} -Timeout 120 }} catch {{ }}" >> "%LOG%" 2>&1
echo [%TIME%] app closed >> "%LOG%"
where robocopy >> "%LOG%" 2>&1
echo [%TIME%] copying "%NEW%" to "%APP%" >> "%LOG%"
rem /E adds and overwrites but never deletes, and input_data is skipped outright, so the store
rem listings the user has fetched are not casualties of an update.
rem /XD matches directories in the SOURCE, so it is the new build's input_data that has to be
rem named here. Naming the destination's looks right and excludes nothing at all.
robocopy "%NEW%" "%APP%" /E /XD "%NEW%\\input_data" "%APP%\\input_data" /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
rem robocopy says 0-7 for success and 8 and up for failure, so it cannot be checked the usual way.
if errorlevel 8 (echo [%TIME%] ROBOCOPY FAILED with %ERRORLEVEL% >> "%LOG%") else (echo [%TIME%] copied, robocopy said %ERRORLEVEL% >> "%LOG%")
start "" "{exe}"
echo [%TIME%] restarted the app >> "%LOG%"
rem The script deletes itself last; nothing is left behind in temp that could be run again.
del "%~f0"
"""


def install(new_folder: str, app_dir: str = "", exe: str = "", log: str = "") -> str:
    """Hand the swap to a detached script and return its path. The caller must then exit.

    Windows only: it is the only platform this is published for, and the batch file is the whole
    mechanism. Anywhere else this refuses rather than pretending to have updated anything.
    """
    if not sys.platform.startswith("win"):
        raise RuntimeError("Automatic updates are only available on Windows. "
                           "Download the new version from the releases page instead.")
    app_dir = app_dir or app_folder()
    exe = exe or (os.path.abspath(sys.executable) if getattr(sys, "frozen", False)
                  else os.path.join(app_dir, "AI_Product_Duplicate_Finder.exe"))
    # A release zip holds the app folder's contents, but a zip made from the folder itself would
    # nest them one deeper; copy from whichever of the two actually holds the exe.
    inner = os.path.join(new_folder, os.path.basename(app_dir))
    source = inner if os.path.isdir(inner) else new_folder
    script = os.path.join(tempfile.mkdtemp(prefix="apdf_update_"), "apply_update.bat")
    log = log or os.path.join(app_dir, "update_log.txt")
    with open(script, "w") as handle:
        handle.write(SWAP_SCRIPT.format(pid=os.getpid(), new=os.path.abspath(source),
                                        app=os.path.abspath(app_dir), exe=os.path.abspath(exe),
                                        log=os.path.abspath(log)))
    # Detached, so closing the app does not take the script with it.
    subprocess.Popen(["cmd", "/c", script], close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    return script
