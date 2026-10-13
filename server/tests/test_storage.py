import io

import pytest
from app.core.config import Settings
from app.core.errors import ServiceError
from app.providers.storage import LocalStorage, S3Storage
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber
from cryptography.fernet import Fernet


async def test_local_files_are_encrypted_and_bound_to_their_object_key(tmp_path):
    storage = LocalStorage(tmp_path, Fernet.generate_key().decode())
    key = "task/document.pdf"
    content = b"Synthetic private document"
    await storage.put(key, content)
    raw = storage.path(key).read_bytes()
    assert content not in raw and await storage.read(key) == content
    assert storage.path(key).stat().st_mode & 0o777 == 0o600
    other_key = "task/other.pdf"
    with pytest.raises(ServiceError, match="integrity"):
        storage.cipher.decrypt(other_key, raw)
    broken = raw[:-1] + bytes([raw[-1] ^ 1])
    storage.path(key).write_bytes(broken)
    with pytest.raises(ServiceError, match="integrity"):
        await storage.read(key)


async def test_existing_content_cannot_be_replaced_or_read_with_another_key(tmp_path):
    key = "task/document.pdf"
    storage = LocalStorage(tmp_path, Fernet.generate_key().decode())
    await storage.put(key, b"first")
    original = storage.path(key).read_bytes()
    with pytest.raises(ServiceError) as conflict:
        await storage.put(key, b"replacement")
    assert conflict.value.code == "storage_conflict"
    assert storage.path(key).read_bytes() == original
    other = LocalStorage(tmp_path, Fernet.generate_key().decode())
    with pytest.raises(ServiceError, match="integrity"):
        await other.read(key)


def s3_storage():
    return S3Storage(
        Settings(
            storage="s3",
            s3_endpoint="http://127.0.0.1:9000",
            s3_access_key="synthetic-test-access",
            s3_secret_key="synthetic-test-secret",
        )
    )


async def test_s3_immutable_encrypted_put_and_duplicate_read():
    key = "task/document.pdf"
    storage = s3_storage()
    stored = storage.cipher.encrypt(key, b"synthetic")
    body = StreamingBody(io.BytesIO(stored), len(stored))
    put = {"Bucket": "bid", "Key": key, "Body": ANY, "IfNoneMatch": "*"}
    with Stubber(storage.client) as stub:
        stub.add_response("put_object", {}, put)
        stub.add_client_error(
            "put_object", "PreconditionFailed", http_status_code=412, expected_params=put
        )
        stub.add_response("get_object", {"Body": body}, {"Bucket": "bid", "Key": key})
        await storage.put(key, b"synthetic")
        await storage.put(key, b"synthetic")
        assert body._raw_stream.closed
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "code,status,exit_code",
    [("NoSuchKey", 404, 4), ("AccessDenied", 503, 4), ("SlowDown", 503, 3)],
)
async def test_s3_errors_are_sanitized_and_classified(code, status, exit_code):
    key = "task/document.pdf"
    storage = s3_storage()
    with Stubber(storage.client) as stub:
        stub.add_client_error(
            "get_object",
            code,
            service_message="must not leak provider details",
            expected_params={"Bucket": "bid", "Key": key},
        )
        with pytest.raises(ServiceError) as error:
            await storage.read(key)
        assert error.value.status == status and error.value.exit_code == exit_code
        assert "provider details" not in error.value.message
        stub.assert_no_pending_responses()


async def test_s3_rejects_traversal_before_any_provider_request():
    storage = s3_storage()
    with Stubber(storage.client):
        with pytest.raises(ServiceError) as error:
            await storage.read("../task/document.pdf")
        assert error.value.code == "invalid_storage_key"
