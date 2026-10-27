"""Pydantic schemas for gold evaluation fixtures.

Validates targets, mentions, action items, and OCR ground truth annotations.
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class GoldTarget(BaseModel):
    """Ground truth reference target (heading, section, clause, annexure, or page anchor)."""

    model_config = ConfigDict(extra="ignore")

    page: int
    key: str = Field(..., description="Target key, e.g. 'subdoc:judgment', 'point:3', 'clause:14.3'")
    occurrence_index: int = 0
    label: Optional[str] = None
    scope_key: Optional[str] = Field(
        None, description="Ancestor path e.g. 'subdoc:judgment/subdoc:facts' or empty string"
    )


class GoldMention(BaseModel):
    """Ground truth in-text citation or reference mention."""

    model_config = ConfigDict(extra="ignore")

    page: int
    occurrence_index: int = 0
    key: Optional[str] = None
    raw_text: Optional[str] = None
    resolution: Literal["exact", "approximate", "unresolved", "suppressed", "undefined"]
    suppressed_reason: Optional[str] = None


class GoldIndexFixture(BaseModel):
    """Complete document-structure and cross-reference gold fixture."""

    model_config = ConfigDict(extra="ignore")

    job_id: str
    source_job_id: Optional[str] = None
    total_pages: Optional[int] = None
    targets: List[GoldTarget] = Field(default_factory=list)
    mentions: List[GoldMention] = Field(default_factory=list)


class GoldActionItem(BaseModel):
    """Ground truth actionable item (deadline, obligation, condition, other)."""

    model_config = ConfigDict(extra="ignore")

    page: int
    item_type: Literal["deadline", "obligation", "condition", "other"]
    item_text: str
    evidence_quote: str
    responsible_party: Optional[str] = None
    confidence: Optional[str] = "high"
    occurrence_index: int = 0
    date_text: Optional[str] = None
    date_iso: Optional[str] = None
    anchor_event: Optional[str] = None
    cadence: Optional[str] = None
    end_page: Optional[int] = None


class GoldActionItemsFixture(BaseModel):
    """Container fixture for all action items in a benchmark job."""

    model_config = ConfigDict(extra="ignore")

    job_id: str
    total_pages: Optional[int] = None
    action_items: List[GoldActionItem] = Field(default_factory=list)


class GoldOCRPage(BaseModel):
    """Ground truth transcription and table layout for a single page."""

    model_config = ConfigDict(extra="ignore")

    page: int
    source_job_id: str
    category: Literal["dense_table", "clean_legal", "degraded_scan", "mixed_layout"]
    ground_truth_text: str
    ground_truth_tables: List[List[List[str]]] = Field(default_factory=list)


class GoldOCRFixture(BaseModel):
    """Container fixture for OCR evaluation pages."""

    model_config = ConfigDict(extra="ignore")

    description: str
    pages: List[GoldOCRPage] = Field(default_factory=list)
