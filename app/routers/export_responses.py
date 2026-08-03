from __future__ import annotations

from collections.abc import Iterable

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.services.resource_limits import ResourceLimitExceeded, enforce_byte_limit
from app.settings import get_settings


def attachment_response(
    content: str | bytes,
    *,
    media_type: str,
    filename: str,
    max_bytes: int | None = None,
) -> StreamingResponse:
    settings = get_settings()
    limit = max_bytes or settings.max_export_size_mb * 1024 * 1024
    try:
        enforce_byte_limit(content, limit, resource=filename)
    except ResourceLimitExceeded as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "code": exc.code,
                "message": exc.message,
                "hint": "Narrow filters or lower the page/export size before retrying.",
            },
        ) from exc

    response = StreamingResponse(
        _single_chunk(content),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
    response.body = content if isinstance(content, bytes) else content.encode("utf-8")
    return response


def _single_chunk(content: str | bytes) -> Iterable[str | bytes]:
    yield content
