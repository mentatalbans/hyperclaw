"""Provider registry and capability router.

Route by capability over the providers the user actually configured —
never call, and never nag about, a provider that isn't configured.

Providers and slot ladders are declared in ``models.yaml`` (user copy in
``~/.hyperclaw/config/``, seeded from ``hyperclaw/default_config/``);
secrets live in ``.env``. A provider is LIVE only if every ``*_env`` it
names resolves to a non-empty value. Slot ladders are filtered to live
providers, so an unconfigured provider is simply invisible.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("hyperclaw.providers")

SLOTS = ("primary", "tools", "vision", "fast", "embeddings", "images")


@dataclass
class Provider:
    name: str
    kind: str                       # anthropic | openai_compat | openai
    capabilities: frozenset
    api_key_env: str = ""
    base_url_env: str = ""
    model_env: str = ""
    models: dict = field(default_factory=dict)   # e.g. {default: ..., fast: ...}

    @property
    def live(self) -> bool:
        for env in (self.api_key_env, self.base_url_env, self.model_env):
            if env and not os.environ.get(env, "").strip():
                return False
        if self.kind == "openai":
            # The native OpenAI provider is only live when OPENAI_* really
            # points at OpenAI. Users of the legacy OPENAI_BASE_URL compat
            # wiring (37afbb0) have a *different* endpoint behind these
            # vars — routing images/embeddings there with that key fails.
            base = os.environ.get("OPENAI_BASE_URL", "").strip()
            if base and "api.openai.com" not in base:
                return False
        return True

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""

    @property
    def base_url(self) -> str:
        return os.environ.get(self.base_url_env, "") if self.base_url_env else ""

    def model_for(self, key: str = "default") -> str:
        """Resolve a model id: model_env wins, then the models map."""
        if self.model_env:
            m = os.environ.get(self.model_env, "").strip()
            if m:
                return m
        return self.models.get(key) or self.models.get("default", "")


class ProviderRegistry:
    def __init__(self, providers: dict, slots: dict):
        self.providers = providers          # name -> Provider
        self.slots = slots                  # slot -> ["name" | "name:modelkey", ...]

    # ── construction ────────────────────────────────────────────────────
    @classmethod
    def load(cls) -> "ProviderRegistry":
        """Load providers/slots from the user models.yaml; if the user copy
        predates provider routing (no ``providers:`` section), fall back to
        the packaged default for those sections."""
        import yaml
        from hyperclaw.api_utils import find_config

        data = {}
        try:
            p = find_config("models.yaml")
            if p.exists():
                data = yaml.safe_load(p.read_text()) or {}
        except Exception as e:
            log.warning(f"models.yaml unreadable ({e}); using packaged defaults")
        if "providers" not in data:
            packaged = Path(__file__).resolve().parent / "default_config" / "models.yaml"
            try:
                pdata = yaml.safe_load(packaged.read_text()) or {}
                data.setdefault("providers", pdata.get("providers", {}))
                data.setdefault("slots", pdata.get("slots", {}))
            except Exception:
                pass

        providers = {}
        for name, cfg in (data.get("providers") or {}).items():
            providers[name] = Provider(
                name=name,
                kind=cfg.get("kind", "openai_compat"),
                capabilities=frozenset(cfg.get("capabilities", [])),
                api_key_env=cfg.get("api_key_env", ""),
                base_url_env=cfg.get("base_url_env", ""),
                model_env=cfg.get("model_env", ""),
                models=cfg.get("models") or {},
            )
        slots = {s: list(v) for s, v in (data.get("slots") or {}).items()}
        return cls(providers, slots)

    # ── queries ─────────────────────────────────────────────────────────
    def resolve(self, slot: str, required_capabilities=None) -> list:
        """Ordered (Provider, model_id) candidates for a slot: the ladder
        filtered to live providers that declare every required capability."""
        need = frozenset(required_capabilities or ())
        out = []
        for entry in self.slots.get(slot, []):
            name, _, model_key = entry.partition(":")
            prov = self.providers.get(name)
            if not prov or not prov.live or not need <= prov.capabilities:
                continue
            model = prov.model_for(model_key or "default")
            if not model and prov.kind in ("anthropic", "openai"):
                continue  # native providers need a concrete model id
            out.append((prov, model))
        return out

    def startup_line(self) -> str:
        parts = [f"{n} {'✓' if p.live else '–'}" for n, p in self.providers.items()]
        return "providers: " + "  ".join(parts)

    def slot_summary(self) -> dict:
        """slot -> human-readable resolution, for doctor and logs."""
        out = {}
        for slot in SLOTS:
            cands = self.resolve(slot)
            if cands:
                prov, model = cands[0]
                out[slot] = f"{prov.name}" + (f" ({model})" if model else "")
            elif slot == "embeddings":
                out[slot] = "local hash"
            else:
                out[slot] = "not configured"
        return out


# ── module-level singleton + served_by tracking ─────────────────────────
_registry: Optional[ProviderRegistry] = None
_served_by: str = ""


def registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry.load()
    return _registry


def reset_registry() -> None:
    """Testing hook: force a reload on next access."""
    global _registry
    _registry = None


def record_served_by(value: str) -> None:
    global _served_by
    _served_by = value


def get_served_by() -> str:
    return _served_by


async def stream_with_failover(candidates, attempt):
    """Stream from the first candidate that works.

    candidates: ordered (Provider, model) pairs from resolve().
    attempt(provider, model): async generator yielding output items.

    A provider that raises before its first item -> next rung,
    transparently. One that dies mid-stream -> the interruption is marked
    and the turn ENDS (never re-answer with another provider).
    """
    last_err = None
    for prov, model in candidates:
        yielded = False
        record_served_by(f"{prov.name}/{model}" if model else prov.name)
        try:
            async for item in attempt(prov, model):
                yielded = True
                yield item
            if yielded:
                return
            last_err = last_err or RuntimeError(f"{prov.name} returned an empty stream")
        except Exception as e:
            if yielded:
                yield ("text", f"\n[{prov.name} stream interrupted: {e}]")
                return
            log.warning(f"{prov.name} failed before first token, trying next rung: {e}")
            last_err = e
    record_served_by("")
    yield ("text", f"[Error: no provider could serve this turn: {last_err}]")
