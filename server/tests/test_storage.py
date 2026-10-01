import io
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.errors import ServiceError
from app.providers.storage import LocalStorage, S3Storage
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber
from cryptography.fernet import Fernet


async def test_local_files_are_encrypted_and_bound_to_their_tenant_key(tmp_path):
    org = uuid4()
    storage = LocalStorage(tmp_path, Fernet.generate_key().decode())
    key = f"org/{org}/task/document.pdf"
    content = b"Synthetic private document"
    await storage.put(org, key, content)
    raw = storage.path(org, key).read_bytes()
    assert content not in raw and await storage.read(org, key) == content
    assert storage.path(org, key).stat().st_mode & 0o777 == 0o600
    other_key = f"org/{uuid4()}/task/document.pdf"
    with pytest.raises(ServiceError, match="integrity"):
        storage.cipher.decrypt(other_key, raw)
    broken = raw[:-1] + bytes([raw[-1] ^ 1])
    storage.path(org, key).write_bytes(broken)
    with pytest.raises(ServiceError, match="integrity"):
        await storage.read(org, key)


async def test_existing_content_cannot_be_replaced_or_read_with_another_key(tmp_path):
    org = uuid4()
    key = f"org/{org}/task/document.pdf"
    storage = LocalStorage(tmp_path, Fernet.generate_key().decode())
    await storage.put(org, key, b"first")
    original = storage.path(org, key).read_bytes()
    with pytest.raises(ServiceError) as conflict:
        await storage.put(org, key, b"replacement")
    assert conflict.value.code == "storage_conflict"
    assert storage.path(org, key).read_bytes() == original
    other = LocalStorage(tmp_path, Fernet.generate_key().decode())
    with pytest.raises(ServiceError, match="integrity"):
        await other.read(org, key)


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
    org = uuid4()
    key = f"org/{org}/task/document.pdf"
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
        await storage.put(org, key, b"synthetic")
        await storage.put(org, key, b"synthetic")
        assert body._raw_stream.closed
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "code,status,exit_code",
    [("NoSuchKey", 404, 4), ("AccessDenied", 503, 4), ("SlowDown", 503, 3)],
)
async def test_s3_errors_are_sanitized_and_classified(code, status, exit_code):
    org = uuid4()
    key = f"org/{org}/task/document.pdf"
    storage = s3_storage()
    with Stubber(storage.client) as stub:
        stub.add_client_error(
            "get_object",
            code,
            service_message="must not leak provider details",
            expected_params={"Bucket": "bid", "Key": key},
        )
        with pytest.raises(ServiceError) as error:
            await storage.read(org, key)
        assert error.value.status == status and error.value.exit_code == exit_code
        assert "provider details" not in error.value.message
        stub.assert_no_pending_responses()


async def test_s3_rejects_other_tenant_before_any_provider_request():
    storage = s3_storage()
    with Stubber(storage.client):
        with pytest.raises(ServiceError) as error:
            await storage.read(uuid4(), f"org/{uuid4()}/task/document.pdf")
        assert error.value.code == "invalid_storage_key"
