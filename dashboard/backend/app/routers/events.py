"""SSE endpoint for streaming real-time agent events to the dashboard."""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.event_bus import subscribe, subscriber_count

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("/stream")
async def event_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events endpoint.

    Clients connect with EventSource and receive real-time events
    as they are published by the webhook handler.

    Event format (SSE):
        event: <event_type>
        data: {"run_id": "...", ...}

    Reconnection hint is sent as the first message.
    """

    async def generate():
        # Send initial comment so the client knows the connection is alive
        yield ": connected\n\n"

        try:
            async for event in subscribe():
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                event_type = event.get("type", "message")
                data = json.dumps(event.get("data", event), default=str)

                yield f"event: {event_type}\ndata: {data}\n\n"
        except Exception:
            logger.exception("Error in SSE stream")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.get("/subscribers")
async def get_subscriber_count():
    """Return the number of active SSE subscribers (for monitoring)."""
    return {"subscribers": subscriber_count()}
