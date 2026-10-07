"""OpenRouter credit tracking — the ``openrouter_credits`` tool.

Reads OpenRouter's ``/credits`` and ``/key`` endpoints with the credential
Hermes already uses for inference, prints balance + spend windows, appends a
timestamped snapshot to a local history file, and derives a burn rate and an
estimated runway from that history.

Stdlib only (urllib) so it loads in every process (CLI, TUI, Desktop, gateway,
cron) without adding dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_HTTP_TIMEOUT = 15
_HISTORY_KEEP = 2000  # snapshots retained before the file is compacted


# --------------------------------------------------------------------------
# Paths + credentials
# --------------------------------------------------------------------------

def _hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    try:
        from hermes_cli.config import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


def _history_path() -> Path:
    return _hermes_home() / "cache" / "openrouter-credits" / "history.jsonl"


def _scoped_key() -> Optional[str]:
    """``OPENROUTER_API_KEY`` through Hermes' secret scope.

    ``agent.secret_scope.get_secret`` is profile-aware and multiplex-safe (it
    resolves the *served* profile's secrets, and owns the ``.env`` parsing), so
    the plugin never hand-rolls its own credential lookup or caches a home.
    Falls back to the process environment for bare invocations.
    """
    try:
        from agent.secret_scope import get_secret

        value = (get_secret("OPENROUTER_API_KEY") or "").strip()
        if value:
            return value
    except Exception:
        logger.debug("openrouter-credits: scoped secret lookup unavailable", exc_info=True)
    return (os.environ.get("OPENROUTER_API_KEY") or "").strip() or None


def resolve_credentials() -> tuple[Optional[str], str]:
    """Resolve ``(api_key, base_url)`` for OpenRouter.

    Prefers Hermes's own runtime resolver (honours config, .env, credential
    pools and command-minted keys); falls back to the plain environment and the
    profile .env so the tool still works from a bare process.
    """
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested="openrouter")
        key = str(runtime.get("api_key") or "").strip()
        base = str(runtime.get("base_url") or "").strip()
        if key:
            return key, (base or DEFAULT_BASE_URL).rstrip("/")
    except Exception:
        logger.debug("openrouter-credits: runtime resolver unavailable", exc_info=True)

    key = _scoped_key() or ""
    base = DEFAULT_BASE_URL
    try:
        from hermes_cli.config import load_config_readonly

        configured = ((load_config_readonly() or {}).get("model") or {}).get("base_url")
        if configured:
            base = str(configured)
    except Exception:
        pass
    return (key or None), base.rstrip("/")


def has_openrouter_key() -> bool:
    """check_fn gate: only surface the tool when a key actually resolves."""
    try:
        return bool(resolve_credentials()[0])
    except Exception:
        return False


def _get_json(url: str, api_key: str) -> dict:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:  # noqa: S310 (fixed host)
        return json.loads(response.read().decode("utf-8")) or {}


# --------------------------------------------------------------------------
# Fetch + snapshot
# --------------------------------------------------------------------------

def fetch_snapshot() -> dict:
    """One reading of OpenRouter's credit state. Raises on credential/network failure."""
    api_key, base_url = resolve_credentials()
    if not api_key:
        raise RuntimeError(
            "No OpenRouter API key found. Set OPENROUTER_API_KEY in ~/.hermes/.env "
            "or configure model.provider: openrouter."
        )

    credits = (_get_json(f"{base_url}/credits", api_key).get("data") or {})
    try:
        key_data = _get_json(f"{base_url}/key", api_key).get("data") or {}
    except Exception:
        key_data = {}  # /key is optional metadata; never fail the whole reading for it

    total_credits = float(credits.get("total_credits") or 0.0)
    total_usage = float(credits.get("total_usage") or 0.0)
    now = datetime.now(timezone.utc)
    return {
        "ts": now.isoformat(timespec="seconds"),
        "epoch": now.timestamp(),
        "total_credits": total_credits,
        "total_usage": total_usage,
        "balance": total_credits - total_usage,
        "usage_daily": _as_float(key_data.get("usage_daily")),
        "usage_weekly": _as_float(key_data.get("usage_weekly")),
        "usage_monthly": _as_float(key_data.get("usage_monthly")),
        "key_limit": _as_float(key_data.get("limit")),
        "key_limit_remaining": _as_float(key_data.get("limit_remaining")),
        "key_limit_reset": key_data.get("limit_reset"),
    }


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------

def _read_history() -> list[dict]:
    path = _history_path()
    if not path.is_file():
        return []
    entries: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and "epoch" in record:
                entries.append(record)
    except OSError:
        return []
    return entries


def _append_history(entry: dict) -> int:
    """Append one snapshot, compacting the file when it grows past the cap."""
    path = _history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_history()
        if len(existing) + 1 > _HISTORY_KEEP:
            existing = existing[-(_HISTORY_KEEP - 1):]
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in existing), encoding="utf-8"
            )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        return len(existing) + 1
    except OSError as exc:
        logger.debug("openrouter-credits: could not write history (%s)", exc)
        return 0


# --------------------------------------------------------------------------
# Burn rate
# --------------------------------------------------------------------------

def burn_rate(history: list[dict], *, days: int = 7, min_span_days: float = 0.25) -> Optional[dict]:
    """Average spend per day over the trailing window, from snapshot deltas.

    Uses total spend deltas between the oldest in-window snapshot and the newest
    one, so a top-up (which changes balance but not lifetime spend) never
    distorts the rate.

    Returns None when the history is too thin to measure — including when the
    covered span is under ``min_span_days``. Two snapshots taken minutes apart
    imply a tiny span, and dividing a rounding-level spend delta by it produces
    a wildly inflated rate (``$0.05 over 0.001 days`` -> ``$50/day``), so short
    spans fall through to the rolling-window estimate instead.
    """
    if not history:
        return None
    newest = history[-1]
    cutoff = newest["epoch"] - days * 86400
    window = [row for row in history if row["epoch"] >= cutoff]
    if len(window) < 2:
        return None
    oldest = window[0]
    span_days = (newest["epoch"] - oldest["epoch"]) / 86400.0
    if span_days < min_span_days:
        return None
    spent = float(newest.get("total_usage") or 0.0) - float(oldest.get("total_usage") or 0.0)
    if spent <= 0:
        return None
    return {
        "per_day": spent / span_days,
        "span_days": span_days,
        "spent": spent,
        "samples": len(window),
        "source": f"{days}-day history",
    }


def _estimated_line(snapshot: dict, history: list[dict], window_days: int) -> list[str]:
    """Runway estimate: from measured history when available, else published windows."""
    rate = burn_rate(history, days=window_days)
    lines: list[str] = []
    if rate and rate["per_day"] > 0:
        lines.append(
            f"Burn rate: ~${rate['per_day']:.2f}/day "
            f"({rate['source']}, ${rate['spent']:.2f} over {rate['span_days']:.1f} days)"
        )
        balance = snapshot.get("balance") or 0.0
        if balance > 0:
            lines.append(f"Est. runway: ~{balance / rate['per_day']:.1f} days at the current rate")
        else:
            lines.append("Est. runway: exhausted — top up to restore access")
        return lines

    # No measured history yet: fall back to OpenRouter's own rolling windows.
    fallback = [(snapshot.get("usage_weekly"), 7.0, "7-day spend"),
                (snapshot.get("usage_monthly"), 30.0, "30-day spend")]
    for amount, divisor, label in fallback:
        if amount and amount > 0:
            per_day = amount / divisor
            balance = snapshot.get("balance") or 0.0
            lines.append(f"Approx burn rate: ~${per_day:.2f}/day (from {label})")
            if balance > 0:
                lines.append(f"Est. runway: ~{balance / per_day:.1f} days at that rate")
            break
    if not lines:
        lines.append("Burn rate: not enough data yet — history builds each time this tool runs")
    return lines


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render_report(snapshot: dict, history: list[dict], *, window_days: int = 7) -> str:
    balance = snapshot.get("balance") or 0.0
    total_credits = snapshot.get("total_credits") or 0.0
    total_usage = snapshot.get("total_usage") or 0.0

    if balance <= 0:
        headline = f"⛔ Balance: ${max(0.0, balance):.2f} — credit exhausted, top up to restore access"
    elif total_credits > 0 and balance / total_credits < 0.15:
        headline = f"⚠️ Balance: ${balance:,.2f} (under 15% of credit left)"
    else:
        headline = f"💳 Balance: ${balance:,.2f}"

    lines = ["OpenRouter credit", headline,
             f"Lifetime: ${total_usage:,.2f} spent of ${total_credits:,.2f} purchased"]

    windows = []
    for key, label in (("usage_daily", "today"), ("usage_weekly", "this week"),
                       ("usage_monthly", "this month")):
        value = snapshot.get(key)
        if value is not None:
            windows.append(f"${value:,.2f} {label}")
    if total_usage:
        windows.append(f"${total_usage:,.2f} all time")
    if windows:
        lines.append("Spend: " + " • ".join(windows))

    limit = snapshot.get("key_limit")
    if limit and limit > 0:
        remaining = snapshot.get("key_limit_remaining") or 0.0
        line = f"API key cap: ${remaining:,.2f} of ${limit:,.2f} remaining"
        if snapshot.get("key_limit_reset"):
            line += f" (resets {snapshot['key_limit_reset']})"
        lines.append(line)
    else:
        lines.append("API key cap: none — no spend limit set on this key")

    lines.extend(_estimated_line(snapshot, history, window_days))

    if len(history) > 1:
        first = datetime.fromtimestamp(history[0]["epoch"], tz=timezone.utc).date().isoformat()
        lines.append(f"History: {len(history)} snapshot(s) since {first} — {_history_path()}")
    else:
        lines.append(f"History: this is the first snapshot — {_history_path()}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Tool handler + registration
# --------------------------------------------------------------------------

SCHEMA = {
    "name": "openrouter_credits",
    "description": (
        "Report OpenRouter credit: current balance, spend for today / this week / this month / "
        "all time, any spend cap on the API key, the measured burn rate and estimated days of "
        "credit remaining. Records a timestamped snapshot each call so burn rate improves over "
        "time. Use when the user asks how much credit is left, how much they have spent, what "
        "the spend rate is, or whether a top-up is needed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "window_days": {
                "type": "integer",
                "description": "Trailing window in days used to measure burn rate (default 7).",
            },
            "record": {
                "type": "boolean",
                "description": "Append a snapshot to the local history file (default true).",
            },
        },
    },
}


def handle_openrouter_credits(args: dict, **kwargs: Any) -> str:
    """Tool handler: fetch, record, render."""
    args = args or {}
    try:
        window_days = int(args.get("window_days") or 7)
    except (TypeError, ValueError):
        window_days = 7
    window_days = min(max(window_days, 1), 365)
    record = args.get("record")
    record = True if record is None else bool(record)

    try:
        snapshot = fetch_snapshot()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return (f"OpenRouter rejected the credential (HTTP {exc.code}). Check that "
                    "OPENROUTER_API_KEY in ~/.hermes/.env is valid and not revoked.")
        return f"OpenRouter API error (HTTP {exc.code}) while reading credit — try again shortly."
    except urllib.error.URLError as exc:
        return f"Could not reach openrouter.ai: {exc.reason}."
    except RuntimeError as exc:
        return str(exc)

    history = _read_history()
    if record:
        _append_history(snapshot)
        history = history + [snapshot]

    return render_report(snapshot, history, window_days=window_days)


def register_tools(ctx) -> None:
    """Register the ``openrouter_credits`` tool (deferred-plugin path)."""
    ctx.register_tool(
        name="openrouter_credits",
        toolset="openrouter",
        schema=SCHEMA,
        handler=handle_openrouter_credits,
        description=SCHEMA["description"],
        emoji="💳",
        check_fn=has_openrouter_key,
    )
