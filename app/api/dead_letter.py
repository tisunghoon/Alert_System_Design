from fastapi import APIRouter, Query

from app.core.redis import DEAD_LETTER_STREAM, get_redis

router = APIRouter(tags=["dead-letter"])


@router.get("/dead-letter")
async def list_dead_letters(limit: int = Query(100, ge=1, le=1000)):
    messages = await get_redis().xrevrange(DEAD_LETTER_STREAM, count=limit)
    return {
        "items": [
            {
                "notification_id": fields.get("notification_id"),
                "failed_at": fields.get("failed_at"),
                "failure_reason": fields.get("failure_reason"),
                "retry_count": int(fields.get("retry_count") or 0),
            }
            for _, fields in messages
        ]
    }
