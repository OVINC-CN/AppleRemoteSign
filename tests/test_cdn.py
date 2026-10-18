import hashlib
import re
from urllib.parse import parse_qs, quote, unquote, urlsplit

import pytest
from pydantic import ValidationError

from apple_remote_sign.config import Settings
from apple_remote_sign.integrations import cdn
from apple_remote_sign.integrations.cdn import CdnSigner


def test_official_type_a_example(settings, monkeypatch):
    values = settings.model_dump()
    values.update(
        cdn_base_url="https://www.example.com/",
        cdn_sign_key="DvYmqE81E1F9R791H6lmht",
        cdn_sign_param="token",
    )
    monkeypatch.setattr(cdn.time, "time", lambda: 1721028437)
    monkeypatch.setattr(cdn.secrets, "token_hex", lambda _size: "Kv4cPTAAP5YTi")
    signer = CdnSigner(Settings(_env_file=None, **values))
    assert signer.sign("foo.jpg") == (
        "https://www.example.com/foo.jpg?token="
        "1721028437-Kv4cPTAAP5YTi-0-0fbdca749d7ab784750685347e42075c"
    )


@pytest.mark.parametrize("filename", ["应用 1.2.zip", "release%20.zip", "a?b#c+&.zip"])
def test_signature_uses_exact_escaped_path(settings, filename):
    key = "app-dist/app/2026/01/02/job/" + filename
    url = urlsplit(CdnSigner(settings).sign(key))
    assert url.path == quote("/" + key, safe="/")
    assert unquote(url.path) == "/" + key
    assert not url.fragment
    token = parse_qs(url.query)["eo_sign"][0]
    timestamp, rand, uid, digest = token.split("-")
    assert int(timestamp) > 0
    assert re.fullmatch("[0-9a-f]{32}", rand)
    assert uid == "0"
    expected = hashlib.md5(f"{url.path}-{timestamp}-{rand}-0-test-cdn-secret".encode()).hexdigest()
    assert digest == expected


def test_urls_are_fresh_even_within_the_same_second(settings, monkeypatch):
    monkeypatch.setattr(cdn.time, "time", lambda: 1721028437)
    signer = CdnSigner(settings)
    assert signer.sign("artifact.zip") != signer.sign("artifact.zip")


@pytest.mark.parametrize(
    "overrides",
    [
        {"cdn_base_url": "https://cdn.example.com/path"},
        {"cdn_base_url": "https://user:secret@cdn.example.com"},
        {"cdn_base_url": "https://cdn.example.com?key=secret"},
        {"cdn_base_url": "http://cdn.example.com"},
        {"cdn_base_url": "//cdn.example.com"},
        {"cdn_sign_key": ""},
        {"cdn_sign_param": "bad-key"},
        {"cdn_sign_param": ""},
        {"s3_bucket": ""},
        {"s3_prefix": "app/../dist"},
        {"s3_prefix": "app\\dist"},
        {"s3_endpoint_url": "https://user:secret@s3.example.com"},
        {"s3_endpoint_url": "file:///tmp/artifacts"},
        {"s3_connect_timeout_seconds": 0},
        {"s3_read_timeout_seconds": -1},
        {"aws_access_key_id": "unpaired-key", "aws_secret_access_key": None},
    ],
)
def test_invalid_distribution_config_is_rejected(settings, overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **(settings.model_dump() | overrides))


@pytest.mark.parametrize("prefix, expected", [("/custom//dist/", "custom//dist/"), ("/", "")])
def test_prefix_normalization(settings, prefix, expected):
    configured = Settings(_env_file=None, **(settings.model_dump() | {"s3_prefix": prefix}))
    assert configured.s3_prefix == expected
