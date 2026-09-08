"""Provider-backed inference shared by chat, tasks, and terminal adapters.

The internal message format is the Messages API format already used by the
tool layer. Ollama implements that format directly; OpenAI-compatible text
providers are adapted here. Every request uses one finite candidate list.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import AsyncIterator

import anthropic
import httpx

from .providers import ProviderRegistry, record_served_by, registry

_active_inference = ContextVar("hyperclaw_tool_inference", default=None)


@contextmanager
def inference_context(inference):
    """Propagate the selected transport to model-backed tools in worker threads."""
    token = _active_inference.set(inference)
    try:
        yield
    finally:
        _active_inference.reset(token)


def current_inference():
    return _active_inference.get() or Inference()


class Inference:
    def __init__(self, providers: ProviderRegistry | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.providers = providers or registry()
        self.transport = transport
        self.route_slot = None
        self.on_usage = None

    def candidates(self, slot="primary", required_capabilities=None, model_override=None):
        if self.route_slot:
            slot = self.route_slot(slot)
        candidates = self.providers.resolve(slot, required_capabilities or {"chat"})
        if model_override:
            matches = [(p, model_override) for p, model in candidates
                       if model_override == model or model_override in p.models.values()]
            if not matches:
                raise ValueError(f"Model {model_override!r} is not configured for {slot}")
            candidates = matches
        # Collapsed aliases must not retry the same endpoint/model.
        return list({(p.name, m): (p, m) for p, m in candidates}.values())

    def _client(self):
        return httpx.AsyncClient(
            timeout=float(os.environ.get("HYPERCLAW_REQUEST_TIMEOUT", "120")),
            transport=self.transport,
        )

    @staticmethod
    def _endpoint(provider):
        if provider.kind == "anthropic":
            base = provider.base_url or "https://api.anthropic.com"
            base = base.rstrip("/")
            return base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        return (provider.base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"

    @staticmethod
    def _headers(provider):
        if provider.kind == "anthropic":
            return {"x-api-key": provider.api_key or "ollama", "anthropic-version": "2023-06-01"}
        return {"Authorization": f"Bearer {provider.api_key or 'ollama'}"}

    @staticmethod
    async def _check(response, provider):
        if response.is_error:
            await response.aread()
            try:
                detail = response.json().get("error", {})
                detail = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
            except (ValueError, AttributeError):
                detail = response.reason_phrase
            raise ProviderError(f"{provider.name}: {detail[:500]}", response.status_code)

    @staticmethod
    def _request(provider, model, messages, system, max_tokens, tools=None):
        system = (system + f"\nThe model serving this request is {model} through {provider.name}.").strip()
        args = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if provider.kind == "anthropic":
            if system:
                args["system"] = system
            if tools:
                args["tools"] = tools
            if provider.name == "ollama":
                args["thinking"] = {"type": "enabled", "budget_tokens": 1024} if os.environ.get("OLLAMA_THINK", "0").lower() in ("1", "true", "yes") else {"type": "disabled"}
            return args
        args["messages"] = _openai_messages(messages, system)
        if tools:
            args["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t.get("description", ""),
                "parameters": t["input_schema"]}} for t in tools]
        return args

    @staticmethod
    def _retryable(exc):
        if isinstance(exc, httpx.TransportError):
            return True
        status = getattr(exc, "status_code", None)
        return status in (0, 404, 408, 429) or (status is not None and status >= 500)

    async def complete(self, messages, system="", *, slot="primary", model_override=None,
                       max_tokens=4096, tools=None, required_capabilities=None):
        need = set(required_capabilities or {"chat"}) | message_capabilities(messages)
        if tools:
            need.add("tool_use")
        candidates = self.candidates(slot, need, model_override)
        if not candidates:
            raise RuntimeError(f"No configured provider supports {slot}: {', '.join(sorted(need))}")
        started = time.monotonic()
        for index, (provider, model) in enumerate(candidates):
            try:
                async with self._client() as client:
                    args = self._request(provider, model, messages, system, max_tokens, tools)
                    wire = await client.post(self._endpoint(provider), json=args, headers=self._headers(provider))
                    await self._check(wire, provider)
                    data = wire.json()
                    if provider.kind == "anthropic":
                        response = anthropic.types.Message.model_validate(data)
                    else:
                        response = _anthropic_response(data)
                record_served_by(f"{provider.name}/{model}")
                metadata = {"provider": provider.name, "model": model,
                    "model_name": model, "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "latency_ms": int((time.monotonic() - started) * 1000)}
                if self.on_usage:
                    self.on_usage(metadata)
                return response, metadata
            except Exception as exc:
                if not self._retryable(exc) or index == len(candidates) - 1:
                    raise

    async def stream_events(self, messages, system="", *, slot="primary", model_override=None,
                            max_tokens=4096, required_capabilities=None) -> AsyncIterator[tuple[str, str]]:
        need = set(required_capabilities or {"chat"}) | message_capabilities(messages) | {"streaming"}
        candidates = self.candidates(slot, need, model_override)
        if not candidates:
            raise RuntimeError(f"No configured provider supports {slot}: {', '.join(sorted(need))}")
        for index, (provider, model) in enumerate(candidates):
            emitted = False
            accepted = False
            usage = {"input_tokens": 0, "output_tokens": 0}
            try:
                async with self._client() as client:
                    args = self._request(provider, model, messages, system, max_tokens)
                    args["stream"] = True
                    if provider.kind != "anthropic":
                        args["stream_options"] = {"include_usage": True}
                    record_served_by(f"{provider.name}/{model}")
                    finished = False
                    async with client.stream("POST", self._endpoint(provider), json=args,
                                             headers=self._headers(provider)) as wire:
                        await self._check(wire, provider)
                        accepted = True
                        async for line in wire.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                finished = True
                                break
                            data = json.loads(raw)
                            counts = data.get("message", {}).get("usage") or data.get("usage") or {}
                            for target, source in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens"),
                                                   ("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
                                if source in counts:
                                    usage[target] = counts[source]
                            if data.get("type") == "error":
                                raise ProviderError(str(data.get("error", "Stream failed")), 0)
                            if data.get("type") == "message_stop":
                                finished = True
                                break
                            if provider.kind == "anthropic":
                                delta = data.get("delta", {})
                                if data.get("type") == "content_block_delta":
                                    kind = delta.get("type")
                                    if kind == "text_delta" and delta.get("text"):
                                        emitted = True
                                        yield "text", delta["text"]
                                    elif kind == "thinking_delta" and delta.get("thinking"):
                                        emitted = True
                                        yield "thinking", delta["thinking"]
                            else:
                                choices = data.get("choices", [])
                                text = choices[0].get("delta", {}).get("content") if choices else None
                                if text:
                                    emitted = True
                                    yield "text", text
                    if not finished:
                        raise ProviderError("Stream ended before its completion marker", 0)
                if not emitted:
                    raise ProviderError(f"{provider.name} returned an empty stream", 0)
                return
            except Exception as exc:
                if emitted:
                    yield "text", f"\n[{provider.name} stream interrupted: {exc}]"
                    raise
                if not self._retryable(exc) or index == len(candidates) - 1:
                    raise
            finally:
                if accepted and self.on_usage:
                    self.on_usage({"provider": provider.name, "model": model, **usage})

    def complete_sync(self, *args, **kwargs):
        """Terminal/worker-thread adapter; async callers use complete directly."""
        return asyncio.run(self.complete(*args, **kwargs))


def message_capabilities(messages):
    """Check every outgoing block, including history and nested tool results."""
    needs = {"chat"}
    def visit(value):
        if isinstance(value, list):
            for block in value:
                visit(block)
        elif isinstance(value, dict):
            if value.get("type") in ("image", "image_url"):
                needs.add("images")
            elif value.get("type") == "document":
                needs.add("documents")
            visit(value.get("content"))
    visit(messages)
    return needs


def _openai_messages(messages, system):
    out = [{"role": "system", "content": system}] if system else []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            out.append({"role": message["role"], "content": content})
            continue
        parts, calls, results = [], [], []
        for block in content:
            kind = block.get("type")
            if kind == "text":
                parts.append({"type": "text", "text": block["text"]})
            elif kind == "image":
                source = block["source"]
                parts.append({"type": "image_url", "image_url": {"url": f"data:{source['media_type']};base64,{source['data']}"}})
            elif kind == "tool_use":
                calls.append({"id": block["id"], "type": "function", "function": {
                    "name": block["name"], "arguments": json.dumps(block["input"])}})
            elif kind == "tool_result":
                results.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": str(block["content"])})
            elif kind not in ("thinking", "redacted_thinking"):
                raise ValueError(f"Unsupported content block: {kind}")
        if parts or calls:
            item = {"role": message["role"], "content": parts or None}
            if calls:
                item["tool_calls"] = calls
            out.append(item)
        out.extend(results)
    return out


class ProviderError(RuntimeError):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def _anthropic_response(response):
    choice = response["choices"][0]
    msg = choice["message"]
    blocks = [{"type": "text", "text": msg["content"]}] if msg.get("content") else []
    for call in msg.get("tool_calls") or []:
        blocks.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"],
                       "input": json.loads(call["function"]["arguments"])})
    usage = response.get("usage") or {}
    reason = "tool_use" if msg.get("tool_calls") else "max_tokens" if choice.get("finish_reason") == "length" else "end_turn"
    return anthropic.types.Message.model_validate({
        "id": response["id"], "type": "message", "role": "assistant", "model": response["model"],
        "content": blocks, "stop_reason": reason, "stop_sequence": None,
        "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                  "output_tokens": usage.get("completion_tokens", 0)}})
