import base64
import hashlib
import json
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from apple_remote_sign.api.oidc import build_oidc_client, login_cookie_name
from apple_remote_sign.app import create_app
from apple_remote_sign.config import Settings
from apple_remote_sign.persistence.access_models import LoginTransaction, WebSession
from apple_remote_sign.persistence.models import utcnow


@pytest.fixture
def oidc(settings, context):
    app = create_app(settings, context)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update(kid="test-key", use="sig", alg="RS256")
    flow = {"overrides": {}, "userinfo": {"sub": "subject-alice", "preferred_username": "alice"}}

    def handle(request):
        root = settings.oidc_issuer_url
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": flow.get("issuer", root),
                    "authorization_endpoint": root + "/authorize",
                    "token_endpoint": root + "/token",
                    "jwks_uri": root + "/jwks",
                    "userinfo_endpoint": root + "/userinfo",
                    "id_token_signing_alg_values_supported": flow.get("algorithms", ["RS256"]),
                },
            )
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [jwk]})
        if request.url.path == "/userinfo":
            return httpx.Response(200, json=flow["userinfo"])
        assert request.url.path == "/token"
        if flow.get("timeout"):
            raise httpx.ReadTimeout("secret-provider-error")
        form = parse_qs(request.content.decode())
        verifier = form["code_verifier"][0]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == flow["challenge"]
        assert form["redirect_uri"][0] == settings.oidc_redirect_uri
        claims = {
            "iss": root,
            "aud": settings.oidc_client_id,
            "sub": "subject-alice",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "nonce": flow["nonce"],
            "preferred_username": "alice",
            **flow["overrides"],
        }
        if flow.get("omit_username"):
            del claims["preferred_username"]
        key = (
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
            if flow.get("bad_signature")
            else private
        )
        token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})
        return httpx.Response(
            200, json={"access_token": "provider-secret", "token_type": "Bearer", "id_token": token}
        )

    app.state.oidc_client.client_kwargs["transport"] = httpx.MockTransport(handle)
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as client:
        yield client, flow


def start(client, flow):
    response = client.get("/api/v1/auth/login")
    assert response.status_code == 302, response.text
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    flow["nonce"] = query["nonce"][0]
    flow["challenge"] = query["code_challenge"][0]
    return query["state"][0]


def test_oidc_full_flow_state_consumption_and_session_rotation(oidc, settings, context):
    client, flow = oidc
    state = start(client, flow)
    cookie_name = login_cookie_name(settings, state)
    browser = client.cookies.get(cookie_name)
    result = client.get("/api/v1/auth/callback", params={"state": state, "code": "code"})
    assert result.status_code == 303 and result.headers["location"].endswith("/jobs")
    cookie = client.cookies.get(settings.cookie_name)
    assert cookie and cookie != browser
    assert (
        "HttpOnly" in result.headers["set-cookie"]
        and "SameSite=lax" in result.headers["set-cookie"]
    )
    assert client.get("/api/v1/me").json()["data"]["username"] == "alice"
    with context.repository.sessions() as session:
        assert not session.scalars(select(LoginTransaction)).all()
        stored = session.scalars(select(WebSession)).one()
        assert stored.token_hash != cookie
    client.cookies.set(cookie_name, browser, domain="testserver.local")
    result = client.get("/api/v1/auth/callback", params={"state": state, "code": "code"})
    assert "login_expired" in result.headers["location"]
    state = start(client, flow)
    assert (
        client.get("/api/v1/auth/callback", params={"state": state, "code": "new-code"})
        .headers["location"]
        .endswith("/jobs")
    )
    new_cookie = client.cookies.get(settings.cookie_name)
    assert new_cookie != cookie
    client.cookies.clear()
    client.cookies.set(settings.cookie_name, cookie)
    assert client.get("/api/v1/me").status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://evil.example"},
        {"aud": "other"},
        {"nonce": "wrong"},
        {"exp": 1},
        {"preferred_username": "unknown"},
        {"sub": ""},
    ],
)
def test_invalid_id_token_or_disallowed_user(oidc, overrides):
    client, flow = oidc
    flow["overrides"] = overrides
    state = start(client, flow)
    result = client.get("/api/v1/auth/callback", params={"state": state, "code": "code"})
    assert "/login?error=" in result.headers["location"]
    assert client.get("/api/v1/me").status_code == 401


@pytest.mark.parametrize("failure", ["bad_signature", "timeout"])
def test_provider_error_and_invalid_signature(oidc, failure):
    client, flow = oidc
    flow[failure] = True
    state = start(client, flow)
    result = client.get("/api/v1/auth/callback", params={"state": state, "code": "code"})
    assert result.headers["location"].endswith("login_failed")
    assert "secret" not in result.text


def test_username_from_userinfo_and_subject_mismatch(oidc):
    client, flow = oidc
    flow["omit_username"] = True
    state = start(client, flow)
    assert (
        client.get("/api/v1/auth/callback", params={"state": state, "code": "code"})
        .headers["location"]
        .endswith("/jobs")
    )
    flow["userinfo"]["sub"] = "wrong"
    state = start(client, flow)
    assert (
        client.get("/api/v1/auth/callback", params={"state": state, "code": "code"})
        .headers["location"]
        .endswith("login_failed")
    )


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ("state", "login_cookie_missing"),
        ("cookie", "login_cookie_missing"),
        ("expired", "login_expired"),
        ("denied", "login_failed"),
    ],
)
def test_bad_or_expired_login_transaction(oidc, settings, context, failure, reason):
    client, flow = oidc
    state = start(client, flow)
    params = {"state": state, "code": "code"}
    if failure == "state":
        params["state"] = "unknown"
    elif failure == "cookie":
        client.cookies.clear()
    elif failure == "expired":
        with context.repository.sessions.begin() as session:
            transaction = session.scalars(select(LoginTransaction)).one()
            transaction.expires_at = utcnow() - timedelta(seconds=1)
    else:
        params["error"] = "access_denied"
    assert client.get("/api/v1/auth/callback", params=params).headers["location"].endswith(reason)
    assert client.get("/api/v1/me").status_code == 401


@pytest.mark.parametrize("origin", ["http://localhost:8000", "https://testserver"])
def test_login_alias_redirects_before_creating_transaction(oidc, settings, context, origin):
    client, _flow = oidc
    response = client.get(origin + "/api/v1/auth/login")
    assert response.status_code == 303
    assert response.headers["location"] == settings.oidc_redirect_uri.replace("callback", "login")
    assert "set-cookie" not in response.headers
    with context.repository.sessions() as session:
        assert not session.scalars(select(LoginTransaction)).all()


def test_parallel_login_transactions_keep_independent_cookies(oidc, settings):
    client, flow = oidc
    first_state = start(client, flow)
    first = dict(flow)
    second_state = start(client, flow)
    second = dict(flow)
    first_cookie = login_cookie_name(settings, first_state)
    second_cookie = login_cookie_name(settings, second_state)
    assert client.cookies.get(first_cookie) != client.cookies.get(second_cookie)
    for state, saved in [(first_state, first), (second_state, second)]:
        flow.update(saved)
        result = client.get("/api/v1/auth/callback", params={"state": state, "code": "code"})
        assert result.headers["location"].endswith("/jobs")
        assert client.cookies.get(login_cookie_name(settings, state)) is None
        if state == first_state:
            assert client.cookies.get(second_cookie)
    assert client.get("/api/v1/me").status_code == 200


def test_wrong_browser_cookie_is_rejected_without_consuming_state(oidc, settings, context, caplog):
    client, flow = oidc
    state = start(client, flow)
    cookie_name = login_cookie_name(settings, state)
    browser = client.cookies.get(cookie_name)
    client.cookies.set(cookie_name, "wrong-browser", domain="testserver.local", path="/")
    result = client.get("/api/v1/auth/callback", params={"state": state, "code": "code"})
    assert result.headers["location"].endswith("login_cookie_mismatch")
    assert client.get("/api/v1/me").status_code == 401
    with context.repository.sessions() as session:
        assert len(session.scalars(select(LoginTransaction)).all()) == 1
    assert "\nreason: login_cookie_mismatch\n" in caplog.text
    assert browser not in caplog.text and state not in caplog.text


def test_discovery_issuer_must_match_configured_provider(oidc):
    client, flow = oidc
    flow["issuer"] = "https://other.example"
    assert client.get("/api/v1/auth/login").status_code == 502


def test_unsigned_provider_is_rejected(oidc):
    client, flow = oidc
    flow["algorithms"] = ["none"]
    assert client.get("/api/v1/auth/login").status_code == 502


@pytest.mark.parametrize(
    "changed_metadata", [{}, {"issuer": "https://other.example"}, {"algorithms": ["none"]}]
)
def test_callback_on_another_worker_validates_provider(oidc, settings, changed_metadata):
    client, flow = oidc
    state = start(client, flow)
    replacement = build_oidc_client(settings)
    replacement.client_kwargs["transport"] = client.app.state.oidc_client.client_kwargs["transport"]
    client.app.state.oidc_client = replacement
    flow.update(changed_metadata)
    result = client.get("/api/v1/auth/callback", params={"state": state, "code": "code"})
    expected = "login_failed" if changed_metadata else "/jobs"
    assert result.headers["location"].endswith(expected)
    assert client.get("/api/v1/me").status_code == (401 if changed_metadata else 200)


def test_cookie_configuration(settings):
    values = settings.model_dump()
    values.update(cookie_samesite="none", cookie_secure=False)
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(_env_file=None, **values)
    values.update(cookie_secure=True, cookie_domain="testserver")
    configured = Settings(_env_file=None, **values)
    assert configured.cookie_domain == "testserver"
    values["cookie_domain"] = "unrelated.example.com"
    with pytest.raises(ValidationError, match="COOKIE_DOMAIN"):
        Settings(_env_file=None, **values)
    assert "api_token" not in Settings.model_fields
