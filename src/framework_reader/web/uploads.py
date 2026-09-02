"""上传体积边界。路由只决定如何处理文件，不能各自忘记限制大小。"""
from collections.abc import AsyncIterator
from typing import BinaryIO

from fastapi import UploadFile

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024


class UploadTooLarge(Exception):
    pass


def _limit_label(max_bytes: int) -> str:
    if max_bytes >= 1024 * 1024 and max_bytes % (1024 * 1024) == 0:
        return f"{max_bytes // (1024 * 1024)} MB"
    return f"{max_bytes // 1024} KB"


async def _chunks(
    upload: UploadFile, max_bytes: int | None = None,
) -> AsyncIterator[bytes]:
    max_bytes = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    total = 0
    while chunk := await upload.read(_CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLarge(f"That file is over {_limit_label(max_bytes)}.")
        yield chunk


async def read_limited(
    upload: UploadFile, max_bytes: int | None = None,
) -> bytes:
    parts = [chunk async for chunk in _chunks(upload, max_bytes)]
    return b"".join(parts)


async def save_limited(upload: UploadFile, destination: BinaryIO) -> None:
    async for chunk in _chunks(upload):
        destination.write(chunk)
