import os
import bcrypt
import logging
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from database import db
from dotenv import load_dotenv
import usage_tracker

load_dotenv()

logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY is not configured in the environment.")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

bearer_scheme = HTTPBearer()


# ─── Password Helpers ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ─── JWT Helpers ───────────────────────────────────────────────────────────────

def create_access_token(user_id: str, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {"sub": user_id, "email": email, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ─── Auth Dependency ───────────────────────────────────────────────────────────

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> dict:
    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload.")
    
    # Set the current user ID for usage tracking in the current async context
    usage_tracker.current_user_id.set(user_id)
    
    # Also attach role if possible, but standard token only has email. 
    # Let's fetch the user to get the role.
    collection = db["users"]
    from bson import ObjectId
    user = await collection.find_one({"_id": ObjectId(user_id)})
    role = user.get("role", "user") if user else "user"
    
    return {"user_id": user_id, "email": payload.get("email"), "role": role}


# ─── Signup ────────────────────────────────────────────────────────────────────

async def signup_user(email: str, password: str, name: str) -> dict:
    collection = db["users"]
    existing = await collection.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered.")

    hashed = hash_password(password)
    user_doc = {
        "email": email,
        "name": name,
        "password": hashed,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await collection.insert_one(user_doc)
    user_id = str(result.inserted_id)
    token = create_access_token(user_id, email)
    return {"token": token, "user": {"id": user_id, "email": email, "name": name, "role": "user"}}


# ─── Login ─────────────────────────────────────────────────────────────────────

async def login_user(email: str, password: str) -> dict:
    collection = db["users"]
    user = await collection.find_one({"email": email})
    if not user or not verify_password(password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    user_id = str(user["_id"])
    token = create_access_token(user_id, email)
    return {"token": token, "user": {"id": user_id, "email": email, "name": user.get("name", ""), "role": user.get("role", "user")}}


# ─── Seed Admin ────────────────────────────────────────────────────────────────

async def seed_admin():
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("ADMIN_PASSWORD")
    admin_name = os.getenv("ADMIN_NAME", "Admin")

    if not admin_email or not admin_password:
        return

    collection = db["users"]
    existing = await collection.find_one({"email": admin_email})
    if not existing:
        hashed = hash_password(admin_password)
        await collection.insert_one({
            "email": admin_email,
            "name": admin_name,
            "password": hashed,
            "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"Admin user seeded: {admin_email}")
    else:
        logger.info(f"Admin user already exists: {admin_email}")
