"""Disposable, loopback-only browser test app. Never packaged into the runtime image."""

import base64
import hashlib
import json
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apple_remote_sign.app import create_app
from apple_remote_sign.config import Settings
from apple_remote_sign.integrations.cdn import CdnSigner
from apple_remote_sign.integrations.s3 import content_disposition
from apple_remote_sign.locks import JobLocks
from apple_remote_sign.persistence.access_repository import AccessRepository
from apple_remote_sign.persistence.auth_repository import AuthRepository
from apple_remote_sign.persistence.models import Base
from apple_remote_sign.persistence.repository import JobRepository
from apple_remote_sign.redaction import Redactor
from apple_remote_sign.services.context import ServiceContext
from apple_remote_sign.storage import FileStorage
from tests.fake_redis import FakeRedis
from tests.fakes import FakeApple, FakeArtifacts, FakeTool

workspace = tempfile.TemporaryDirectory(prefix="ars-browser-")
root = Path(workspace.name)
backend = "http://127.0.0.1:18765"
settings = Settings(
    _env_file=None,
    database_url="mysql+pymysql://unused/unused",
    redis_url="redis://unused:6379/3",
    apple_api_key_file=root / "unused.json",
    data_dir=root,
    oidc_issuer_url=backend,
    oidc_client_id="browser-tests",
    oidc_client_secret="test-only",
    oidc_redirect_uri=backend + "/api/v1/auth/callback",
    oidc_admin_usernames=["admin"],
    oidc_allowed_usernames=["alice", "bob"],
    frontend_url="http://127.0.0.1:4173",
    cookie_secure=False,
    s3_bucket="browser-artifacts",
    cdn_base_url=backend,
    cdn_sign_key="browser-cdn-key",
)
engine = create_engine(f"sqlite:///{root / 'test.db'}")
Base.metadata.create_all(engine)
sessions = sessionmaker(engine, expire_on_commit=False)
access = AccessRepository(sessions)
desktop = access.create_application("com.example.desktop", "Desktop")
private_app = access.create_application("com.example.private", "Private")
access.set_users(desktop.id, ["alice"])
auth = AuthRepository(sessions, settings)
for username in ("admin", "alice", "bob"):
    auth.bind_user(username, backend, "subject-" + username)
apple = FakeApple()
apple.current = "Accepted"
artifacts = FakeArtifacts()
context = ServiceContext(
    JobRepository(sessions),
    FileStorage(root / "data", settings.max_upload_bytes),
    FakeTool(),
    apple,
    JobLocks(FakeRedis()),
    Redactor(),
    artifacts,
    CdnSigner(settings),
)
app = create_app(settings, context)
private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
jwk.update(kid="browser-test", alg="RS256", use="sig")
flows = {}
codes = {}


@app.get("/app-dist/{key:path}", include_in_schema=False)
def download(key: str, request: Request):
    object_key = "app-dist/" + key
    try:
        timestamp, rand, uid, digest = request.query_params.get("eo_sign", "").split("-")
        age = time.time() - int(timestamp)
    except ValueError:
        return Response(status_code=403)
    path = request.scope["raw_path"].decode("ascii")
    expected = hashlib.md5(
        f"{path}-{timestamp}-{rand}-{uid}-{settings.cdn_sign_key.get_secret_value()}".encode()
    ).hexdigest()
    if (
        object_key not in artifacts.objects
        or uid != "0"
        or not 0 <= age <= 300
        or not secrets.compare_digest(digest, expected)
    ):
        return Response(status_code=403)
    return Response(
        artifacts.objects[object_key],
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(key.rsplit("/", 1)[-1])},
    )


def provider(request):
    if request.url.path.endswith("openid-configuration"):
        return httpx.Response(
            200,
            json={
                "issuer": backend,
                "authorization_endpoint": backend + "/test/authorize",
                "token_endpoint": backend + "/test/token",
                "jwks_uri": backend + "/test/jwks",
                "id_token_signing_alg_values_supported": ["RS256"],
            },
        )
    if request.url.path == "/test/jwks":
        return httpx.Response(200, json={"keys": [jwk]})
    form = parse_qs(request.content.decode())
    flow, username = codes.pop(form["code"][0])
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"][0].encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == flow["code_challenge"]
    token = jwt.encode(
        {
            "iss": backend,
            "sub": "subject-" + username,
            "aud": "browser-tests",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "nonce": flow["nonce"],
            "preferred_username": username,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "browser-test"},
    )
    return httpx.Response(
        200, json={"access_token": "test-only", "token_type": "Bearer", "id_token": token}
    )


app.state.oidc_client.client_kwargs["transport"] = httpx.MockTransport(provider)


@app.get("/test/authorize", include_in_schema=False)
def authorize(request: Request):
    flow_id = secrets.token_urlsafe(16)
    flows[flow_id] = dict(request.query_params)
    return HTMLResponse(f'''<!doctype html><html lang="en"><h1>Test identity provider</h1>
<form action="/test/approve"><input type="hidden" name="flow" value="{flow_id}">
<button name="username" value="admin">admin</button>
<button name="username" value="alice">alice</button>
<button name="username" value="bob">bob</button></form></html>''')


@app.get("/test/approve", include_in_schema=False)
def approve(flow: str, username: str):
    assert username in {"admin", "alice", "bob"}
    transaction = flows.pop(flow)
    code = secrets.token_urlsafe(16)
    codes[code] = (transaction, username)
    return RedirectResponse(
        settings.oidc_redirect_uri + "?" + urlencode({"state": transaction["state"], "code": code})
    )
