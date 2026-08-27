"""
HyperClaw Provider Abstraction Layer.
Unified interfaces for AI/LLM, voice, image, search, and other providers.
"""

from .llm import (
    LLMProvider,
    AnthropicProvider,
    OpenAIProvider,
    GeminiProvider,
    MistralProvider,
    GroqProvider,
    XAIProvider,
    BedrockProvider,
    OpenRouterProvider,
    TogetherProvider,
    PerplexityProvider,
    DeepSeekProvider,
    QwenProvider,
    CerebrasProvider,
    HuggingFaceProvider,
    OllamaProvider,
    VLLMProvider,
    LiteLLMProvider,
    get_llm_provider,
)

from .voice import (
    VoiceProvider,
    STTProvider,
    TTSProvider,
    WhisperProvider,
    DeepgramProvider,
    AssemblyAIProvider,
    ElevenLabsProvider,
    GoogleTTSProvider,
    OpenAITTSProvider,
)

from .image import (
    ImageProvider,
    DALLEProvider,
    StabilityProvider,
    MidjourneyProvider,
    ReplicateProvider,
)

from .search import (
    SearchProvider,
    GoogleSearchProvider,
    BingSearchProvider,
    DuckDuckGoProvider,
    SerpAPIProvider,
    PerplexitySearchProvider,
    ArxivProvider,
    WikipediaProvider,
)

from .embeddings import (
    EmbeddingProvider,
    OpenAIEmbedder,
    VoyageEmbedder,
    GeminiEmbedder,
    MistralEmbedder,
    CohereEmbedder,
)

__all__ = [
    # LLM
    "LLMProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "GeminiProvider",
    "MistralProvider",
    "GroqProvider",
    "XAIProvider",
    "BedrockProvider",
    "OpenRouterProvider",
    "TogetherProvider",
    "PerplexityProvider",
    "DeepSeekProvider",
    "QwenProvider",
    "CerebrasProvider",
    "HuggingFaceProvider",
    "OllamaProvider",
    "VLLMProvider",
    "LiteLLMProvider",
    "get_llm_provider",
    # Voice
    "VoiceProvider",
    "STTProvider",
    "TTSProvider",
    "WhisperProvider",
    "DeepgramProvider",
    "AssemblyAIProvider",
    "ElevenLabsProvider",
    "GoogleTTSProvider",
    "OpenAITTSProvider",
    # Image
    "ImageProvider",
    "DALLEProvider",
    "StabilityProvider",
    "MidjourneyProvider",
    "ReplicateProvider",
    # Search
    "SearchProvider",
    "GoogleSearchProvider",
    "BingSearchProvider",
    "DuckDuckGoProvider",
    "SerpAPIProvider",
    "PerplexitySearchProvider",
    "ArxivProvider",
    "WikipediaProvider",
    # Embeddings
    "EmbeddingProvider",
    "OpenAIEmbedder",
    "VoyageEmbedder",
    "GeminiEmbedder",
    "MistralEmbedder",
    "CohereEmbedder",
]
