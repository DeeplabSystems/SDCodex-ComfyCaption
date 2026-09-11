"""Caption Models — HuggingFace model downloader for the ComfyUI Captioning
plugin.

Provides a settings page (Settings -> Caption Models) where the user can set a
HuggingFace token, choose a model repo, and download one of four preset
GGUF+mmproj captioning models (e.g. Qwen3-VL-8B from LM Studio style repos).
The downloader is pure-Python (stdlib only), following the include-pattern /
resume / token-auth approach of the bundled ``hfd.sh`` script, so it runs
inside the container without requiring curl, wget or aria2c.

Files land in the CAPTION_MODELS volume (container /data/caption_models).
"""

import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

# ---------------------------------------------------------------------------
# Paths & defaults
# ---------------------------------------------------------------------------

def _models_dir():
    """The directory where downloaded caption models are stored.

    Prefers the CAPTION_MODELS volume; then a sibling dir named
    "caption_models" under the app /data root; finally the plugin dir.
    """
    for cand in (
        os.environ.get("CAPTION_MODELS"),
        os.path.join(os.environ.get("DATA_DIR", "/data"), "caption_models"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "caption_models"),
    ):
        if cand:
            try:
                os.makedirs(cand, exist_ok=True)
                return cand
            except Exception:
                continue
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "caption_models")


def _settings_file():
    return os.path.join(_models_dir(), "models_settings.json")


DEFAULT_REPO = "nakedlittlezombie/Qwen3-VL-8B-Thinking-heretic"
HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://huggingface.co")
CHUNK = 1024 * 1024  # 1 MiB

# The four preset model options (GGUF + its matching mmproj projector).
MODEL_OPTIONS = [
    {
        "key": "q4_k_m",
        "label": "Q4_K_M",
        "size": "~5.0 GB",
        "gguf": "Qwen3-VL-8B-Thinking-heretic-Q4_K_M.gguf",
        "mmproj": "mmproj-Qwen3-VL-8b-Thinking-heretic-Q4_K_M.gguf",
        "gguf_size_approx": 5.1e9,
        "mmproj_size_approx": 0.6e9,
    },
    {
        "key": "q8_0",
        "label": "Q8_0",
        "size": "~8.7 GB",
        "gguf": "Qwen3-VL-8B-Thinking-heretic-Q8_0.gguf",
        "mmproj": "mmproj-Qwen3-VL-8b-Thinking-heretic-Q8_0.gguf",
        "gguf_size_approx": 8.7e9,
        "mmproj_size_approx": 0.75e9,
    },
    {
        "key": "f16",
        "label": "F16",
        "size": "~16.4 GB",
        "gguf": "Qwen3-VL-8B-Thinking-heretic-F16.gguf",
        "mmproj": "mmproj-Qwen3-VL-8b-Thinking-heretic-F16.gguf",
        "gguf_size_approx": 16.4e9,
        "mmproj_size_approx": 1.16e9,
    },
    {
        "key": "bf16",
        "label": "BF16",
        "size": "~16.4 GB",
        "gguf": "Qwen3-VL-8B-Thinking-heretic-BF16.gguf",
        "mmproj": "mmproj-Qwen3-VL-8b-Thinking-heretic-BF16.gguf",
        "gguf_size_approx": 16.4e9,
        "mmproj_size_approx": 1.16e9,
    },
]

# ---------------------------------------------------------------------------
# Settings store
# ---------------------------------------------------------------------------

def load_settings():
    try:
        with open(_settings_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
    data.setdefault("hf_token", "")
    data.setdefault("repo", DEFAULT_REPO)
    return data


def save_settings(**updates):
    data = load_settings()
    data.update(updates)
    os.makedirs(os.path.dirname(_settings_file()), exist_ok=True)
    with open(_settings_file(), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return data


# ---------------------------------------------------------------------------
# Background downloader
# ---------------------------------------------------------------------------

class _DownloadJob:
    def __init__(self):
        self.lock = threading.Lock()
        self.generation = 0
        self.cancel = False
        self.file = ""            # current file being downloaded
        self.done_bytes = 0
        self.total_bytes = 0
        self.speed = 0.0
        self.state = "idle"       # idle | downloading | done | error | cancelled
        self.message = ""

# Single global job (one download at a time) — simplest & predictable.
_job = _DownloadJob()
_job_thread = None


def _resolve_remote_size(repo, filename, token):
    """Return exact byte size for a file in a HF repo via the API, or None."""
    url = f"{HF_ENDPOINT}/api/models/{repo}?blobs=true"
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
        for sib in meta.get("siblings", []) or []:
            if sib.get("rfilename") == filename:
                return int(sib.get("size") or 0) or None
    except Exception:
        pass
    return None


def _download_one(dest, url, token, approx_size):
    """Stream-download a single file to ``dest``, resuming from any existing
    ``<dest>.incomplete`` partial. Returns True on success."""
    partial = dest + ".incomplete"
    start = 0
    if os.path.exists(partial):
        start = os.path.getsize(partial)

    with _job.lock:
        gen = _job.generation

    headers = {"Range": f"bytes={start}-"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)

    with _job.lock:
        _job.file = os.path.basename(dest)
        _job.cancel = False
        _job.state = "downloading"
        _job.done_bytes = start or 0
        _job.total_bytes = approx_size or 0
        _job.speed = 0.0
        _job.message = f"Downloading {os.path.basename(dest)}"

    prev = time.time()
    prev_bytes = start or 0

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = approx_size
            # 206 = partial content (resumed). 200 = full (start ignored) → reset.
            if resp.status == 200 and start:
                start = 0
                with _job.lock:
                    _job.done_bytes = 0
                if os.path.exists(partial):
                    os.remove(partial)
            elif resp.status == 206:
                total = approx_size
            # If the server knows the total from Content-Range/Content-Length, use it.
            clen = resp.headers.get("Content-Length")
            crng = resp.headers.get("Content-Range")
            if crng and "/" in crng:
                try:
                    total = int(crng.rsplit("/", 1)[1])
                except Exception:
                    pass
            elif clen:
                try:
                    total = start + int(clen)
                except Exception:
                    pass
            with _job.lock:
                _job.total_bytes = total

            mode = "ab" if start else "wb"
            with open(partial, mode) as fh:
                while True:
                    if _job.cancel or _job.generation != gen:
                        with _job.lock:
                            _job.state = "cancelled"
                            _job.message = "Cancelled"
                        return False
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    now = time.time()
                    with _job.lock:
                        _job.done_bytes += len(chunk)
                        dt = now - prev
                        if dt >= 1.0:
                            _job.speed = (len(chunk) or 0) / dt if dt else 0
                            prev = now
        os.replace(partial, dest)
        return True
    except urllib.error.HTTPError as e:
        code = e.code
        if code in (401, 403):
            reason = "Auth rejected — check the HuggingFace token / repo access."
        elif code == 404:
            reason = f"File not found in repo (HTTP 404): {os.path.basename(dest)}"
        else:
            reason = f"HTTP {code} while downloading {os.path.basename(dest)}"
        with _job.lock:
            _job.state = "error"
            _job.message = reason
        return False
    except Exception as e:
        with _job.lock:
            _job.state = "error"
            _job.message = f"{type(e).__name__}: {e}"
        return False


def _run_download(repo, token, option):
    """Download the GGUF + mmproj files for a selected option from ``repo``."""
    with _job.lock:
        gen = _job.generation
    gguf_name = option["gguf"]
    mmproj_name = option["mmproj"]
    dest_dir = _models_dir()
    files = [
        (gguf_name, option.get("gguf_size_approx", 0)),
        (mmproj_name, option.get("mmproj_size_approx", 0)),
    ]

    # Try to get exact sizes from HF metadata (fall back to the approx sizes).
    sizes = {}
    for name, approx in files:
        sizes[name] = None  # resolved below
    try:
        meta_url = f"{HF_ENDPOINT}/api/models/{repo}?blobs=true"
        req = urllib.request.Request(meta_url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
        for sib in meta.get("siblings", []) or []:
            if sib.get("rfilename") in sizes:
                sizes[sib["rfilename"]] = int(sib.get("size") or 0) or None
    except Exception:
        pass

    ok = True
    for name, approx in files:
        if _job.cancel or _job.generation != gen:
            break
        dest = os.path.join(dest_dir, name)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            # Already downloaded (or an old complete copy) — skip unless partial.
            if not os.path.exists(dest + ".incomplete"):
                with _job.lock:
                    _job.message = f"Skipping {name} (already present)"
                continue
        url = f"{HF_ENDPOINT}/{repo}/resolve/main/{name}"
        size = sizes.get(name) or approx
        if not _download_one(dest, url, token, size):
            ok = False
            break

    with _job.lock:
        if _job.generation == gen:
            if _job.cancel:
                _job.state = "cancelled"
                _job.message = "Cancelled"
            else:
                _job.state = "done" if ok else "error"
                _job.message = "All files downloaded" if ok else _job.message or "Download failed"


def start_download(repo, token, option_key):
    """Start (or restart) a background download for the given option."""
    global _job_thread
    option = next((o for o in MODEL_OPTIONS if o["key"] == option_key), None)
    if not option:
        return False, "Unknown option"
    repo = (repo or DEFAULT_REPO).strip() or DEFAULT_REPO

    # Supersede any running job: bump the generation and cancel it.
    with _job.lock:
        _job.cancel = True
        _job.generation += 1
        _job.state = "idle"

    _job_thread = threading.Thread(
        target=_run_download, args=(repo, token, option), daemon=True
    )
    _job_thread.start()
    return True, ""


def status():
    with _job.lock:
        return {
            "state": _job.state,
            "message": _job.message,
            "file": _job.file,
            "done_bytes": _job.done_bytes,
            "total_bytes": _job.total_bytes,
            "speed": _job.speed,
            "cancel": _job.cancel,
        }


def stop_download():
    with _job.lock:
        _job.cancel = True
        _job.state = "cancelling"


# ---------------------------------------------------------------------------
# Blueprint & routes
# ---------------------------------------------------------------------------

caption_models_bp = Blueprint("caption_models", __name__, template_folder="templates")


def _list_models(option=None):
    """Return models present on disk (for UI badges), optional by option key."""
    d = _models_dir()
    present = {}
    if os.path.isdir(d):
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.isfile(p) and not name.endswith((".incomplete", ".json", ".partial")):
                present[name] = os.path.getsize(p)
    return present


@caption_models_bp.route("/caption_models")
def page():
    embed = request.args.get("embed") == "1"
    s = load_settings()
    present = _list_models()
    opts = []
    pdir = _models_dir()
    for o in MODEL_OPTIONS:
        gguf_done = os.path.exists(os.path.join(pdir, o["gguf"])) and not os.path.exists(
            os.path.join(pdir, o["gguf"] + ".incomplete")
        )
        mm_done = os.path.exists(os.path.join(pdir, o["mmproj"])) and not os.path.exists(
            os.path.join(pdir, o["mmproj"] + ".incomplete")
        )
        opts.append(dict(o, gguf_done=gguf_done, mmproj_done=mm_done))
    return render_template(
        "caption_models.html",
        embed=embed,
        settings=s,
        options=opts,
        models_dir=pdir,
        present=present,
        status=status(),
    )


@caption_models_bp.route("/caption_models/save", methods=["POST"])
def save():
    data = request.get_json(silent=True) or request.form
    updates = {}
    if "hf_token" in data:
        updates["hf_token"] = (data.get("hf_token") or "").strip()
    if "repo" in data:
        updates["repo"] = (data.get("repo") or "").strip() or DEFAULT_REPO
    save_settings(**updates)
    return jsonify(ok=True, settings=load_settings())


@caption_models_bp.route("/caption_models/download", methods=["POST"])
def download():
    data = request.get_json(silent=True) or request.form
    option = data.get("option", "")
    repo = (data.get("repo") or "").strip()
    token = (data.get("hf_token") or "").strip()
    if repo and repo != load_settings().get("repo"):
        save_settings(repo=repo)
    if token:
        save_settings(hf_token=token)
    else:
        token = load_settings().get("hf_token", "")
    ok, msg = start_download(repo or load_settings().get("repo", DEFAULT_REPO), token, option)
    return jsonify(ok=ok, message=msg)


@caption_models_bp.route("/caption_models/stop", methods=["POST"])
def stop():
    stop_download()
    return jsonify(ok=True)


@caption_models_bp.route("/caption_models/status", methods=["GET"])
def status_route():
    return jsonify(status())


def init_caption_models(app):
    if "caption_models" not in app.blueprints:
        app.register_blueprint(caption_models_bp)