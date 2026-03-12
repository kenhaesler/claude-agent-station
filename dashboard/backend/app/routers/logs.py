"""Log streaming (WebSocket) and search endpoints."""

import asyncio
import json
import os
import re
from typing import Optional, List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.config import settings
from app.services.log_streamer import tail_log_file

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.websocket("/stream")
async def stream_logs(
    websocket: WebSocket,
    file: Optional[str] = None,
    from_beginning: bool = False,
):
    """WebSocket endpoint for live log streaming."""
    await websocket.accept()

    # Validate file path if provided (must be within log_dir)
    filepath = None
    if file:
        filepath = os.path.realpath(file)
        log_dir_real = os.path.realpath(settings.log_dir)
        if not filepath.startswith(log_dir_real):
            await websocket.send_json({"error": "Path outside log directory"})
            await websocket.close()
            return

    try:
        async for line in tail_log_file(filepath=filepath, from_beginning=from_beginning):
            await websocket.send_text(line)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass


def _search_files(
    log_dir: str, filenames: List[str], q: str, limit: int, run_id: Optional[str]
) -> List[dict]:
    """Sync helper for file searching — called via asyncio.to_thread."""
    results: List[dict] = []
    q_lower = q.lower()
    for fname in filenames:
        if not fname.endswith(".stream.jsonl"):
            continue
        if run_id and run_id not in fname:
            continue

        filepath = os.path.join(log_dir, fname)
        try:
            with open(filepath, "r") as f:
                for line_num, line in enumerate(f, 1):
                    if q_lower in line.lower():
                        results.append({
                            "file": fname,
                            "line": line_num,
                            "content": line.strip()[:500],
                        })
                        if len(results) >= limit:
                            return results
        except OSError:
            continue
    return results


@router.get("/search")
async def search_logs(
    q: str = Query(..., min_length=1),
    run_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
):
    """Search across log files for a string."""
    log_dir = settings.log_dir

    try:
        filenames = sorted(os.listdir(log_dir), reverse=True)
    except OSError:
        return {"results": [], "total": 0}

    results = await asyncio.to_thread(_search_files, log_dir, filenames, q, limit, run_id)
    return {"results": results, "total": len(results)}


def _read_run_log_files(
    log_dir: str, filenames: List[str], offset: int, limit: int
) -> List[dict]:
    """Sync helper for reading run log files — called via asyncio.to_thread."""
    lines: List[dict] = []
    for fname in sorted(filenames):
        filepath = os.path.join(log_dir, fname)
        try:
            with open(filepath, "r") as f:
                for i, line in enumerate(f):
                    if i < offset:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                        lines.append(parsed)
                    except json.JSONDecodeError:
                        lines.append({"type": "raw", "content": line[:500]})
                    if len(lines) >= limit:
                        return lines
        except OSError:
            continue
    return lines


@router.get("/{run_id}")
async def get_run_logs(
    run_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Get log lines for a specific run."""
    log_dir = settings.log_dir

    try:
        filenames = [
            f for f in os.listdir(log_dir)
            if run_id in f and f.endswith(".stream.jsonl")
        ]
    except OSError:
        return {"run_id": run_id, "lines": [], "total": 0}

    lines = await asyncio.to_thread(_read_run_log_files, log_dir, filenames, offset, limit)
    return {"run_id": run_id, "lines": lines, "total": len(lines)}
