"""
Model selector — tiered Claude routing.

Picks the right Claude model per request so the assistant switches across the whole ladder instead of
running one model for everything:

    Routes by prompt complexity across THREE tiers (no Haiku, per policy):
    SONNET  (claude-sonnet-5)          -> simple/everyday requests, quick questions, acks
    OPUS    (claude-opus-5)            -> complex: analysis, planning, writing, research, judgement
    FABLE   (claude-fable-5, ACTIVE)   -> hardest: code, architecture, quant, deep multi-step work; also the default backend model

Model IDs are env-overridable so the exact Fable id can be dropped in without code changes:
    FABLE_MODEL        the new model's API id   (defaults to claude-fable-5)
    HYPERCLAW_OPUS_MODEL / HYPERCLAW_SONNET_MODEL / HYPERCLAW_HAIKU_MODEL
Disable routing entirely with HYPERCLAW_MODEL_ROUTING=off (everything uses the passed default).
"""

from __future__ import annotations

import os
import re

DEFAULT_MODEL = "claude-fable-5"


def tiers() -> dict:
    return {
        # FABLE: claude-fable-5 (activated 2026-08-20). FABLE_MODEL env still overrides.
        "fable": (os.environ.get("FABLE_MODEL", "").strip() or DEFAULT_MODEL),
        "opus": os.environ.get("HYPERCLAW_OPUS_MODEL", "claude-opus-5"),
        "sonnet": os.environ.get("HYPERCLAW_SONNET_MODEL", "claude-sonnet-5"),
        "haiku": os.environ.get("HYPERCLAW_HAIKU_MODEL", "claude-haiku-4-5"),
    }


# Hardest tier (FABLE): deep technical / engineering / quantitative / heavy multi-step work.
_FABLE_RE = re.compile(
    r"\b(code|coding|debug|refactor|architect|architecture|algorithm|implement|build (a|an|the|me|out)|"
    r"design (a|an|the|system|schema)|migrat|optimi[sz]|prove|valuation|forecast|financial model|"
    r"legal|contract|root cause|deep dive|end.to.end|whole (system|codebase)|"
    r"think (hard|deeply|carefully|step)|step.?by.?step|trading strateg|portfolio)\b",
    re.IGNORECASE,
)

# Complex tier (OPUS): analysis, planning, writing, research, judgement.
_OPUS_RE = re.compile(
    r"\b(analy[sz]e|analysis|plan|planning|research|draft|write (a|an|the|me)|essay|memo|report|"
    r"compar|evaluat|assess|recommend|strateg|decision|review|investor|proposal|negotiat|"
    r"summari[sz]e|explain (why|how)|reason|brief|pitch|roadmap|tradeoff|pros and cons)\b",
    re.IGNORECASE,
)


def classify(message: str) -> str:
    """Return a tier by prompt complexity: 'sonnet' (simple) | 'opus' (complex) | 'fable' (hardest).

    Haiku tier is available via env override; default routing uses Sonnet/Opus/Fable."""
    msg = (message or "").strip()
    words = len(msg.split())
    if _FABLE_RE.search(msg) or words >= 120:
        return "fable"
    if _OPUS_RE.search(msg) or words >= 30:
        return "opus"
    return "sonnet"


def pick_model(message: str, default: str = None) -> str:
    """Pick a concrete model id for this request. Falls back to `default` when routing is off."""
    default = default or DEFAULT_MODEL
    if os.environ.get("HYPERCLAW_MODEL_ROUTING", "on").lower() in ("off", "0", "false", "no"):
        return default
    return tiers()[classify(message)]
