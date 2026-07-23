import contextvars
from datetime import datetime, timezone
import logging
from database import db
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Context variable to hold the current user ID globally for the request
current_user_id = contextvars.ContextVar('current_user_id', default=None)

# Configuration
DAILY_TOKEN_QUOTA = 250_000
TOKENS_PER_MESSAGE = 5_000  # For UI display

async def check_quota(user_id: str):
    """Check if the user has enough quota for another request."""
    if not user_id:
        return
        
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    collection = db["usage_logs"]
    
    # Aggregate usage for today
    pipeline = [
        {"$match": {"user_id": user_id, "date": today}},
        {"$group": {"_id": None, "total_tokens": {"$sum": "$tokens"}}}
    ]
    
    cursor = collection.aggregate(pipeline)
    usage = await cursor.to_list(length=1)
    total_used = usage[0]["total_tokens"] if usage else 0
    
    if total_used >= DAILY_TOKEN_QUOTA:
        raise HTTPException(
            status_code=429, 
            detail="Daily message quota exceeded. Please try again tomorrow."
        )

async def log_usage(user_id: str, tokens: int, model: str, query_type: str = "general"):
    """Log token usage to the database."""
    if not user_id or not tokens:
        return
        
    try:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        collection = db["usage_logs"]
        await collection.insert_one({
            "user_id": user_id,
            "date": today,
            "tokens": tokens,
            "model": model,
            "query_type": query_type,
            "timestamp": datetime.now(timezone.utc)
        })
    except Exception as e:
        logger.error(f"Failed to log token usage: {e}")

RPD_WARN_THRESHOLD = {"OpenAI": 50}

async def check_provider_rpd(provider: str):
    """Warn when a provider is approaching its daily request cap.  """
    if provider not in RPD_WARN_THRESHOLD:
        return
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    collection = db["usage_logs"]
    count = await collection.count_documents({"model": provider, "date":today})
    limit = RPD_WARN_THRESHOLD[provider]
    if count >= limit * 0.8 : 
        logger.warning(f" {provider} at {count}/{limit} RPD ({round(count/limit*100)}%) - approaching daily cap. ")


async def get_user_usage(user_id: str) -> dict:
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    collection = db["usage_logs"]
    
    pipeline = [
        {"$match": {"user_id": user_id, "date": today}},
        {"$group": {"_id": None, "total_tokens": {"$sum": "$tokens"}}}
    ]
    cursor = collection.aggregate(pipeline)
    usage = await cursor.to_list(length=1)
    total_used = usage[0]["total_tokens"] if usage else 0
    
    messages_left = max(0.0, (DAILY_TOKEN_QUOTA - total_used) / TOKENS_PER_MESSAGE)
    
    # Compute reset time (time until midnight UTC)
    now = datetime.now(timezone.utc)
    # Next day midnight
    tomorrow = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + __import__('datetime').timedelta(days=1)
    diff = tomorrow - now
    hours = diff.seconds // 3600
    minutes = (diff.seconds % 3600) // 60
    
    return {
        "quota": DAILY_TOKEN_QUOTA,
        "used": total_used,
        "messages_left": round(messages_left, 1),
        "reset_in": f"{hours}h {minutes}m"
    }
