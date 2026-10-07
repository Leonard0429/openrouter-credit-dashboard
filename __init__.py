"""openrouter-credits — track OpenRouter credit balance, spend and burn rate.

Standalone user plugin: registers one tool (``openrouter_credits``) into the
``openrouter`` toolset. The tool is gated on an OpenRouter key resolving, so it
costs nothing in sessions that never touch OpenRouter.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_tools():
    """Import the sibling ``tools`` module (relative import, path fallback)."""
    try:
        from . import tools as _tools  # type: ignore[import-not-found]

        return _tools
    except Exception:
        spec = importlib.util.spec_from_file_location(
            "openrouter_credits_tools", Path(__file__).with_name("tools.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module


def register(ctx) -> None:
    """Register tools with the plugin context (called once by the plugin loader)."""
    _load_tools().register_tools(ctx)
