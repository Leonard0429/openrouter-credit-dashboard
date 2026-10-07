#!/usr/bin/env python3
"""OpenRouter credit watchdog — daily snapshot + low-balance alert.

Designed to run as a Hermes cron job with ``no_agent=True``: the scheduler
executes this script each tick and delivers its stdout verbatim. **Empty stdout
sends nothing**, so a healthy run is completely silent and costs zero tokens.

Every run records a snapshot into the same history file the ``openrouter_credits``
tool uses, so a silent run still contributes to the burn-rate measurement. An
alert is emitted only when the balance falls below the threshold.

Install:
    cp openrouter-credit-watch.py "$HERMES_HOME/scripts/"
    hermes cron create "OpenRouter credit watchdog" \
        --schedule "every day at 9am" \
        --script openrouter-credit-watch.py --no-agent --deliver bot-chat

Threshold resolution: ``OPENROUTER_CREDIT_THRESHOLD`` env var, else THRESHOLD.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

THRESHOLD = 5.00  # alert when the remaining balance drops below this (USD)
TOPUP_URL = "https://openrouter.ai/settings/credits"


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    try:
        from hermes_cli.config import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


def find_plugin_tools() -> Path:
    """Locate the plugin's ``tools.py``: installed copy first, then this repo."""
    candidates = [
        hermes_home() / "plugins" / "openrouter-credits" / "tools.py",
        Path(__file__).resolve().parent / "tools.py",           # repo root layout
        Path(__file__).resolve().parent.parent / "tools.py",    # examples/ layout
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError(
        "openrouter-credits plugin not found; install it with "
        "`hermes plugins install openrouter-credits`"
    )


def load_plugin_tools():
    """Import the plugin's tools.py by path — single source of truth for the fetch."""
    spec = importlib.util.spec_from_file_location("openrouter_credits_tools", find_plugin_tools())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def threshold() -> float:
    try:
        return float((os.environ.get("OPENROUTER_CREDIT_THRESHOLD") or "").strip())
    except ValueError:
        return THRESHOLD


def main() -> int:
    tools = load_plugin_tools()

    snapshot = tools.fetch_snapshot()
    history = tools._read_history()
    tools._append_history(snapshot)          # always record, even on a silent run
    history = history + [snapshot]

    balance = snapshot["balance"]
    limit = threshold()
    if balance >= limit:
        return 0  # healthy: print nothing, deliver nothing

    lines = [
        f"⚠️ OpenRouter credit low: ${max(0.0, balance):,.2f} left "
        f"(threshold ${limit:,.2f})",
    ]
    rate = tools.burn_rate(history)
    if rate and rate["per_day"] > 0:
        lines.append(f"Burn rate: ~${rate['per_day']:.2f}/day → "
                     f"~{max(0.0, balance) / rate['per_day']:.1f} days left")
    else:
        weekly = snapshot.get("usage_weekly")
        if weekly:
            per_day = weekly / 7.0
            lines.append(f"Recent spend: ~${per_day:.2f}/day → "
                         f"~{max(0.0, balance) / per_day:.1f} days left")
    lines.append(f"Top up: {TOPUP_URL}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Delivered as a message — a broken watchdog must not fail silently.
        print(f"⚠️ OpenRouter credit watchdog failed: {type(exc).__name__}: {exc}")
        sys.exit(0)
