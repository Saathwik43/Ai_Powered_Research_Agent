"""AWS-6 — PDF bytes move from GridFS to S3 without stranding what came before.

The risky part of this change is not the upload, it is everything that has to
keep working around it: a `pdf_chats.file_id` written last month is a GridFS
ObjectId and must still stream; ownership moved from GridFS metadata into the S3
key and must still be enforced; and the whole thing has to stay off when
`S3_BUCKET` is unset, because that is how local development and this suite run.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import object_store
from core.object_store import (
    PdfNotFound,
    key_for,
    open_pdf,
    owner_of,
    purge_user_objects,
    store_pdf,
)

USER_ID = "000000000000000000000009"
OTHER_ID = "00000000000000000000000f"
LEGACY_ID = "5f2b1c8e9d4a3b2c1e0f7a61"  # a 24-hex GridFS ObjectId


def _enabled():
    """Turn the S3 path on for one test. `is_enabled` reads the global at call
    time, so patching the attribute is enough — no import juggling."""
    return patch.object(object_store, "S3_BUCKET", "test-bucket")


# ─── keys and ownership ───────────────────────────────────────────────────────


def test_key_carries_the_owner_and_not_the_filename():
    """The filename is attacker-influenced and not unique, so it stays out of the
    path. The owner goes in, because that is what the read check reads."""
    key = key_for(USER_ID, "../../etc/passwd.pdf")

    assert key.startswith(f"uploads/{USER_ID}/")
    assert key.endswith(".pdf")
    assert "passwd" not in key
    assert ".." not in key


def test_key_for_rejects_a_user_id_that_would_escape_the_prefix():
    with pytest.raises(ValueError):
        key_for("../other", "x.pdf")


def test_owner_of_reads_the_key_and_ignores_everything_else():
    assert owner_of(f"uploads/{USER_ID}/abc123.pdf") == USER_ID
    # A GridFS id has no owner in it — the caller must fall through to metadata.
    assert owner_of(LEGACY_ID) is None
    assert owner_of("uploads/only-two-parts") is None
    assert owner_of("") is None


def test_two_uploads_by_one_user_never_collide():
    assert key_for(USER_ID, "paper.pdf") != key_for(USER_ID, "paper.pdf")


# ─── writes ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_upload_goes_to_gridfs_when_s3_is_not_configured():
    """The default. Unsetting one variable has to restore the old behaviour
    exactly, or this is not a reversible rollout."""
    bucket = AsyncMock()
    bucket.upload_from_stream.return_value = LEGACY_ID

    with patch.object(object_store, "S3_BUCKET", ""), \
         patch("core.database.get_pdf_bucket", lambda: bucket):
        file_id = await store_pdf(USER_ID, "paper.pdf", b"%PDF-1.4")

    assert file_id == LEGACY_ID
    kwargs = bucket.upload_from_stream.await_args.kwargs
    assert kwargs["metadata"]["user_id"] == USER_ID


@pytest.mark.anyio
async def test_upload_goes_to_s3_when_configured_and_is_encrypted():
    client = MagicMock()

    with _enabled(), patch.object(object_store, "_get_client", lambda: client):
        file_id = await store_pdf(USER_ID, "paper.pdf", b"%PDF-1.4")

    assert file_id.startswith(f"uploads/{USER_ID}/")
    kwargs = client.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "test-bucket"
    assert kwargs["Key"] == file_id
    assert kwargs["Body"] == b"%PDF-1.4"
    assert kwargs["ServerSideEncryption"] == "AES256"


# ─── reads ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_legacy_gridfs_id_still_streams_after_s3_is_switched_on():
    """The whole point of the id-shape check. Turning S3 on must not orphan a
    single file uploaded before it."""
    grid_out = MagicMock()
    grid_out.metadata = {"user_id": USER_ID}
    grid_out.readchunk = AsyncMock(side_effect=[b"abc", b"def", b""])
    bucket = AsyncMock()
    bucket.open_download_stream.return_value = grid_out

    with _enabled(), patch("core.database.get_pdf_bucket", lambda: bucket):
        owner, chunks = await open_pdf(LEGACY_ID)
        body = b"".join([c async for c in chunks])

    assert owner == USER_ID
    assert body == b"abcdef"


@pytest.mark.anyio
async def test_a_gridfs_file_with_no_metadata_reports_no_owner():
    """`metadata is None` used to be a 500 on `None.get`. No recorded owner has
    to mean nobody owns it, so the caller's comparison fails closed."""
    grid_out = MagicMock()
    grid_out.metadata = None
    grid_out.readchunk = AsyncMock(side_effect=[b""])
    bucket = AsyncMock()
    bucket.open_download_stream.return_value = grid_out

    with patch("core.database.get_pdf_bucket", lambda: bucket):
        owner, _ = await open_pdf(LEGACY_ID)

    assert owner is None
    assert owner != USER_ID


@pytest.mark.anyio
async def test_s3_object_streams_in_chunks_and_closes_the_body():
    body = MagicMock()
    body.read.side_effect = [b"one", b"two", b""]
    client = MagicMock()
    client.get_object.return_value = {"Body": body}
    key = f"uploads/{USER_ID}/abc.pdf"

    with _enabled(), patch.object(object_store, "_get_client", lambda: client):
        owner, chunks = await open_pdf(key)
        data = b"".join([c async for c in chunks])

    assert owner == USER_ID
    assert data == b"onetwo"
    # Not closing returns nothing to the connection pool.
    body.close.assert_called_once()


@pytest.mark.anyio
async def test_a_missing_object_is_not_found_rather_than_a_500():
    client = MagicMock()
    client.get_object.side_effect = Exception("NoSuchKey")

    with _enabled(), patch.object(object_store, "_get_client", lambda: client):
        with pytest.raises(PdfNotFound):
            await open_pdf(f"uploads/{USER_ID}/gone.pdf")


@pytest.mark.anyio
async def test_a_malformed_id_is_not_found_rather_than_a_crash():
    with _enabled():
        with pytest.raises(PdfNotFound):
            await open_pdf("not-a-key-and-not-an-objectid")


@pytest.mark.anyio
async def test_an_s3_key_cannot_be_read_while_s3_is_disabled():
    """Otherwise unsetting the variable would 500 on every id written while it
    was set, instead of reporting the file as unavailable."""
    with patch.object(object_store, "S3_BUCKET", ""):
        with pytest.raises(PdfNotFound):
            await open_pdf(f"uploads/{USER_ID}/abc.pdf")


# ─── deletes ──────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_purge_deletes_every_version_not_just_the_current_object():
    """The regression guard for a bug the fixtures originally missed.

    The bucket is versioned, so deleting a key without a VersionId only writes a
    delete marker — the object vanishes from a listing while the PDF itself stays
    in the bucket. Verified against the live bucket: a purge left 0 objects and 1
    version. Every delete must therefore name a version.
    """
    client = MagicMock()
    client.list_object_versions.side_effect = [
        {
            "Versions": [
                {"Key": f"uploads/{USER_ID}/a.pdf", "VersionId": "v1"},
                {"Key": f"uploads/{USER_ID}/a.pdf", "VersionId": "v0"},
            ],
            "DeleteMarkers": [{"Key": f"uploads/{USER_ID}/a.pdf", "VersionId": "dm1"}],
            "IsTruncated": False,
        }
    ]

    with _enabled(), patch.object(object_store, "_get_client", lambda: client):
        removed = await purge_user_objects(USER_ID)

    sent = client.delete_objects.call_args.kwargs["Delete"]["Objects"]
    assert all("VersionId" in obj for obj in sent), "a delete without VersionId only hides the object"
    assert {obj["VersionId"] for obj in sent} == {"v1", "v0", "dm1"}
    # Both historical versions count; the delete marker is bookkeeping, not a document.
    assert removed == 2


@pytest.mark.anyio
async def test_purge_follows_pagination_with_both_version_markers():
    """A user with more than a page of uploads must not keep the remainder, and
    version listings page on two markers rather than one token."""
    client = MagicMock()
    client.list_object_versions.side_effect = [
        {
            "Versions": [{"Key": f"uploads/{USER_ID}/a.pdf", "VersionId": "v1"}],
            "IsTruncated": True,
            "NextKeyMarker": "k1",
            "NextVersionIdMarker": "v1",
        },
        {
            "Versions": [{"Key": f"uploads/{USER_ID}/b.pdf", "VersionId": "v2"}],
            "IsTruncated": False,
        },
    ]

    with _enabled(), patch.object(object_store, "_get_client", lambda: client):
        removed = await purge_user_objects(USER_ID)

    assert removed == 2
    first, second = client.list_object_versions.call_args_list
    assert first.kwargs["Prefix"] == f"uploads/{USER_ID}/"
    # The second page must continue, not restart.
    assert second.kwargs["KeyMarker"] == "k1"
    assert second.kwargs["VersionIdMarker"] == "v1"


@pytest.mark.anyio
async def test_purge_is_a_no_op_when_s3_is_not_configured():
    """Returns 0, not -1: nothing failed, there is simply nothing there. The
    GridFS half of the purge runs regardless."""
    with patch.object(object_store, "S3_BUCKET", ""):
        assert await purge_user_objects(USER_ID) == 0


@pytest.mark.anyio
async def test_a_broken_s3_does_not_abort_the_rest_of_the_purge():
    """Mirrors the per-collection behaviour: -1 records the failure and the
    account deletion still completes."""
    client = MagicMock()
    client.list_object_versions.side_effect = Exception("s3 said no")

    with _enabled(), patch.object(object_store, "_get_client", lambda: client):
        assert await purge_user_objects(USER_ID) == -1
