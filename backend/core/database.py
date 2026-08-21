import os
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
logger = logging.getLogger(__name__)

client = AsyncIOMotorClient(MONGO_URI)
db = client.research_agent_db

from motor.motor_asyncio import AsyncIOMotorGridFSBucket

def get_pdf_bucket():
    return AsyncIOMotorGridFSBucket(db, bucket_name="pdfs")

async def ping_db():
    try:
        await client.admin.command('ping')
        logger.info("Pinged your deployment. You successfully connected to MongoDB!")
        return True
    except Exception as e:
        logger.error(e)
        return False

_TTL_UNSET = object()


def _index_keys(keys):
    if isinstance(keys, str):
        return [(keys, 1)]
    return list(keys)


def _default_index_name(keys) -> str:
    return "_".join(f"{field}_{direction}" for field, direction in _index_keys(keys))


def _index_options_match(existing: dict, unique: bool, expire_after) -> bool:
    if bool(existing.get("unique")) != unique:
        return False
    if expire_after is _TTL_UNSET:
        return "expireAfterSeconds" not in existing
    return existing.get("expireAfterSeconds") == expire_after


async def _ensure_index(collection, keys, *, unique: bool = False, expireAfterSeconds=_TTL_UNSET, name=None):
    """Create an index, replacing a same-name index when options differ.

    MongoDB raises IndexKeySpecsConflict when the auto-generated name matches
    an existing index with different options (unique vs not, TTL vs not).
    Never raise: a bad index must not take down startup.
    """
    index_name = name or _default_index_name(keys)
    options: dict = {}
    if unique:
        options["unique"] = True
    if expireAfterSeconds is not _TTL_UNSET:
        options["expireAfterSeconds"] = expireAfterSeconds
    if name:
        options["name"] = name
    coll_name = getattr(collection, "name", collection)

    try:
        info = await collection.index_information()
        existing = info.get(index_name) if isinstance(info, dict) else None
        if isinstance(existing, dict):
            if _index_options_match(existing, unique, expireAfterSeconds):
                return
            await collection.drop_index(index_name)
        await collection.create_index(keys, **options)
    except Exception as exc:
        logger.warning("Could not ensure index %s on %s: %s", index_name, coll_name, exc)


async def ensure_indexes():
    # Manuscripts: (user_id, topic) unique so save cannot race into duplicate drafts.
    await _ensure_index(db["manuscripts"], [("user_id", 1), ("topic", 1)], unique=True)
    await _ensure_index(db["usage_logs"], [("user_id", 1), ("date", 1)])
    await _ensure_index(db["literature"], [("user_id", 1), ("query", 1)])
    await _ensure_index(db["users"], "email", unique=True)
    await _ensure_index(db["pdf_chats"], [("user_id", 1), ("updated_at", -1)])
    # Sources are many-per-topic (multiple PDFs/URLs). A leftover unique index
    # with this name is what crashed Render (IndexKeySpecsConflict).
    await _ensure_index(db["sources"], [("user_id", 1), ("topic", 1)])
    await _ensure_index(db["revoked_tokens"], "jti", unique=True)
    await _ensure_index(db["revoked_tokens"], "expires_at", expireAfterSeconds=0)
    logger.info("Database indexes ensured.")
