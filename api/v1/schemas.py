"""All Pydantic request and response models for API v1.

Keeping all schemas in one place to:
- Track breaking changes (add/remove fields)
- Generate OpenAPI docs automatically
- Share types between routers
"""

from __future__ import annotations

import math
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")


class PagedResponse(BaseModel, Generic[T]):
    """Standard paginated response returned by all list endpoints. A Generic pagination envelope"""

    total: int = Field(description="Total number of items across all pages")
    page: int = Field(description="Current 1-indexed page number")
    size: int = Field(description="Items per page requested")
    pages: int = Field(description="Total number of pages")
    items: List[T] = Field(description="Items on this page")

    @classmethod
    def build(cls, total: int, page: int, size: int, items: list) -> "PagedResponse":
        return cls(
            total=total,
            page=page,
            size=size,
            pages=math.ceil(total / size) if size > 0 else 0,
            items=items,
        )


class UploadedFileSchema(BaseModel):
    """Returned for each file after a successful upload"""

    file_id: str = Field(description="UUID identifying the upload slot on disk")
    name: str = Field(description="Original filename (basename only)")
    size_bytes: int = Field(description="File size in bytes")
    content_hash: str = Field(description="SHA-256 of first 64 KB use for dedup check")


class CheckHashesRequest(BaseModel):
    """Send computed hashes to find out which files were already extracted"""

    hashes: List[str] = Field(
        description="List of SHA-256 hex strings (one per file)",
        min_length=1,
    )


class CheckHashesResponse(BaseModel):
    already_processed: dict[str, Any] = Field(
        description="Hash -> extraction record for files already in the DB"
    )
    unprocessed: List[str] = Field(
        description="Hashes that have NOT been extracted yet"
    )


class StartJobRequest(BaseModel):
    file_ids: List[str] = Field(
        description="'file_id' values returned by the upload endpoint or registration"
    )
    selected_files: Optional[dict[str, List[str]]] = Field(
        default=None,
        description="Optional mapping of ref_id -> list of relative paths to process. If provided for a ref_id, only these files will be processed.",
    )
    output_dir: str = Field(default="extracted_files")
    force: bool = Field(
        default=False,
        description="Reprocess files that are already in the database",
    )
    settings: Optional[dict] = Field(default=None)
    timeout_seconds: Optional[int] = Field(
        default=None,
        description="Optional global timeout for the entire job in seconds.",
    )

    @field_validator("file_ids")
    @classmethod
    def must_not_be_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("file_ids must not be empty")
        return v


class JobStatusResponse(BaseModel):
    """Compact job status."""

    status: str
    done: int
    total: int
    failed: int
    progress_pct: int
    elapsed: float
    eta_seconds: Optional[int]
    current_file: str
    log: List[str] = Field(default_factory=list)


class FileEntryResponse(BaseModel):
    name: str
    path: str
    size_bytes: int
    status: str
    progress_pct: int
    method: str
    elapsed: float
    char_count: int
    message: str
    current_page: int
    total_pages: int


class ResultRecord(BaseModel):
    id: int
    run_id: Optional[str] = None
    run_started_at: Optional[str] = None
    source_path: str
    filename: str
    rel_path: str
    txt_path: str
    method: str
    char_count: int
    page_count: int
    content_hash: Optional[str]
    processed_at: str
    confidence: Optional[float] = None
    flags: Optional[str] = None


class ResultDetail(ResultRecord):
    content: str = Field(description="Full text content read from disk")


class RunRecord(BaseModel):
    run_id: str
    started_at: str
    completed_at: Optional[str] = None
    status: str
    total_files: int = 0
    done_files: int = 0
    failed_files: int = 0
    direct_files: int = 0
    ocr_files: int = 0
    elapsed_seconds: Optional[float] = (
        None  # computed server-side (completed_at - started_at)
    )
    input_dir: Optional[str] = None
    log_path: Optional[str] = None  # absolute path to per-run activity log .txt


class ExtractedFileRecord(BaseModel):
    name: str
    rel_path: str
    size_bytes: int
    modified: float
    confidence: Optional[float] = None
    flags: Optional[str] = None


class SearchResultItem(BaseModel):
    result_id: int  # links to GET /results/{id}
    file: str
    rel_path: str
    page_no: int  # which page matched (1-indexed)
    snippet: str  # HTML - contains <mark> tags around the match


class SearchResponse(BaseModel):
    query: str
    total: int
    page: int
    size: int
    pages: int
    results: List[SearchResultItem]


class FileReferenceSchema(BaseModel):
    """Request body for POST /api/v1/upload/reference.

    Provides either a folder path (all PDFs inside are included recursively)
    or a single PDF file path already accessible on the server's filesystem.
    """

    path: str = Field(
        description=(
            "Absolute or relative path on the server filesystem. "
            "Folder: all PDFs inside are included recursively. "
            "Single .pdf file: that file only."
        )
    )


class FileReferenceItem(BaseModel):
    name: str
    size_bytes: int
    content_hash: str
    rel_path: str


class FileReferenceResponse(BaseModel):
    """Returned after a successful local file reference registration."""

    ref_id: str = Field(description="ID to pass in StartJobRequest.file_ids")
    resolved_path: str = Field(description="Absolute path as resolved on the server")
    pdf_count: int = Field(description="Number of PDF files found under the path")
    already_processed_count: int = Field(
        default=0,
        description="Number of PDFs that have already been extracted (based on hash check)",
    )
    is_folder: bool = Field(description="True when path is a directory")
    files: List[FileReferenceItem] = Field(
        default_factory=list,
        description="Detailed list of individual PDF files found under the path",
    )
