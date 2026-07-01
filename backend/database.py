import os
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
logger = logging.getLogger(__name__)

client = AsyncIOMotorClient(MONGO_URI)
db = client.research_agent_db

async def ping_db():
    try:
        await client.admin.command('ping')
        logger.info("Pinged your deployment. You successfully connected to MongoDB!")
        return True
    except Exception as e:
        logger.error(e)
        return False

async def ensure_indexes():
    await db["manuscripts"].create_index([("user_id", 1), ("topic", 1)])
    await db["literature"].create_index([("user_id", 1), ("query", 1)])
    await db["users"].create_index("email", unique=True)
    logger.info("Database indexes ensured.")
