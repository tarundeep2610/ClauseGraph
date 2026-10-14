"""AI-assisted document-structure indexing — Gemini/RokkaAI/generic
OpenAI-compatible providers, with an automatic per-chunk fallback to
reference_index.py's deterministic detector (see ai_reference_index.py for
the stitching layer that actually reaches into reference_index.py/database.py).

This package is deliberately provider/schema/parsing/config plumbing only —
it never writes ocr_reference_targets or end_of_page_scope itself, and the
provider API key is never stored anywhere (no DB, no encryption at rest) —
see credentials.py, which reads it live from config.AI_INDEX_API_KEY.
"""

from ai_indexing.errors import (
    AIIndexError,
    AIConfigError,
    AIAuthError,
    AIIndexingDisabledError,
    AIProviderError,
    AIUnreachableError,
    AITimeoutError,
    AIRateLimitError,
    AIRemoteError,
    AICancelledError,
    AIInvalidOutputError,
    classify,
    is_fallbackable,
)
from ai_indexing.providers import (
    ProviderConfig,
    CompletionResult,
    BaseProvider,
    GeminiProvider,
    OpenAICompatibleProvider,
    RokkaAIProvider,
    get_provider,
    list_providers,
    aclose_all,
)
from ai_indexing.schema import (
    AIHeading,
    AIMentionChunk,
    AIMentionReview,
    AIDocrefVerdict,
    AIDocrefReview,
    AIPriorRejection,
    AIStructureChunk,
    NormalizationReport,
    VALID_MENTION_ACTIONS,
    VALID_REJECTION_REASONS,
    normalize_chunk,
)
from ai_indexing.parsing import parse_mention_response, parse_structure_response, parse_docref_review_response
from ai_indexing.prompt import (
    CarryContext,
    ChunkPlan,
    PageInput,
    build_mention_review_prompt,
    build_docref_review_prompt,
    build_structure_prompt,
    plan_chunks,
)
from ai_indexing.client import ChunkOutcome, ChunkStructureResult, StructureIndexer
from ai_indexing.credentials import ResolvedAIConfig, resolve_active_config, describe_active_config
from ai_indexing.recording import (
    RecordSession,
    ReplaySession,
    ReplayFidelityError,
    get_active_record_session,
    get_active_replay_session,
    record_scope,
    replay_scope,
)

__all__ = [
    "AIIndexError", "AIConfigError", "AIAuthError",
    "AIIndexingDisabledError", "AIProviderError", "AIUnreachableError", "AITimeoutError",
    "AIRateLimitError", "AIRemoteError", "AICancelledError", "AIInvalidOutputError",
    "classify", "is_fallbackable",
    "ProviderConfig", "CompletionResult", "BaseProvider", "GeminiProvider",
    "OpenAICompatibleProvider", "RokkaAIProvider", "get_provider", "list_providers", "aclose_all",
    "AIHeading", "AIStructureChunk", "NormalizationReport", "normalize_chunk",
    "parse_structure_response",
    "PageInput", "ChunkPlan", "CarryContext", "plan_chunks", "build_structure_prompt",
    "ChunkOutcome", "ChunkStructureResult", "StructureIndexer",
    "ResolvedAIConfig", "resolve_active_config", "describe_active_config",
    "RecordSession", "ReplaySession", "ReplayFidelityError", "record_scope", "replay_scope",
]
