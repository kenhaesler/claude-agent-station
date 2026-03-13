"""Async file tail for WebSocket live log streaming.

Uses asyncio.to_thread with standard open(), polling at configurable interval.
No inotify or aiofiles dependency needed.
"""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator

from app.config import settings

logger = logging.getLogger(__name__)


def _get_latest_stream_file(log_dir: str) -> str | None:
    """Find the most recently modified .stream.jsonl file."""
    try:
        candidates = [
            os.path.join(log_dir, f)
            for f in os.listdir(log_dir)
            if f.endswith(".stream.jsonl")
        ]
    except OSError:
        return None

    if not candidates:
        return None

    return max(candidates, key=os.path.getmtime)


def _read_new_lines(filepath: str, position: int) -> tuple:
    """Read new lines from file starting at position. Returns (lines, new_position)."""
    try:
        size = os.path.getsize(filepath)
        if size <= position:
            return [], position

        with open(filepath) as f:
            f.seek(position)
            lines = f.readlines()
            new_pos = f.tell()
        return [line.rstrip("\n") for line in lines if line.strip()], new_pos
    except OSError:
        return [], position


async def tail_log_file(
    filepath: str | None = None,
    from_beginning: bool = False,
) -> AsyncGenerator[str, None]:
    """Async generator that yields new lines from a log file.

    If filepath is None, auto-detects the latest stream file.
    """
    if filepath is None:
        filepath = _get_latest_stream_file(settings.log_dir)
        if filepath is None:
            yield '{"error": "No stream files found"}'
            return

    if not os.path.exists(filepath):
        yield f'{{"error": "File not found: {filepath}"}}'
        return

    position = 0 if from_beginning else os.path.getsize(filepath)

    while True:
        lines, position = await asyncio.to_thread(
            _read_new_lines, filepath, position
        )
        for line in lines:
            yield line

        # Check if a newer file appeared (run switched)
        latest = await asyncio.to_thread(
            _get_latest_stream_file, settings.log_dir
        )
        if latest and latest != filepath:
            # Switch to new file
            filepath = latest
            position = 0
            continue

        await asyncio.sleep(settings.ws_poll_interval)
