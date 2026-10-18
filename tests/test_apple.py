import base64
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from apple_remote_sign.domain import OperationError
from apple_remote_sign.integrations.apple import AppleNotaryClient
from apple_remote_sign.integrations.credentials import AppleCredentials

IDENTIFIER = "498bd264-2807-48c9-ad28-ccfe491b0ce6"


@pytest.fixture
def key():
    return ec.generate_private_key(ec.SECP256R1())


def make_client(key, handler):
    der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    credentials = AppleCredentials.from_json(
        json.dumps(
            {
                "key_id": "KEY123",
                "issuer_id": "test-issuer",
                "private_key": base64.b64encode(der).decode(),
            }
        ).encode()
    )
    return AppleNotaryClient(httpx.Client(transport=httpx.MockTransport(handler)), credentials)


def test_json_credentials_jwt_and_single_status_request(key):
    requests = []

    def handler(request):
        requests.append(request)
        token = request.headers["authorization"].removeprefix("Bearer ")
        claims = jwt.decode(
            token,
            key.public_key(),
            algorithms=["ES256"],
            audience="appstoreconnect-v1",
            issuer="test-issuer",
        )
        assert claims["exp"] - claims["iat"] == 300
        assert abs(time.time() - claims["iat"]) < 5
        assert jwt.get_unverified_header(token)["kid"] == "KEY123"
        assert request.url.path.endswith(f"/submissions/{IDENTIFIER}")
        return httpx.Response(
            200, json={"data": {"id": IDENTIFIER, "attributes": {"status": "In Progress"}}}
        )

    client = make_client(key, handler)
    assert client.status(IDENTIFIER) == "In Progress"
    assert len(requests) == 1
    client.client.close()


@pytest.mark.parametrize("status", ["Accepted", "Invalid", "Rejected"])
def test_terminal_statuses(key, status):
    client = make_client(
        key,
        lambda _: httpx.Response(
            200,
            json={"data": {"id": IDENTIFIER, "attributes": {"status": status}}},
        ),
    )
    assert client.status(IDENTIFIER) == status
    client.client.close()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, text="private upstream output"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"data": {"id": "wrong", "attributes": {"status": "Accepted"}}}),
        httpx.Response(
            200, json={"data": {"id": IDENTIFIER, "attributes": {"status": "NewStatus"}}}
        ),
    ],
)
def test_bad_responses_are_not_inferred_as_success(key, response):
    client = make_client(key, lambda _: response)
    with pytest.raises(OperationError):
        client.status(IDENTIFIER)
    client.client.close()


def test_transport_failure_has_no_retry(key):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    client = make_client(key, handler)
    with pytest.raises(OperationError, match="ReadTimeout"):
        client.status(IDENTIFIER)
    assert len(requests) == 1
    client.client.close()


def test_log_download_does_not_forward_apple_token(key):
    def handler(request):
        if request.url.host == "appstoreconnect.apple.com":
            assert request.headers.get("authorization")
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": IDENTIFIER,
                        "attributes": {
                            "developerLogUrl": "https://logs.example/file?signature=test"
                        },
                    }
                },
            )
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"statusSummary": "Package Invalid"})

    client = make_client(key, handler)
    assert client.log(IDENTIFIER)["statusSummary"] == "Package Invalid"
    client.client.close()
