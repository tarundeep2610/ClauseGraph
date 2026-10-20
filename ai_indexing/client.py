"""StructureIndexer — the façade the stitching layer (ai_reference_index.py)
actually calls. Never raises the "soft" per-chunk error kind itself; returns
a ChunkOutcome result instead. Only raises AIConfigError (and subclasses,
including the circuit breaker) — which means "stop the whole AI pass for
this job."
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import config
from ai_indexing.credentials import ResolvedAIConfig
from ai_indexing.errors import (
    AICancelledError,
    AIConfigError,
    AIIndexingDisabledError,
    AIIndexError,
    AIInvalidOutputError,
    AIRateLimitError,
    classify,
    is_fallbackable,
)
from ai_indexing.parsing import parse_structure_response
from ai_indexing.prompt import CarryContext, ChunkPlan, build_structure_prompt
from ai_indexing.providers import get_provider
from ai_indexing.recording import get_active_record_session, get_active_replay_session


class ChunkOutcome(str, Enum):
    OK = "ok"        # headings is authoritative for these pages
    EMPTY = "empty"  # valid response, model genuinely found no headings — NOT a failure
    FAILED = "failed"  # AI unusable for this chunk -> caller should fall back to rules
    CANCELLED = "cancelled"  # paused/cancelled mid-call — caller should stop cleanly, not fall back


@dataclass
class ChunkStructureResult:
    outcome: ChunkOutcome
    page_nums: List[int]
    headings: List[Any] = field(default_factory=list)  # List[ai_indexing.schema.AIHeading]
    confirmed_prior_ids: List[str] = field(default_factory=list)
    rejected_priors: List[Any] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    duration_ms: int = 0
    attempts: int = 0
    error_kind: Optional[str] = None
    error_message: str = ""
    normalization: Optional[Dict[str, Any]] = None
    raw_excerpt: str = ""
    provider_job_id: Optional[str] = None


class StructureIndexer:
    """Stateful per-job façade — one instance per job/reindex run, so the
    circuit breaker counts failures within THIS document, not globally
    across every job using this provider."""

    def __init__(self, resolved: ResolvedAIConfig,
                 failure_limit: int = None):
        self.resolved = resolved
        self.failure_limit = failure_limit or config.AI_INDEX_CONSECUTIVE_FAILURE_LIMIT
        self._consecutive_failures = 0

    async def extract_structure_for_chunk(
        self,
        chunk: ChunkPlan,
        *,
        carry_context: Optional[CarryContext] = None,
        document_hint: str = "",
        cancel_check=None,
        include_pleading_points: bool = False,
        rule_prior: Optional[List[dict]] = None,
        review_rule_prior: bool = False,
        order_by_line_hint: bool = False,
    ) -> ChunkStructureResult:
        page_nums = chunk.page_nums

        replay_session = get_active_replay_session()
        if replay_session is not None:
            recorded = replay_session.get_chunk(page_nums)
            ai_chunk, norm_report = parse_structure_response(
                recorded["raw_text"], page_nums, order_by_line_hint=order_by_line_hint,
            )
            has_content = bool(ai_chunk.headings or ai_chunk.confirmed_prior_ids or ai_chunk.rejected_priors)
            outcome = ChunkOutcome.OK if has_content else ChunkOutcome.EMPTY
            return ChunkStructureResult(
                outcome=outcome, page_nums=page_nums, headings=list(ai_chunk.headings),
                confirmed_prior_ids=list(ai_chunk.confirmed_prior_ids),
                rejected_priors=list(ai_chunk.rejected_priors),
                provider=replay_session.fingerprint.get("provider", "replay"),
                model=replay_session.fingerprint.get("model", "replay"),
                duration_ms=recorded.get("duration_ms", 0),
                attempts=recorded.get("attempt", 1),
                normalization=norm_report.to_dict(),
            )

        if self._consecutive_failures >= self.failure_limit:
            raise AIIndexingDisabledError(
                f"{self._consecutive_failures} consecutive AI chunk failures "
                f"(limit {self.failure_limit}) — disabling AI indexing for the rest of this job."
            )

        provider = await get_provider(self.resolved.provider_config)
        system_prompt, user_prompt = build_structure_prompt(
            chunk, carry_context=carry_context, document_hint=document_hint,
            include_pleading_points=include_pleading_points,
            rule_prior=rule_prior,
            review_rule_prior=review_rule_prior,
        )

        attempts = 0
        last_error: Optional[Exception] = None
        for attempt in (1, 2):  # one full re-call on invalid output, per tender_ai's pattern
            attempts = attempt
            try:
                completion = await provider.complete(
                    system_prompt=system_prompt, user_prompt=user_prompt, cancel_check=cancel_check,
                )
            except AICancelledError:
                return ChunkStructureResult(
                    outcome=ChunkOutcome.CANCELLED, page_nums=page_nums,
                    provider=self.resolved.provider_config.provider,
                    model=self.resolved.provider_config.model, attempts=attempts,
                )
            except AIConfigError:
                raise  # terminal — propagate to the caller
            except AIRateLimitError as e:
                last_error = e
                if getattr(e, "is_daily_quota", False):
                    self._consecutive_failures = self.failure_limit
                    break
                # Temporary per-minute burst rate limit (RPM/TPM) — wait for reset and retry once
                wait_s = max(5.0, min(float(e.retry_after or 30.0) + 2.0, 75.0))
                print(f"[AI Indexing] Rate limit on pages {page_nums}. Waiting {wait_s:.1f}s for reset before retry...")
                sleep_deadline = time.monotonic() + wait_s
                cancelled = False
                while time.monotonic() < sleep_deadline:
                    if cancel_check and cancel_check():
                        cancelled = True
                        break
                    await asyncio.sleep(min(1.0, max(0.1, sleep_deadline - time.monotonic())))
                if cancelled:
                    return ChunkStructureResult(
                        outcome=ChunkOutcome.CANCELLED, page_nums=page_nums,
                        provider=self.resolved.provider_config.provider,
                        model=self.resolved.provider_config.model, attempts=attempts,
                    )
                # Retry the completion call after the temporary limit has cleared
                try:
                    completion = await provider.complete(
                        system_prompt=system_prompt, user_prompt=user_prompt, cancel_check=cancel_check,
                    )
                except Exception as retry_err:
                    last_error = retry_err
                    break
            except AIIndexError as e:
                last_error = e
                if not is_fallbackable(e):
                    raise
                break  # a provider-transport failure isn't worth re-calling the model for
            else:
                try:
                    ai_chunk, norm_report = parse_structure_response(
                        completion.text, page_nums, order_by_line_hint=order_by_line_hint,
                    )
                except AIInvalidOutputError as e:
                    last_error = e
                    if attempt == 1:
                        user_prompt = (
                            user_prompt
                            + f"\n\n# CORRECTION\nYour previous response was not valid JSON matching "
                              f"the required schema (error: {e}). Return ONLY the JSON object described "
                              f"above, no other text."
                        )
                        continue
                    break
                else:
                    self._consecutive_failures = 0
                    has_content = bool(ai_chunk.headings or ai_chunk.confirmed_prior_ids or ai_chunk.rejected_priors)
                    outcome = ChunkOutcome.OK if has_content else ChunkOutcome.EMPTY
                    record_session = get_active_record_session()
                    if record_session is not None:
                        record_session.record_chunk(
                            page_nums=page_nums,
                            raw_text=completion.text,
                            duration_ms=completion.duration_ms,
                            attempt=attempts,
                            system_prompt=system_prompt,
                        )
                    return ChunkStructureResult(
                        outcome=outcome, page_nums=page_nums, headings=list(ai_chunk.headings),
                        confirmed_prior_ids=list(ai_chunk.confirmed_prior_ids),
                        rejected_priors=list(ai_chunk.rejected_priors),
                        provider=completion.provider, model=completion.model,
                        duration_ms=completion.duration_ms, attempts=attempts,
                        normalization=norm_report.to_dict(),
                        provider_job_id=completion.provider_job_id,
                    )

        self._consecutive_failures += 1
        record_session = get_active_record_session()
        if record_session is not None:
            record_session.record_failure(
                page_nums=page_nums,
                error=str(last_error) if last_error else "unknown",
            )
        return ChunkStructureResult(
            outcome=ChunkOutcome.FAILED, page_nums=page_nums,
            provider=self.resolved.provider_config.provider,
            model=self.resolved.provider_config.model, attempts=attempts,
            error_kind=classify(last_error) if last_error else "unknown",
            error_message=str(last_error) if last_error else "",
            raw_excerpt=getattr(last_error, "raw_excerpt", "") if last_error else "",
        )

    async def aclose(self) -> None:
        pass  # providers are process-wide cached instances; closed via ai_indexing.aclose_all()
