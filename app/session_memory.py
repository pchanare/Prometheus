"""
session_memory.py — Persistent key-fact store for Prometheus.

Extracts the handful of facts that matter (address, solar figures, homeowner
name, etc.) from tool responses and saves them to a JSON file next to this
module.  On every new ADK session the stored facts are re-injected as a tiny
[SESSION MEMORY] note so the model never has to ask the user to repeat info.

Storage backends:
  • Cloud Run  — Google Cloud Storage (persists across container restarts/OOM)
                 Requires env vars: K_SERVICE (set automatically by Cloud Run)
                                    MEMORY_BUCKET (GCS bucket name)
  • Local dev  — Local JSON file (prometheus_memory.json beside this module)

Survives: context window resets, WebSocket reconnects, server restarts (GCS).
Lost when: GCS object / local JSON file is manually deleted.
"""

import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

_EASTERN = ZoneInfo("America/New_York")

log = logging.getLogger("prometheus.memory")

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

# Cloud Run sets K_SERVICE automatically; MEMORY_BUCKET must be provided via
# the Cloud Run env var (populated by Terraform).
_ON_CLOUD_RUN = bool(os.environ.get("K_SERVICE"))
_BUCKET_NAME  = os.environ.get("MEMORY_BUCKET", "")
_GCS_OBJECT   = "prometheus_memory.json"

_MEMORY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "prometheus_memory.json"
)

# In-process cache — loaded once on import, written on every update.
_mem: dict = {}


# ---------------------------------------------------------------------------
# GCS helpers (only imported / used when on Cloud Run)
# ---------------------------------------------------------------------------

def _gcs_read() -> dict:
    """Download and parse the memory JSON from GCS. Returns {} on any error."""
    try:
        from google.cloud import storage  # type: ignore
        client = storage.Client()
        blob = client.bucket(_BUCKET_NAME).blob(_GCS_OBJECT)
        if blob.exists():
            data = json.loads(blob.download_as_text(encoding="utf-8"))
            log.info(
                "session_memory: loaded %d facts from gs://%s/%s",
                len(data), _BUCKET_NAME, _GCS_OBJECT,
            )
            return data
        log.info("session_memory: gs://%s/%s not found — starting fresh", _BUCKET_NAME, _GCS_OBJECT)
    except Exception as exc:
        log.warning("session_memory: GCS read failed (%s) — starting fresh", exc)
    return {}


def _gcs_write() -> None:
    """Serialise _mem to JSON and upload to GCS."""
    try:
        from google.cloud import storage  # type: ignore
        client = storage.Client()
        client.bucket(_BUCKET_NAME).blob(_GCS_OBJECT).upload_from_string(
            json.dumps(_mem, indent=2),
            content_type="application/json",
        )
        log.info(
            "session_memory: saved %d facts to gs://%s/%s",
            len(_mem), _BUCKET_NAME, _GCS_OBJECT,
        )
    except Exception as exc:
        log.warning("session_memory: GCS write failed: %s", exc)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load() -> None:
    global _mem
    if _ON_CLOUD_RUN and _BUCKET_NAME:
        _mem = _gcs_read()
        return

    # Local fallback
    if os.path.exists(_MEMORY_FILE):
        try:
            with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                _mem = json.load(f)
            log.info("session_memory: loaded %d facts from %s", len(_mem), _MEMORY_FILE)
        except Exception as exc:
            log.warning("session_memory: could not load %s: %s", _MEMORY_FILE, exc)
            _mem = {}
    else:
        _mem = {}


def _save() -> None:
    if _ON_CLOUD_RUN and _BUCKET_NAME:
        _gcs_write()
        return

    # Local fallback
    try:
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(_mem, f, indent=2)
    except Exception as exc:
        log.warning("session_memory: could not save %s: %s", _MEMORY_FILE, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reset() -> None:
    """
    Wipe all stored memory and persist the empty state to disk.
    Called when the user provides a new address — a different property means
    all prior solar figures, roof data, and image paths are stale.
    """
    global _mem
    _mem = {}
    _save()
    log.info("session_memory: reset — all prior facts cleared")


def update(**kwargs) -> None:
    """
    Update one or more memory fields and persist to disk immediately.
    None values and empty strings are ignored so callers can pass raw
    tool-response fields without guarding each one.
    """
    changed = False
    for key, val in kwargs.items():
        if val is not None and str(val).strip() not in ("", "N/A"):
            _mem[key] = val
            changed = True
    if changed:
        _mem["last_updated"] = datetime.now(_EASTERN).isoformat()
        _save()
        log.info("session_memory: updated keys=%s", list(kwargs.keys()))


def build_injection() -> str:
    """
    Return a compact [SESSION MEMORY] note to inject at the start of every
    new ADK session.  Returns an empty string when nothing is stored yet.
    """
    if not _mem:
        return ""

    lines = ["── SESSION MEMORY (identity facts from your previous conversation) ─────────",
             "Use address, name, and bill (if listed below) without asking again.",
             "IMPORTANT: Do NOT open with a welcome-back greeting. The user has already",
             "been greeted. Respond directly and naturally to their first message.",
             "IMPORTANT: Never use any cached cost, panel count, or payback figures for",
             "financial calculations — always call the relevant tools to get fresh data."]

    if _mem.get("address"):
        lines.append(f"Property address       : {_mem['address']}")
    if _mem.get("homeowner_name"):
        lines.append(f"Homeowner name         : {_mem['homeowner_name']}")
    if _mem.get("state"):
        lines.append(f"State                  : {_mem['state']}")
    if _mem.get("monthly_bill_usd"):
        lines.append(
            f"Monthly electricity bill: ${float(_mem['monthly_bill_usd']):,.0f}/month"
            " — use this, do not ask again unless the user says it has changed"
        )
    if _mem.get("yearly_sunshine_hours"):
        lines.append(f"Annual sunshine        : {_mem['yearly_sunshine_hours']} hrs/year")
    if _mem.get("roof_area_m2"):
        lines.append(f"Roof area              : {_mem['roof_area_m2']} m²")
    if _mem.get("roof_age_years") is not None:
        lines.append(f"Roof age               : {_mem['roof_age_years']} years")

    # Image path — only include if the file still exists on disk
    img = _mem.get("last_image_path", "")
    if img and os.path.exists(img):
        lines.append(f"Last uploaded image    : {img}")

    updated = _mem.get("last_updated", "")
    if updated:
        lines.append(f"(Facts recorded: {updated[:10]})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Load on import
# ---------------------------------------------------------------------------
_load()
