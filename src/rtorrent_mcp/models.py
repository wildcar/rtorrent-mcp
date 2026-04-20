"""Pydantic models for tool inputs/outputs.

All envelopes follow the ``{result, error}`` pattern: exceptions are caught
in ``tools.py`` and surfaced as structured errors, so MCP callers can
dispatch on ``error.code`` without string-matching.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MediaKind = Literal["movie", "series"]
DownloadState = Literal["active", "stopped", "paused", "complete"]


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "invalid_argument",
        "not_found",
        "rtorrent_unreachable",
        "rtorrent_error",
        "internal_error",
    ]
    message: str


class Download(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hash: str = Field(..., description="Info-hash, uppercase hex (40 chars for BTIH).")
    name: str = Field(..., description="Release name as rtorrent sees it.")
    size_bytes: int = Field(0, ge=0)
    completed_bytes: int = Field(0, ge=0)
    down_rate: int = Field(0, ge=0, description="Download rate in bytes/sec.")
    up_rate: int = Field(0, ge=0, description="Upload rate in bytes/sec.")
    ratio: float = Field(0.0, description="Share ratio as float (0.0 = nothing uploaded).")
    directory: str = Field("", description="Absolute path rtorrent stores the payload in.")
    state: DownloadState = Field("stopped")


class AddTorrentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    download: Download | None = None
    error: ToolError | None = None


class ListDownloadsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    downloads: list[Download] = Field(default_factory=list)
    error: ToolError | None = None


class DownloadStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    download: Download | None = None
    error: ToolError | None = None


class AckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = False
    error: ToolError | None = None
