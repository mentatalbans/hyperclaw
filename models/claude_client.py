"""
ClaudeClient — async Anthropic API client with retry logic, streaming, and cost tracking.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

import os

import anthropic

log = logging.getLogger("hyperclaw.claude_client")

# Cost per 1K tokens (approximate, subject to Anthropic pricing changes)
_INPUT_COST_PER_1K = 0.003
_OUTPUT_COST_PER_1K = 0.015


class ClaudeClient:
    """
    Async Anthropic Claude client.

    Retries up to 3 times with exponential backoff on transient errors.
    Logs every call: model, token counts, latency, and cost estimate.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 4096,
        base_url: str | None = None,
    ) -> None:
        resolved_base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        prov = os.environ.get("LLM_PROVIDER", "anthropic")
        self._provider = prov
        if prov == "bedrock":
            pass  # no client needed — uses boto3 per-call
        elif prov == "openai_compat":
            import openai as _oai
            self._oai_client = _oai.AsyncOpenAI(
                api_key=api_key or os.environ.get("OPENAI_API_KEY", "placeholder"),
                base_url=resolved_base_url or os.environ.get("OPENAI_BASE_URL"),
            )
        else:
            # anthropic or anthropic_compat
            kwargs: dict = {"api_key": api_key or None}
            base = resolved_base_url or os.environ.get("ANTHROPIC_BASE_URL")
            if base:
                kwargs["base_url"] = base
            self._client = anthropic.AsyncAnthropic(**kwargs)
        self.model = model
        self.max_tokens = max_tokens

    async def chat(
        self,
        messages: list[dict],
        system: str = "",
    ) -> str:
        """
        Send a chat request with retry logic (3 attempts, exponential backoff).
        Returns the assistant's text response.
        """
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await self._do_chat(messages, system)
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
                last_error = e
                wait = 2 ** attempt
                log.warning(f"Claude transient error (attempt {attempt+1}/3): {e}. Retrying in {wait}s...")
                await asyncio.sleep(wait)
            except anthropic.RateLimitError as e:
                last_error = e
                wait = 4 ** attempt
                log.warning(f"Claude rate limit (attempt {attempt+1}/3). Retrying in {wait}s...")
                await asyncio.sleep(wait)
        raise last_error or RuntimeError("Claude chat failed after 3 attempts")

    async def _do_chat(self, messages: list[dict], system: str) -> str:
        if self._provider == "bedrock":
            return await self._do_chat_bedrock(messages, system)
        if self._provider == "openai_compat":
            return await self._do_chat_openai(messages, system)
        t0 = time.time()
        kwargs: dict = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
        )
        if system:
            kwargs["system"] = system

        response = await self._client.messages.create(**kwargs)
        latency_ms = (time.time() - t0) * 1000

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = (input_tokens / 1000 * _INPUT_COST_PER_1K) + (output_tokens / 1000 * _OUTPUT_COST_PER_1K)
        # Handle text and thinking content blocks
        text = ""
        for block in (response.content or []):
            if hasattr(block, "type") and block.type == "text":
                text = block.text
                break
        if not text and response.content:
            text = getattr(response.content[0], "text", "") or ""

        log.info(
            f"Claude call | model={self.model} | "
            f"in={input_tokens} out={output_tokens} | "
            f"latency={latency_ms:.0f}ms | cost=${cost:.5f}"
        )
        return text

    async def _do_chat_bedrock(self, messages: list[dict], system: str) -> str:
        try:
            from core.providers.llm import BedrockProvider, Message as BMsg
        except ImportError:
            import sys as _sys, pathlib as _pl
            _sys.path.insert(0, str(_pl.Path(__file__).parent.parent))
            from core.providers.llm import BedrockProvider, Message as BMsg
        t0 = time.time()
        provider = BedrockProvider(
            access_key=os.environ.get("AWS_ACCESS_KEY_ID"),
            secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region=os.environ.get("AWS_REGION", "us-east-1"),
        )
        bedrock_model = os.environ.get("BEDROCK_MODEL", "anthropic.claude-sonnet-5")
        bmsgs = [BMsg(role=m["role"], content=m["content"]) for m in messages]
        response = await provider.complete(
            messages=bmsgs,
            model=bedrock_model,
            max_tokens=self.max_tokens,
            system=system or None,
        )
        latency_ms = (time.time() - t0) * 1000
        log.info(
            f"Bedrock call | model={bedrock_model} | "
            f"in={response.input_tokens} out={response.output_tokens} | "
            f"latency={latency_ms:.0f}ms"
        )
        return response.content

    async def _do_chat_openai(self, messages: list[dict], system: str) -> str:
        t0 = time.time()
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        oai_messages.extend(messages)
        model = os.environ.get("OPENAI_MODEL", self.model)
        try:
            response = await self._oai_client.chat.completions.create(
                model=model,
                messages=oai_messages,
                max_tokens=self.max_tokens,
            )
        except Exception as _e:
            _err = str(_e).lower()
            if any(k in _err for k in ("connection", "connect", "unreachable", "refused", "timeout")):
                log.warning(f"OpenAI-compat unreachable ({_e}), falling back to Bedrock")
                import re as _re
                from pathlib import Path as _Path
                _bedrock_model = os.environ.get("BEDROCK_MODEL", "us.anthropic.claude-sonnet-5")
                os.environ["LLM_PROVIDER"] = "bedrock"
                os.environ.setdefault("AWS_REGION", "us-west-2")
                os.environ.setdefault("AWS_PROFILE", "hyper")
                os.environ["BEDROCK_MODEL"] = _bedrock_model
                _env_f = _Path.home() / ".hyperclaw" / ".env"
                if _env_f.exists():
                    _txt = _env_f.read_text()
                    _txt = _re.sub(r'^LLM_PROVIDER=.*$', 'LLM_PROVIDER=bedrock', _txt, flags=_re.MULTILINE)
                    _env_f.write_text(_txt)
                self._provider = "bedrock"
                return await self._do_chat_bedrock(messages, system)
            raise
        latency_ms = (time.time() - t0) * 1000
        usage = response.usage
        log.info(
            f"OpenAI-compat call | model={model} | "
            f"in={usage.prompt_tokens if usage else '?'} "
            f"out={usage.completion_tokens if usage else '?'} | "
            f"latency={latency_ms:.0f}ms"
        )
        return response.choices[0].message.content or ""

    async def chat_stream(
        self,
        messages: list[dict],
        system: str = "",
    ) -> AsyncIterator[str]:
        """
        Stream a chat response. Yields text chunks as they arrive.
        """
        if self._provider == "bedrock":
            # BedrockProvider has no streaming; yield full response as single chunk
            text = await self._do_chat_bedrock(messages, system)
            yield text
            return

        if self._provider == "openai_compat":
            oai_messages = []
            if system:
                oai_messages.append({"role": "system", "content": system})
            oai_messages.extend(messages)
            model = os.environ.get("OPENAI_MODEL", self.model)
            async with self._oai_client.chat.completions.create(
                model=model,
                messages=oai_messages,
                max_tokens=self.max_tokens,
                stream=True,
            ) as stream:
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        yield delta
            return

        # anthropic / anthropic_compat
        kwargs: dict = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
        )
        if system:
            kwargs["system"] = system

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
