"""Operator surface: per-user quota view plus the admin console endpoints
(usage, user management, API health, telemetry events, source toggles)."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth import get_current_user, invalidate_user_cache
from core.database import db
from core.limiter import limiter
from services.usage_tracker import DAILY_TOKEN_QUOTA, TOKENS_PER_MESSAGE, get_user_usage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


def _require_admin(current_user: dict) -> None:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/api/user/usage")
async def get_my_usage(current_user: dict = Depends(get_current_user)):
    return await get_user_usage(current_user["user_id"])


@router.get("/api/admin/usage")
async def admin_usage_endpoint(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)

    collection = db["usage_logs"]
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    total_pipeline = [
        {"$match": {"date": today}},
        {"$group": {"_id": None, "total_tokens": {"$sum": "$tokens"}, "calls": {"$sum": 1}}},
    ]
    by_provider_pipeline = [
        {"$match": {"date": today}},
        {"$group": {
            "_id": "$model",
            "total_tokens": {"$sum": "$tokens"},
            "calls": {"$sum": 1},
        }},
        {"$sort": {"total_tokens": -1}},
    ]
    by_user_pipeline = [
        {"$match": {"date": today}},
        {"$group": {"_id": "$user_id", "total_tokens": {"$sum": "$tokens"}, "calls": {"$sum": 1}}},
        {"$sort": {"total_tokens": -1}},
        {"$limit": 50},
    ]

    total_rows, provider_rows, usage = await asyncio.gather(
        collection.aggregate(total_pipeline).to_list(length=1),
        collection.aggregate(by_provider_pipeline).to_list(length=100),
        collection.aggregate(by_user_pipeline).to_list(length=50),
    )

    today_total = int((total_rows[0]["total_tokens"] if total_rows else 0) or 0)
    today_calls = int((total_rows[0]["calls"] if total_rows else 0) or 0)

    by_provider = [
        {
            "provider": row.get("_id") or "unknown",
            "tokens": int(row.get("total_tokens") or 0),
            "calls": int(row.get("calls") or 0),
        }
        for row in provider_rows
    ]

    oids = []
    for row in usage:
        try:
            oids.append(ObjectId(row["_id"]))
        except Exception:
            pass
    user_docs = await db["users"].find({"_id": {"$in": oids}}).to_list(length=50) if oids else []
    user_map = {str(u["_id"]): u for u in user_docs}

    by_user = []
    for row in usage:
        user_id = row["_id"]
        user_doc = user_map.get(user_id)
        email = user_doc["email"] if user_doc else user_id
        name = user_doc.get("name", "Unknown") if user_doc else "Unknown"
        custom_q = user_doc.get("custom_quota") if user_doc else None
        effective_quota = int(custom_q) if custom_q is not None else DAILY_TOKEN_QUOTA
        total_tokens = int(row.get("total_tokens") or 0)
        messages_left = max(0.0, (effective_quota - total_tokens) / TOKENS_PER_MESSAGE)
        by_user.append({
            "user_id": user_id,
            "name": name,
            "email": email,
            "used": total_tokens,
            "calls": int(row.get("calls") or 0),
            "messages_left": round(messages_left, 1),
            "quota": effective_quota,
        })

    return {
        "today_total": today_total,
        "today_calls": today_calls,
        "default_quota": DAILY_TOKEN_QUOTA,
        "by_provider": by_provider,
        "by_user": by_user,
        "days": 1,
        "data": by_user,
    }

@router.get("/api/admin/users")
async def admin_get_all_users(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    user_docs = await db["users"].aggregate([
        {"$lookup": {
            "from": "usage_logs",
            "let": {"uid": {"$toString": "$_id"}},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$user_id", "$$uid"]}}},
                {"$group": {
                    "_id": None,
                    "tokens_total": {"$sum": "$tokens"},
                    "tokens_today": {
                        "$sum": {"$cond": [{"$eq": ["$date", today]}, "$tokens", 0]},
                    },
                }},
            ],
            "as": "usage",
        }},
        {"$limit": 1000},
    ]).to_list(length=1000)

    results = []
    for u in user_docs:
        uid_str = str(u["_id"])
        usage = (u.get("usage") or [None])[0] or {}
        tokens_today = int(usage.get("tokens_today") or 0)
        tokens_total = int(usage.get("tokens_total") or 0)

        custom_q = u.get("custom_quota")
        effective_quota = int(custom_q) if custom_q is not None else DAILY_TOKEN_QUOTA
        messages_left = max(0.0, (effective_quota - tokens_today) / TOKENS_PER_MESSAGE)

        created = u.get("created_at")
        created_str = created.strftime('%Y-%m-%d %H:%M') if isinstance(created, datetime) else "N/A"

        results.append({
            "user_id": uid_str,
            "name": u.get("name", "Unknown"),
            "email": u.get("email", ""),
            "role": u.get("role", "user"),
            "status": u.get("status", "active"),
            "custom_quota": custom_q,
            "quota": effective_quota,
            "tokens_today": tokens_today,
            "tokens_total": tokens_total,
            "messages_left": round(messages_left, 1),
            "created_at": created_str
        })

    return {"users": results}

@router.post("/api/admin/users/{user_id}/role")
async def admin_update_user_role(user_id: str, payload: dict, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)

    new_role = payload.get("role")
    if new_role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Invalid role specified")

    res = await db["users"].update_one({"_id": ObjectId(user_id)}, {"$set": {"role": new_role}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    # `get_current_user` reads the user document from a 60s cache (1.14).
    # Without this the promotion or demotion would not take effect until it
    # expired, which for a demotion is a minute of admin access after it was
    # revoked.
    invalidate_user_cache(user_id)
    return {"message": f"User role updated to {new_role}"}

@router.post("/api/admin/users/{user_id}/status")
async def admin_update_user_status(user_id: str, payload: dict, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)

    new_status = payload.get("status")
    if new_status not in ("active", "suspended"):
        raise HTTPException(status_code=400, detail="Invalid status specified")

    res = await db["users"].update_one({"_id": ObjectId(user_id)}, {"$set": {"status": new_status}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    invalidate_user_cache(user_id)
    if new_status == "suspended":
        # A suspension has to end the session, not just fail the next request:
        # an outstanding refresh token would keep minting access tokens.
        await db["refresh_tokens"].update_many(
            {"user_id": user_id, "used": False}, {"$set": {"used": True, "revoked": True}}
        )
    return {"message": f"User status updated to {new_status}"}

@router.post("/api/admin/users/{user_id}/quota")
async def admin_update_user_quota(user_id: str, payload: dict, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)

    reset_today = payload.get("reset_today", False)
    custom_quota = payload.get("custom_quota")

    if reset_today:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        await db["usage_logs"].delete_many({"user_id": user_id, "date": today})

    if custom_quota is not None:
        try:
            custom_q_val = int(custom_quota) if custom_quota != "" else None
            if custom_q_val is None:
                await db["users"].update_one({"_id": ObjectId(user_id)}, {"$unset": {"custom_quota": ""}})
            else:
                await db["users"].update_one({"_id": ObjectId(user_id)}, {"$set": {"custom_quota": custom_q_val}})
        except ValueError:
            raise HTTPException(status_code=400, detail="Quota must be a valid integer")

    return {"message": "User quota updated successfully"}

@router.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    if current_user["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own admin account")

    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user id")

    res = await db["users"].delete_one({"_id": oid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    invalidate_user_cache(user_id)
    deleted = await purge_user_data(user_id)
    logger.info("Deleted user %s and their data: %s", user_id, deleted)
    return {"message": "User and associated data deleted", "deleted": deleted}


# Every collection and bucket that stores something on a user's behalf. Three of
# these were missing from the delete path, so "delete this user" left their
# uploaded source documents, their saved literature surveys and the actual PDF
# bytes in GridFS sitting in the database under an account that no longer
# existed — including whatever private manuscript text those sources contained.
_USER_OWNED_COLLECTIONS = (
    "usage_logs",
    "manuscripts",
    "manuscript_references",
    # A version holds the full text of a draft, so leaving it behind would keep
    # exactly what deleting the account was meant to remove.
    "manuscript_versions",
    # Outstanding refresh tokens for an account that no longer exists (1.14).
    "refresh_tokens",
    "pdf_chats",
    "sources",
    "literature",
)


async def purge_user_data(user_id: str) -> dict:
    """Remove everything stored for *user_id*. Returns a per-collection count."""
    deleted: dict = {}
    for name in _USER_OWNED_COLLECTIONS:
        try:
            result = await db[name].delete_many({"user_id": user_id})
            deleted[name] = int(result.deleted_count)
        except Exception as e:
            logger.warning("Could not purge %s for user %s: %s", name, user_id, e)
            deleted[name] = -1

    deleted["pdfs"] = await _purge_user_pdfs(user_id)
    return deleted


async def _purge_user_pdfs(user_id: str) -> int:
    """Drop the user's GridFS PDFs. Their chunks go with the file document —
    deleting only `pdfs.files` would orphan the chunks, which are the bulk."""
    from core.database import get_pdf_bucket

    bucket = get_pdf_bucket()
    removed = 0
    try:
        cursor = db["pdfs.files"].find({"metadata.user_id": user_id}, {"_id": 1})
        async for doc in cursor:
            try:
                await bucket.delete(doc["_id"])
                removed += 1
            except Exception as e:
                logger.warning("Could not delete GridFS file %s: %s", doc["_id"], e)
    except Exception as e:
        logger.warning("Could not enumerate GridFS files for user %s: %s", user_id, e)
        return -1
    return removed

@router.get("/api/admin/system-status")
# Tighter than the global default: `force=true` fans out a live probe to every
# configured integration, so this is the most expensive route in the app for an
# attacker who has got hold of an admin token.
@limiter.limit("10/minute")
async def admin_system_status(
    request: Request,
    force: bool = False,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)

    from services.admin_status import collect_system_status

    return await collect_system_status(force=force)


@router.post("/api/admin/system-status/probe")
@limiter.limit("20/minute")
async def admin_probe_one(request: Request, payload: dict, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    name = (payload or {}).get("name")
    if not name or not isinstance(name, str):
        raise HTTPException(status_code=400, detail="name is required")
    from services.admin_status import probe_one
    try:
        source = await probe_one(name.strip())
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown source")
    return {"source": source}


@router.get("/api/admin/events")
async def admin_events(
    limit: int = 80,
    name: Optional[str] = None,
    ok: Optional[bool] = None,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    from services.api_telemetry import recent_events
    return {"events": recent_events(limit, name=name, ok=ok)}


@router.post("/api/admin/sources/enabled")
async def admin_source_enabled(payload: dict, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    name = (payload or {}).get("name")
    enabled = (payload or {}).get("enabled")
    if not name or not isinstance(name, str):
        raise HTTPException(status_code=400, detail="name is required")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be a boolean")
    from services.admin_status import set_source_enabled
    try:
        return await set_source_enabled(name.strip(), enabled)
    except KeyError:
        raise HTTPException(status_code=400, detail="Source is not skippable")


@router.post("/api/admin/sources/{name}/enabled")
async def admin_source_enabled_by_path(name: str, payload: dict, current_user: dict = Depends(get_current_user)):
    body = dict(payload or {})
    body["name"] = name
    return await admin_source_enabled(body, current_user)
