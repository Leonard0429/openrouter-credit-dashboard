"""Desktop-panel backend for the openrouter-credits plugin.

Serves the live credit readout under ``/api/plugins/openrouter-credits/``.

The web server loads this file with ``importlib`` under a flat synthetic module
name (``hermes_dashboard_plugin_<id>``), so it has no package context and
cannot use relative imports — the sibling ``tools.py`` is therefore loaded by
path. That keeps the panel, the agent tool, and the cron watchdog on ONE
implementation of the fetch and burn-rate maths.

Why a backend at all: the desktop renderer has full app authority but must
never hold the API key. The key stays in the Hermes process; the panel only
ever sees dollar amounts.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

router = APIRouter()

# The panel polls; OpenRouter should not be hit once per poll per window. A
# short server-side cache keeps a 60s client interval cheap and makes several
# open windows share one upstream read.
_CACHE_TTL_SECONDS = 30.0
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "payload": None}


def _load_tools():
    """Import the plugin's ``tools.py`` (single source of truth for the fetch)."""
    tools_py = Path(__file__).resolve().parent.parent / "tools.py"
    if not tools_py.is_file():
        raise RuntimeError(f"openrouter-credits: tools.py not found at {tools_py}")
    spec = importlib.util.spec_from_file_location("openrouter_credits_tools", tools_py)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"openrouter-credits: cannot load {tools_py}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rate(history: list[dict], snapshot: dict) -> tuple[Optional[float], Optional[str]]:
    """(dollars per day, where it came from) — measured history first, else rolling window."""
    rate = _load_tools().burn_rate(history)
    if rate and rate.get("per_day", 0) > 0:
        return float(rate["per_day"]), str(rate.get("source") or "history")
    weekly = snapshot.get("usage_weekly")
    if weekly and float(weekly) > 0:
        return float(weekly) / 7.0, "7-day spend"
    monthly = snapshot.get("usage_monthly")
    if monthly and float(monthly) > 0:
        return float(monthly) / 30.0, "30-day spend"
    return None, None


def _collect(*, record: bool) -> dict:
    """One live readout. Blocking (network) — callers run it off the event loop."""
    tools = _load_tools()
    snapshot = tools.fetch_snapshot()

    history = tools._read_history()
    if record:
        tools._append_history(snapshot)
        history = history + [snapshot]

    balance = float(snapshot.get("balance") or 0.0)
    per_day, source = _rate(history, snapshot)

    runway: Optional[float]
    if balance <= 0:
        runway = 0.0
    elif per_day and per_day > 0:
        runway = balance / per_day
    else:
        runway = None

    total_credits = float(snapshot.get("total_credits") or 0.0)
    total_usage = float(snapshot.get("total_usage") or 0.0)
    used_ratio = (total_usage / total_credits) if total_credits > 0 else None

    return {
        "ok": True,
        "balance": balance,
        "total_credits": total_credits,
        "total_usage": total_usage,
        "used_ratio": used_ratio,
        "usage_daily": snapshot.get("usage_daily"),
        "usage_weekly": snapshot.get("usage_weekly"),
        "usage_monthly": snapshot.get("usage_monthly"),
        "key_limit": snapshot.get("key_limit"),
        "key_limit_remaining": snapshot.get("key_limit_remaining"),
        "key_limit_reset": snapshot.get("key_limit_reset"),
        "burn_rate_per_day": per_day,
        "burn_rate_source": source,
        "runway_days": runway,
        "history_points": len(history),
        "fetched_at": snapshot.get("ts"),
        "recorded": record,
    }


def _cached() -> Optional[dict]:
    with _cache_lock:
        payload = _cache["payload"]
        if payload is not None and (time.monotonic() - _cache["at"]) < _CACHE_TTL_SECONDS:
            return payload
    return None


def _store(payload: dict) -> None:
    with _cache_lock:
        _cache["at"] = time.monotonic()
        _cache["payload"] = payload


@router.get("/credits")
async def credits() -> dict:
    """Live credit readout (balance, spend windows, burn rate, runway).

    Read-only: never records a snapshot, so a polling panel cannot flood the
    history file the burn rate is measured from.
    """
    hit = _cached()
    if hit is not None:
        return {**hit, "cached": True}
    try:
        payload = await run_in_threadpool(_collect, record=False)
    except Exception as exc:  # surfaced in the panel, never a 500 stack trace
        logger.warning("openrouter-credits panel: live read failed: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    _store(payload)
    return {**payload, "cached": False}


@router.post("/record")
async def record() -> dict:
    """Record a snapshot on demand (the panel's button), then return fresh data."""
    try:
        payload = await run_in_threadpool(_collect, record=True)
    except Exception as exc:
        logger.warning("openrouter-credits panel: snapshot record failed: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    _store(payload)
    return {**payload, "cached": False}
