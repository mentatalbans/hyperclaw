"""
Image Provider Abstraction Layer.
Unified interface for image generation and vision providers.
"""
from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ImageResult:
    """Image generation result."""
    url: str | None = None
    data: bytes | None = None
    revised_prompt: str | None = None
    raw: dict | None = None


@dataclass
class VisionResult:
    """Vision/image understanding result."""
    content: str
    raw: dict | None = None


class ImageProvider(ABC):
    """Abstract base for image generation providers."""

    name: str = "base"

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        n: int = 1,
        **kwargs,
    ) -> list[ImageResult]:
        """Generate images from a text prompt."""
        pass


class VisionProvider(ABC):
    """Abstract base for vision/image understanding providers."""

    name: str = "base"

    @abstractmethod
    async def analyze(
        self,
        image: bytes | str | Path,
        prompt: str = "Describe this image in detail.",
        **kwargs,
    ) -> VisionResult:
        """Analyze an image and return description."""
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# OPENAI DALL-E
# ═══════════════════════════════════════════════════════════════════════════════

class DALLEProvider(ImageProvider):
    """OpenAI DALL-E image generation."""

    name = "dalle"

    MODELS = ["dall-e-3", "dall-e-2"]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = "https://api.openai.com/v1"

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        n: int = 1,
        model: str = "dall-e-3",
        **kwargs,
    ) -> list[ImageResult]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/images/generations",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "prompt": prompt,
                    "size": size,
                    "quality": quality,
                    "n": n,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                ImageResult(
                    url=img.get("url"),
                    revised_prompt=img.get("revised_prompt"),
                    raw=img,
                )
                for img in data["data"]
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# STABILITY AI
# ═══════════════════════════════════════════════════════════════════════════════

class StabilityProvider(ImageProvider):
    """Stability AI image generation (Stable Diffusion)."""

    name = "stability"

    MODELS = ["stable-diffusion-xl-1024-v1-0", "stable-diffusion-v1-6"]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("STABILITY_API_KEY")
        self.base_url = "https://api.stability.ai/v1"

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        n: int = 1,
        model: str = "stable-diffusion-xl-1024-v1-0",
        **kwargs,
    ) -> list[ImageResult]:
        width, height = map(int, size.split("x"))

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/generation/{model}/text-to-image",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={
                    "text_prompts": [{"text": prompt, "weight": 1}],
                    "height": height,
                    "width": width,
                    "samples": n,
                    "steps": 30 if quality == "standard" else 50,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            import base64
            return [
                ImageResult(
                    data=base64.b64decode(artifact["base64"]),
                    raw=artifact,
                )
                for artifact in data["artifacts"]
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# MIDJOURNEY (via unofficial API)
# ═══════════════════════════════════════════════════════════════════════════════

class MidjourneyProvider(ImageProvider):
    """Midjourney image generation (requires proxy service)."""

    name = "midjourney"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.environ.get("MIDJOURNEY_API_KEY")
        self.base_url = base_url or os.environ.get("MIDJOURNEY_API_URL", "https://api.mymidjourney.ai/v1")

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        n: int = 1,
        **kwargs,
    ) -> list[ImageResult]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/imagine",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"prompt": prompt},
                timeout=300.0,
            )
            response.raise_for_status()
            data = response.json()

            # Wait for completion
            task_id = data.get("taskId")
            if task_id:
                import asyncio
                while True:
                    status_response = await client.get(
                        f"{self.base_url}/task/{task_id}",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        timeout=30.0,
                    )
                    status_response.raise_for_status()
                    status_data = status_response.json()

                    if status_data.get("status") == "completed":
                        return [
                            ImageResult(url=img_url, raw=status_data)
                            for img_url in status_data.get("imageUrls", [])
                        ]
                    elif status_data.get("status") == "failed":
                        raise RuntimeError(f"Midjourney generation failed: {status_data}")

                    await asyncio.sleep(5)

            return [ImageResult(raw=data)]


# ═══════════════════════════════════════════════════════════════════════════════
# REPLICATE
# ═══════════════════════════════════════════════════════════════════════════════

class ReplicateProvider(ImageProvider):
    """Replicate model hosting for image generation."""

    name = "replicate"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("REPLICATE_API_TOKEN")
        self.base_url = "https://api.replicate.com/v1"

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        n: int = 1,
        model: str = "stability-ai/sdxl:39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b",
        **kwargs,
    ) -> list[ImageResult]:
        width, height = map(int, size.split("x"))

        async with httpx.AsyncClient() as client:
            # Create prediction
            response = await client.post(
                f"{self.base_url}/predictions",
                headers={"Authorization": f"Token {self.api_key}"},
                json={
                    "version": model.split(":")[-1] if ":" in model else model,
                    "input": {
                        "prompt": prompt,
                        "width": width,
                        "height": height,
                        "num_outputs": n,
                    },
                },
                timeout=30.0,
            )
            response.raise_for_status()
            prediction = response.json()

            # Poll for completion
            import asyncio
            while prediction["status"] not in ["succeeded", "failed", "canceled"]:
                await asyncio.sleep(2)
                status_response = await client.get(
                    prediction["urls"]["get"],
                    headers={"Authorization": f"Token {self.api_key}"},
                    timeout=30.0,
                )
                status_response.raise_for_status()
                prediction = status_response.json()

            if prediction["status"] != "succeeded":
                raise RuntimeError(f"Replicate generation failed: {prediction}")

            output = prediction.get("output", [])
            if isinstance(output, str):
                output = [output]

            return [ImageResult(url=url, raw=prediction) for url in output]


# ═══════════════════════════════════════════════════════════════════════════════
# VISION PROVIDERS
# ═══════════════════════════════════════════════════════════════════════════════

class OpenAIVisionProvider(VisionProvider):
    """OpenAI GPT-4 Vision."""

    name = "openai-vision"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = "https://api.openai.com/v1"

    async def analyze(
        self,
        image: bytes | str | Path,
        prompt: str = "Describe this image in detail.",
        model: str = "gpt-4o",
        **kwargs,
    ) -> VisionResult:
        import base64

        if isinstance(image, Path):
            image = image.read_bytes()

        if isinstance(image, bytes):
            image_content = {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64.b64encode(image).decode()}"
                },
            }
        else:
            image_content = {"type": "image_url", "image_url": {"url": image}}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                image_content,
                            ],
                        }
                    ],
                    "max_tokens": 1024,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            return VisionResult(
                content=data["choices"][0]["message"]["content"],
                raw=data,
            )


class AnthropicVisionProvider(VisionProvider):
    """Anthropic Claude Vision."""

    name = "claude-vision"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.base_url = "https://api.anthropic.com/v1"

    async def analyze(
        self,
        image: bytes | str | Path,
        prompt: str = "Describe this image in detail.",
        model: str = "claude-sonnet-4-20250514",
        **kwargs,
    ) -> VisionResult:
        import base64

        if isinstance(image, Path):
            image = image.read_bytes()

        if isinstance(image, bytes):
            image_content = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(image).decode(),
                },
            }
        else:
            image_content = {
                "type": "image",
                "source": {"type": "url", "url": image},
            }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 1024,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                image_content,
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            return VisionResult(
                content=data["content"][0]["text"],
                raw=data,
            )


class GeminiVisionProvider(VisionProvider):
    """Google Gemini Vision."""

    name = "gemini-vision"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    async def analyze(
        self,
        image: bytes | str | Path,
        prompt: str = "Describe this image in detail.",
        model: str = "gemini-1.5-pro",
        **kwargs,
    ) -> VisionResult:
        import base64

        if isinstance(image, Path):
            image = image.read_bytes()

        if isinstance(image, bytes):
            image_part = {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(image).decode(),
                }
            }
        else:
            image_part = {"file_data": {"file_uri": image, "mime_type": "image/jpeg"}}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/models/{model}:generateContent",
                params={"key": self.api_key},
                json={
                    "contents": [
                        {
                            "parts": [
                                image_part,
                                {"text": prompt},
                            ]
                        }
                    ]
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            return VisionResult(
                content=data["candidates"][0]["content"]["parts"][0]["text"],
                raw=data,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

IMAGE_PROVIDERS: dict[str, type[ImageProvider]] = {
    "dalle": DALLEProvider,
    "openai": DALLEProvider,
    "stability": StabilityProvider,
    "midjourney": MidjourneyProvider,
    "replicate": ReplicateProvider,
}

VISION_PROVIDERS: dict[str, type[VisionProvider]] = {
    "openai": OpenAIVisionProvider,
    "anthropic": AnthropicVisionProvider,
    "claude": AnthropicVisionProvider,
    "gemini": GeminiVisionProvider,
    "google": GeminiVisionProvider,
}


def get_image_provider(name: str | None = None, **kwargs) -> ImageProvider:
    """Get an image generation provider by name."""
    name = name or os.environ.get("HYPERCLAW_IMAGE_PROVIDER", "dalle")
    name = name.lower()

    if name not in IMAGE_PROVIDERS:
        available = ", ".join(sorted(IMAGE_PROVIDERS.keys()))
        raise ValueError(f"Unknown image provider: {name}. Available: {available}")

    return IMAGE_PROVIDERS[name](**kwargs)


def get_vision_provider(name: str | None = None, **kwargs) -> VisionProvider:
    """Get a vision/image understanding provider by name."""
    name = name or os.environ.get("HYPERCLAW_VISION_PROVIDER", "anthropic")
    name = name.lower()

    if name not in VISION_PROVIDERS:
        available = ", ".join(sorted(VISION_PROVIDERS.keys()))
        raise ValueError(f"Unknown vision provider: {name}. Available: {available}")

    return VISION_PROVIDERS[name](**kwargs)
