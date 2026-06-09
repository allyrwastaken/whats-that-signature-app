"""Self-update via GitHub Releases.

Checks the repo's latest release, downloads the installer (shipped as a .zip),
extracts it, and runs the installer — which closes the running overlay and
relaunches it. Standard library only (urllib/ssl/zipfile), nothing to bundle.
"""

import ctypes
import json
import os
import shutil
import ssl
import tempfile
import urllib.parse
import urllib.request
import zipfile

REPO = "allyrwastaken/whats-that-signature-app"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
_UA = {"User-Agent": "WhatsThatSignature-Updater"}


def _is_github_host(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host == "github.com" or host.endswith((".github.com", ".githubusercontent.com"))


def _version_tuple(s):
    """'v1.2.0' -> (1, 2, 0); tolerant of odd formatting."""
    out = []
    for part in s.lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def is_newer(latest_tag, current):
    return _version_tuple(latest_tag) > _version_tuple(current)


def check_latest(timeout=10):
    """Return (tag, installer_url, notes) for the latest release; notes is the
    release body (changelog). Raises on error."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        API_LATEST, headers={**_UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        data = json.load(r)
    tag = data.get("tag_name", "")
    notes = (data.get("body") or "").strip()
    url = None
    for asset in data.get("assets", []):
        if asset.get("name", "").lower().endswith(".zip"):
            url = asset.get("browser_download_url")
            break
    return tag, url, notes


def download(url, tag, timeout=120):
    """Download the release .zip and extract the installer .exe from it; return
    the path to the extracted installer."""
    # Only ever fetch the update from GitHub over HTTPS (defense in depth — the
    # URL already comes from GitHub's verified API response).
    if not url.lower().startswith("https://") or not _is_github_host(url):
        raise ValueError(f"Refusing to download update from untrusted URL: {url}")
    ctx = ssl.create_default_context()
    tmp = tempfile.gettempdir()
    zip_path = os.path.join(tmp, f"WhatsThatSignature-Setup-{tag}.zip")
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r, \
            open(zip_path, "wb") as f:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
    # Extract the installer .exe from the archive. Write to a basename-only path
    # (defends against zip-slip path traversal, even though the zip is ours).
    out_dir = os.path.join(tmp, f"WhatsThatSignature-Update-{tag}")
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        members = [n for n in z.namelist() if n.lower().endswith(".exe")]
        if not members:
            raise ValueError("Update archive did not contain an installer .exe.")
        dest = os.path.join(out_dir, os.path.basename(members[0]))
        with z.open(members[0]) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
    return dest


def run_installer(path):
    """Launch the installer's wizard. ShellExecute honours the installer's
    'require administrator' manifest and elevates correctly (CreateProcess /
    subprocess can't). The wizard closes the running app and relaunches it via
    its Finish-page entry."""
    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.restype = ctypes.c_ssize_t
    rc = shell32.ShellExecuteW(None, "open", path, None, None, 1)  # SW_SHOWNORMAL
    if rc <= 32:
        raise OSError(f"Could not start the installer (ShellExecute code {rc}).")
