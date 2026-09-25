"""PDF byte storage (AWS-6).

Uploaded PDFs used to go straight into GridFS, which means the bytes live in
Mongo. On Atlas M0 that is 512MB shared with every application document — drafts,
versions, chats, the cache tier — so a handful of uploads competes for space with
the data the app actually queries. S3 costs cents, has no such ceiling, and the
bucket already carries lifecycle rules that bound what it can grow into.

**GridFS is still the default.** Nothing changes until ``S3_BUCKET`` is set: the
functions here dispatch to the bucket when it is configured and to GridFS when it
is not. That makes the rollout reversible by unsetting one variable, and it keeps
local development and the test suite working with no AWS credentials at all.

**Old files stay readable.** A file written before this landed has a 24-hex
GridFS ObjectId for an id; one written after has an S3 key. ``open_pdf`` tells
them apart by shape and reads from whichever store owns it, so enabling S3 does
not strand a single existing ``pdf_chats.file_id``.

**Ownership is in the key, not in metadata.** GridFS recorded the owner in
``metadata.user_id``; an S3 object records it in its key
(``uploads/<user_id>/<uuid>.pdf``). That is strictly stronger — the check cannot
be skipped by writing an object without metadata, which is exactly the case that
used to 500 (`routers/pdf.py`, "no recorded owner means nobody owns it").

**boto3 in a thread, not aioboto3.** ``aioboto3`` drags in ``aiobotocore``, which
pins ``botocore`` narrowly and would fight the pinned requirement set for no gain
here: uploads are capped at 10/minute and downloads at 30/minute, so these calls
are nowhere near hot enough to justify the dependency risk. Offloading to a
worker thread keeps the event loop free, which is the only property that actually
matters.

The offload is ``anyio.to_thread.run_sync``, not ``asyncio.to_thread``: the test
suite runs every async test on both the asyncio and the trio backend, and
``asyncio.to_thread`` needs a running asyncio loop. anyio is already a dependency
— it is what Starlette runs its own sync work through — and it works on either.
``run_sync`` takes positional arguments only, hence the ``partial``s below.

Retries use boto3's own standard mode rather than ``core/retry.py`` — that seam
is built on httpx exception types and does not apply to botocore.
"""

from __future__ import annotations

import logging
import os
import re
from functools import partial
from uuid import uuid4

import anyio.to_thread

logger = logging.getLogger(__name__)

S3_BUCKET = (os.getenv("S3_BUCKET") or "").strip()
S3_PREFIX = (os.getenv("S3_PREFIX") or "uploads").strip().strip("/")
AWS_REGION = (os.getenv("AWS_REGION") or "ap-south-1").strip()

# Streamed back to the browser a chunk at a time. 256KB is large enough that a
# 10MB paper is ~40 reads rather than hundreds, and small enough that one slow
# client cannot pin megabytes of process memory per request.
CHUNK_BYTES = 256 * 1024

# A GridFS id is exactly 24 hex characters. An S3 key made here always contains a
# path separator, so the two cannot be confused for one another.
_OBJECT_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")

# user_id reaches us from a verified token and is a Mongo ObjectId string, but it
# is about to become part of a key path, so it is checked rather than trusted.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class PdfNotFound(Exception):
    """No stored PDF for that id, in either store."""


def is_enabled() -> bool:
    """True when uploads should go to S3. One variable turns this on or off."""
    return bool(S3_BUCKET)


_client = None


def _get_client():
    """Lazily build one module-level S3 client.

    Built on first use rather than at import so that neither the test suite nor a
    developer without credentials pays for a client they never call.
    """
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        _client = boto3.client(
            "s3",
            region_name=AWS_REGION,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=30,
            ),
        )
    return _client


def key_for(user_id: str, filename: str = "") -> str:
    """Build the object key for a new upload.

    The original filename is deliberately *not* part of the key. It is attacker-
    influenced, it is not unique, and it would put a user-chosen string into a
    path — the name is kept in the `pdf_chats` document where it is only ever
    read back as data.
    """
    if not _SAFE_ID_RE.match(str(user_id or "")):
        raise ValueError("unsafe user_id for an object key")
    return f"{S3_PREFIX}/{user_id}/{uuid4().hex}.pdf"


def owner_of(storage_id: str) -> str | None:
    """The user id embedded in an S3 key, or None if this is not one."""
    parts = str(storage_id or "").split("/")
    if len(parts) == 3 and parts[0] == S3_PREFIX and _SAFE_ID_RE.match(parts[1]):
        return parts[1]
    return None


def _is_legacy_id(storage_id: str) -> bool:
    return bool(_OBJECT_ID_RE.match(str(storage_id or "")))


# ─── writes ───────────────────────────────────────────────────────────────────


async def store_pdf(user_id: str, filename: str, data: bytes) -> str:
    """Persist *data* and return the id to hand back to the client.

    An S3 key when the bucket is configured, a GridFS ObjectId string otherwise.
    Callers store whatever comes back and pass it to ``open_pdf`` unchanged.
    """
    if not is_enabled():
        from core.database import get_pdf_bucket

        file_id = await get_pdf_bucket().upload_from_stream(
            filename,
            data,
            metadata={"user_id": str(user_id), "content_type": "application/pdf"},
        )
        return str(file_id)

    key = key_for(user_id, filename)
    client = _get_client()
    await anyio.to_thread.run_sync(
        partial(
            client.put_object,
            Bucket=S3_BUCKET,
            Key=key,
            Body=data,
            ContentType="application/pdf",
            # Redundant with the bucket default, but it means an object is
            # encrypted even if the bucket-level rule is ever loosened.
            ServerSideEncryption="AES256",
        )
    )
    return key


# ─── reads ────────────────────────────────────────────────────────────────────


async def open_pdf(storage_id: str):
    """Return ``(owner_id, chunks)`` for a stored PDF.

    ``chunks`` is an async iterator of bytes. ``owner_id`` is whoever the store
    says owns the file — the caller compares it against the signed-in user and
    decides. Raises :class:`PdfNotFound` when there is nothing to read.

    Both stores are handled here so that turning S3 on does not orphan the files
    written before it.
    """
    if _is_legacy_id(storage_id):
        return await _open_from_gridfs(storage_id)

    owner = owner_of(storage_id)
    if owner is None or not is_enabled():
        raise PdfNotFound(storage_id)

    client = _get_client()
    try:
        obj = await anyio.to_thread.run_sync(
            partial(client.get_object, Bucket=S3_BUCKET, Key=storage_id)
        )
    except Exception as exc:  # NoSuchKey, AccessDenied, and anything transport
        logger.info("S3 read failed for %s: %s", storage_id, exc.__class__.__name__)
        raise PdfNotFound(storage_id) from exc

    body = obj["Body"]

    async def chunks():
        try:
            while True:
                chunk = await anyio.to_thread.run_sync(body.read, CHUNK_BYTES)
                if not chunk:
                    break
                yield chunk
        finally:
            # The connection returns to the pool only when the body is closed.
            # A client that disconnects mid-download would otherwise leak it.
            await anyio.to_thread.run_sync(body.close)

    return owner, chunks()


async def _open_from_gridfs(storage_id: str):
    from bson import ObjectId

    from core.database import get_pdf_bucket

    try:
        grid_out = await get_pdf_bucket().open_download_stream(ObjectId(storage_id))
    except Exception as exc:
        raise PdfNotFound(storage_id) from exc

    # A file written without metadata has `metadata is None`. No recorded owner
    # means nobody owns it — the caller's comparison then fails closed.
    owner = (grid_out.metadata or {}).get("user_id")

    async def chunks():
        while True:
            chunk = await grid_out.readchunk()
            if not chunk:
                break
            yield chunk

    return owner, chunks()


# ─── deletes ──────────────────────────────────────────────────────────────────


async def purge_user_objects(user_id: str) -> int:
    """Delete every stored version of every object belonging to *user_id*.

    Returns the number of real object versions removed, or -1 if the store could
    not be enumerated. Returns 0 when S3 is not configured — the GridFS half of
    the purge is `routers/admin.py`'s job and runs regardless, because a user who
    uploaded before the switch has bytes in both stores.

    **Versions, not keys.** The bucket has versioning enabled, so a plain
    ``delete_objects`` writes a *delete marker* and keeps the previous version:
    the object disappears from a listing while the actual PDF stays in the
    bucket. The lifecycle rule would eventually expire it, but "delete this
    user's data" has to be true when it returns, not thirty days later. So this
    enumerates ``list_object_versions`` and deletes each ``(Key, VersionId)``,
    delete markers included — otherwise the markers themselves accumulate.
    """
    if not is_enabled() or not _SAFE_ID_RE.match(str(user_id or "")):
        return 0

    client = _get_client()
    prefix = f"{S3_PREFIX}/{user_id}/"
    removed = 0
    key_marker = None
    version_marker = None
    try:
        while True:
            kwargs = {"Bucket": S3_BUCKET, "Prefix": prefix}
            if key_marker:
                kwargs["KeyMarker"] = key_marker
            if version_marker:
                kwargs["VersionIdMarker"] = version_marker
            page = await anyio.to_thread.run_sync(
                partial(client.list_object_versions, **kwargs)
            )

            versions = page.get("Versions") or []
            markers = page.get("DeleteMarkers") or []
            targets = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for item in list(versions) + list(markers)
            ]
            if targets:
                await anyio.to_thread.run_sync(
                    partial(
                        client.delete_objects,
                        Bucket=S3_BUCKET,
                        Delete={"Objects": targets},
                    )
                )
                # Delete markers are bookkeeping, not the user's documents, so
                # only real versions are counted.
                removed += len(versions)

            if not page.get("IsTruncated"):
                break
            key_marker = page.get("NextKeyMarker")
            version_marker = page.get("NextVersionIdMarker")
    except Exception as exc:
        logger.warning("Could not purge S3 objects for user %s: %s", user_id, exc)
        return -1
    return removed
