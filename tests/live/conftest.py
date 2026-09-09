"""Isolated, explicit opt-in fixtures for an already installed Ollama model."""

import ast
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx
import pytest


@dataclass(frozen=True)
class OllamaTarget:
    url: str
    model: str
    version: str
    capabilities: frozenset[str]

    def require(self, capability):
        if capability not in self.capabilities:
            pytest.fail(
                f"Selected Ollama model {self.model!r} lacks {capability!r}; "
                "select an installed model with the required capability using --ollama-model.",
                pytrace=False,
            )


@pytest.fixture(scope="session")
def ollama_target(pytestconfig):
    url = pytestconfig.getoption("--ollama-url").rstrip("/")
    model = pytestconfig.getoption("--ollama-model")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        pytest.fail("--ollama-url must be an HTTP(S) base URL without credentials, query, or fragment", pytrace=False)
    try:
        with httpx.Client(timeout=10, trust_env=False) as client:
            version = client.get(f"{url}/api/version")
            version.raise_for_status()
            details = client.post(f"{url}/api/show", json={"model": model})
            details.raise_for_status()
        target = OllamaTarget(
            url, model, version.json()["version"],
            frozenset(details.json().get("capabilities", [])),
        )
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        pytest.fail(
            f"Ollama prerequisite failed for {model!r} at {url}: {exc}. "
            "Start Ollama and select an already installed model with --ollama-model; "
            "this suite never downloads models.", pytrace=False,
        )
    target.require("completion")
    return target


@pytest.fixture
def ollama_runtime_factory(ollama_target, tmp_path, monkeypatch, request):
    """Inject only Ollama; every fresh runtime shares this test's temporary disk."""
    root = tmp_path / "hyperclaw"
    config = root / "config"
    config.mkdir(parents=True)
    (config / "agents.yaml").write_text("agents: []\n", encoding="utf-8")
    (config / "models.yaml").write_text("providers: {}\nslots: {}\n", encoding="utf-8")
    monkeypatch.setenv("HYPERCLAW_ROOT", str(root))
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("HYPERCLAW_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", ollama_target.url)
    monkeypatch.setenv("OLLAMA_MODEL", ollama_target.model)
    monkeypatch.setenv("OLLAMA_THINK", "0")
    monkeypatch.setenv("HYPERCLAW_MAX_TOKENS", "256")
    monkeypatch.setenv("HYPERCLAW_REQUEST_TIMEOUT", "90")
    monkeypatch.setenv("HYPERCLAW_ENABLE_TOOLS", "0")
    monkeypatch.delenv("PERSONA_FILE", raising=False)
    # Ignore inherited proxies for this explicit provider, including localhost.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)

    # Imports happen after isolation, including the router's module-level lookup.
    from hyperclaw import providers
    from hyperclaw.inference import Inference

    provider = providers.Provider(
        "ollama", "anthropic",
        frozenset({"chat", "streaming", "tool_use", "images", "thinking"}),
        base_url_env="OLLAMA_BASE_URL", model_env="OLLAMA_MODEL",
    )
    registry = providers.ProviderRegistry(
        {"ollama": provider},
        {slot: ["ollama"] for slot in ("primary", "fast", "tools", "vision")},
    )
    monkeypatch.setattr(providers, "_registry", registry)
    from hyperclaw.memory_manager import MemoryManager
    from hyperclaw.model_router import ModelRouter
    from hyperclaw.orchestrator import Orchestrator

    usage = []
    completions = []
    started = time.monotonic()
    request.node.user_properties.extend([
        ("ollama_url", ollama_target.url), ("ollama_model", ollama_target.model),
        ("ollama_version", ollama_target.version),
    ])

    @asynccontextmanager
    async def runtime():
        router = ModelRouter(Inference(registry))
        account = router.inference.on_usage
        complete = router.inference.complete

        def record(metadata):
            account(metadata)
            usage.append(dict(metadata))

        async def observed_complete(*args, **kwargs):
            response, metadata = await complete(*args, **kwargs)
            completions.append({
                "model": metadata["model"], "max_tokens": kwargs.get("max_tokens"),
                "stop_reason": response.stop_reason,
                "content_types": [block.type for block in response.content],
                "tool_names": [block.name for block in response.content if block.type == "tool_use"],
            })
            return response, metadata

        router.inference.on_usage = record
        router.inference.complete = observed_complete
        app = Orchestrator(model_router=router, memory=MemoryManager(root=root))
        try:
            await app.initialize()
            yield app
        finally:
            await app.shutdown()

    yield runtime
    request.node.user_properties.extend([
        ("ollama_elapsed_seconds", round(time.monotonic() - started, 3)),
        ("ollama_usage", json.dumps(usage, sort_keys=True)),
        ("ollama_completions", json.dumps(completions, sort_keys=True)),
    ])


@pytest.fixture
def canonical_memory_schemas():
    # Consume the shipped schemas without importing TUI's computer integrations.
    path = Path(__file__).resolve().parents[2] / "hyperclaw" / "tui.py"
    assignment = next(
        node for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "TOOLS" for target in node.targets)
    )
    return {
        schema["name"]: schema for schema in ast.literal_eval(assignment.value)
        if schema["name"] in {"memory_store", "memory_search"}
    }
