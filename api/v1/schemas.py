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
    run_id: Optional[str] = Field(default=None, description="Current Job run ID")

    @classmethod
    def build(
        cls, total: int, page: int, size: int, items: list, run_id: Optional[str] = None
    ) -> "PagedResponse":
        return cls(
            total=total,
            page=page,
            size=size,
            pages=math.ceil(total / size) if size > 0 else 0,
            items=items,
            run_id=run_id,
        )


class UploadedFileSchema(BaseModel):
    """Returned for each file after a successful upload"""

    file_id: str = Field(description="UUID identifying the upload slot on disk")
    name: str = Field(description="Original filename (basename only)")
    size_bytes: int = Field(description="File size in bytes")
    content_hash: str = Field(description="SHA-256 of first 64 KB use for dedup check")
    is_already_processed: bool = Field(
        default=False,
        description="True if a file with this hash is already in the extraction database",
    )


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
        default_factory=list,
        description="'file_id' values returned by POST /upload or POST /upload/reference",
    )
    batch_id: Optional[str] = Field(
        default=None,
        description="'batch_id' value returned by POST /batches (server side path scan)",
    )
    batch_ids: List[str] = Field(
        default_factory=list,
        description="Currently disabled. Reserved for future multi batch support",
    )
    selected_files: Optional[dict[str, List[str]]] = Field(
        default=None,
        description="Optional mapping of ref_id -> list of relative paths to process. If provided for a ref_id, only these files will be processed.",
    )
    output_dir: str = Field(
        default="",
        description="Empty by default so output is stored in global output_dir only. If specified, output is stored in a subfolder of the global output_dir",
    )
    force: bool = Field(
        default=False,
        description="Reprocess files that are already in the database",
    )
    settings: Optional[dict] = Field(default=None)
    timeout_seconds: Optional[int] = Field(
        default=None,
        description="Optional global timeout for the entire job in seconds.",
    )

    @field_validator("file_ids", "batch_ids", mode="before")
    @classmethod
    def normalise_ids(cls, v: object) -> list:
        """Normalise both ID fields to return a list if None or str."""
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return v

    def any_ids(self) -> bool:
        """True if at least one file_id or batch_id was supplied."""
        return bool(self.file_ids or self.batch_ids or self.batch_id)


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
    log: List[dict] = Field(default_factory=list)
    run_id: Optional[str] = Field(default=None)


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
    is_processed: bool = Field(default=False)


class ResultRecord(BaseModel):
    id: int
    run_id: Optional[str] = None
    run_started_at: Optional[str] = None
    run_number: Optional[int] = None
    filename: str
    rel_path: str
    method: str
    char_count: int
    content_hash: Optional[str]
    processed_at: str
    confidence: Optional[float] = None
    flags: Optional[str] = None


class ResultDetail(ResultRecord):
    content: str = Field(description="Full text content read from disk")
    page_count: int
    source_path: str
    txt_path: str


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
    run_number: Optional[int] = None


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


class RunIdItem(BaseModel):
    run_id: str
    run_number: int


class DirectoryNode(BaseModel):
    run_id: str
    path: str
    count: int


class RunTreeResponse(BaseModel):
    directories: List[DirectoryNode]
    directories_total: int
    top_level_files: List[ResultRecord]
    top_level_files_total: int
    page: int
    size: int
    pages: int

    @classmethod
    def build(cls, data: dict) -> "RunTreeResponse":
        import math

        size = data["size"]
        dirs_total = data["directories_total"]
        files_total = data["top_level_files_total"]
        max_total = max(dirs_total, files_total)
        pages = max(1, math.ceil(max_total / size) if size > 0 else 1)
        return cls(
            directories=data["directories"],
            directories_total=dirs_total,
            top_level_files=data["top_level_files"],
            top_level_files_total=files_total,
            page=data["page"],
            size=size,
            pages=pages,
        )
