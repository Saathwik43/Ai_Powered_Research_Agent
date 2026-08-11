"""Environment and logging bootstrap.

Import this before anything that reads settings at import time (auth, database,
ai.llm_provider, the integration clients). ``main`` and ``routers`` both pull it
in first so the ordering holds no matter which entrypoint loads the app.
"""

from dotenv import load_dotenv

load_dotenv()

import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    filename="backend.log",
)

DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
]


def get_cors_origins() -> list:
    """Comma-separated CORS_ORIGINS if set, else the local Vite dev ports."""
    cors_origins_env = os.getenv("CORS_ORIGINS")
    if cors_origins_env:
        return cors_origins_env.split(",")
    return list(DEFAULT_CORS_ORIGINS)
