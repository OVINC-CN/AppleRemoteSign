import logging
import time
from typing import Protocol
from urllib.parse import urlparse

import httpx
import jwt
from opentelemetry.trace import SpanKind

from apple_remote_sign.domain import OperationError
from apple_remote_sign.integrations.credentials import AppleCredentials
from apple_remote_sign.observability import operation

NOTARY_URL = "https://appstoreconnect.apple.com/notary/v2/submissions"
type JsonObject = dict[str, object]
logger = logging.getLogger(__name__)


class NotaryClient(Protocol):
    def status(self, submission_id: str) -> str: ...

    def log(self, submission_id: str) -> JsonObject: ...


class AppleNotaryClient:
    def __init__(self, client: httpx.Client, credentials: AppleCredentials) -> None:
        self.client = client
        self.private_key = credentials.private_key
        self.key_id = credentials.key_id
        self.issuer_id = credentials.issuer_id

    def _token(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {"iss": self.issuer_id, "iat": now, "exp": now + 300, "aud": "appstoreconnect-v1"},
            self.private_key,
            algorithm="ES256",
            headers={"kid": self.key_id, "typ": "JWT"},
        )

    def _get(self, url: str, *, authenticated: bool = True) -> JsonObject:
        host = urlparse(url).hostname
        with operation("apple.http", kind=SpanKind.CLIENT) as span:
            span.set_attribute("server.address", host or "")
            span.set_attribute("http.request.method", "GET")
            response = self._request(url, authenticated=authenticated)
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code != 200:
                raise OperationError(f"Apple request returned HTTP {response.status_code}.")
            try:
                value = response.json()
            except ValueError:
                raise OperationError("Apple returned invalid JSON.") from None
            if not isinstance(value, dict):
                raise OperationError("Apple returned an unexpected response structure.")
            return value

    def _request(self, url: str, *, authenticated: bool) -> httpx.Response:
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self._token()}"
        try:
            return self.client.get(url, headers=headers, follow_redirects=False)
        except httpx.HTTPError as exc:
            logger.warning(
                "apple.http.failed\nhost: %s\nerror: %s",
                urlparse(url).hostname,
                type(exc).__name__,
            )
            raise OperationError(f"Apple request failed: {type(exc).__name__}.") from None

    @staticmethod
    def _attributes(value: JsonObject, submission_id: str) -> JsonObject:
        data = value.get("data")
        if not isinstance(data, dict) or data.get("id") != submission_id:
            raise OperationError("Apple returned an unexpected submission ID.")
        attributes = data.get("attributes")
        if not isinstance(attributes, dict):
            raise OperationError("Apple response is missing submission attributes.")
        return attributes

    def status(self, submission_id: str) -> str:
        attributes = self._attributes(self._get(f"{NOTARY_URL}/{submission_id}"), submission_id)
        status = attributes.get("status")
        if status not in ("In Progress", "Accepted", "Invalid", "Rejected"):
            raise OperationError("Apple returned an unrecognized notarization status.")
        assert isinstance(status, str)
        return status

    def log(self, submission_id: str) -> JsonObject:
        attributes = self._attributes(
            self._get(f"{NOTARY_URL}/{submission_id}/logs"), submission_id
        )
        url = attributes.get("developerLogUrl")
        if not isinstance(url, str) or urlparse(url).scheme != "https":
            raise OperationError("Apple returned an invalid notarization log URL.")
        # The presigned storage URL must never receive the Apple bearer token.
        return self._get(url, authenticated=False)
