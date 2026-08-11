"""Operator surface: per-user quota view plus the admin console endpoints
(usage, user management, API health, telemetry events, source toggles)."""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from core.auth import get_current_user
from core.database import db
from services.usage_tracker import DAILY_TOKEN_QUOTA, TOKENS_PER_MESSAGE, get_user_usage

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
    user_docs, usage_rows = await asyncio.gather(
        db["users"].find({}).to_list(length=1000),
        db["usage_logs"].aggregate([
            {"$group": {
                "_id": "$user_id",
                "tokens_total": {"$sum": "$tokens"},
                "tokens_today": {
                    "$sum": {"$cond": [{"$eq": ["$date", today]}, "$tokens", 0]},
                },
            }},
        ]).to_list(length=5000),
    )
    usage_by_user = {row["_id"]: row for row in usage_rows}

    results = []
    for u in user_docs:
        uid_str = str(u["_id"])
        usage = usage_by_user.get(uid_str) or {}
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

    res = await db["users"].delete_one({"_id": ObjectId(user_id)})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    await db["usage_logs"].delete_many({"user_id": user_id})
    await db["manuscripts"].delete_many({"user_id": user_id})
    await db["pdf_chats"].delete_many({"user_id": user_id})
    return {"message": "User and associated data deleted"}

@router.get("/api/admin/system-status")
async def admin_system_status(
    force: bool = False,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)

    from services.admin_status import collect_system_status

    return await collect_system_status(force=force)


@router.post("/api/admin/system-status/probe")
async def admin_probe_one(payload: dict, current_user: dict = Depends(get_current_user)):
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
