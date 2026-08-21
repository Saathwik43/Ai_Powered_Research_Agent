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

async def _ensure_unique_user_topic() -> None:
    """(user_id, topic) must be unique so save cannot race into duplicate drafts.

    The previous index on the same keys was not unique. Drop it first; if live
    duplicates already exist, log and keep serving rather than crash startup.
    """
    manuscripts = db["manuscripts"]
    name = "user_id_1_topic_1"
    try:
        info = await manuscripts.index_information()
        existing = info.get(name)
        if existing is not None and not existing.get("unique"):
            await manuscripts.drop_index(name)
        await manuscripts.create_index([("user_id", 1), ("topic", 1)], unique=True)
    except Exception as exc:
        logger.warning(
            "Could not make manuscripts(user_id, topic) unique: %s", exc
        )


async def ensure_indexes():
    await _ensure_unique_user_topic()
    await db["usage_logs"].create_index([("user_id", 1), ("date", 1)])
    await db["literature"].create_index([("user_id", 1), ("query", 1)])
    await db["users"].create_index("email", unique=True)
    await db["pdf_chats"].create_index([("user_id", 1), ("updated_at", -1)])
    await db["sources"].create_index([("user_id", 1), ("topic", 1)])
    await db["revoked_tokens"].create_index("jti", unique=True)
    await db["revoked_tokens"].create_index("expires_at", expireAfterSeconds=0)
    logger.info("Database indexes ensured.")
