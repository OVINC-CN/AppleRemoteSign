import base64
import json
import stat
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import SecretStr, ValidationError

from apple_remote_sign.config import Settings
from apple_remote_sign.integrations.credentials import AppleCredentials
from apple_remote_sign.integrations.process import CommandRunner
from apple_remote_sign.runtime import build_context, validate_files
from tests.fake_redis import FakeRedis


def credential_document(key):
    der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return {
        "issuer_id": "test-issuer",
        "key_id": "TESTKEY",
        "private_key": base64.b64encode(der).decode(),
    }


@pytest.fixture
def mounted_settings(settings, tmp_path):
    settings.rcodesign_bin = Path(sys.executable)
    settings.rcodesign_work_dir = tmp_path / "unrelated work directory"
    settings.rcodesign_work_dir.mkdir()
    settings.signing_certificate_file = tmp_path / "signing.p12"
    settings.signing_certificate_file.write_bytes(b"test p12")
    settings.signing_password_file = tmp_path / "password"
    settings.signing_password_file.write_text("test-password\n")
    settings.apple_api_key_file.parent.mkdir()
    settings.apple_api_key_file.write_text(
        json.dumps(credential_document(ec.generate_private_key(ec.SECP256R1())))
    )
    settings.apple_api_key_file.chmod(0o400)
    return settings


def test_apple_key_file_environment_with_no_separate_key_metadata(settings, monkeypatch):
    custom = "/mounted credentials/arbitrary filename.json"
    monkeypatch.setenv("APPLE_KEY_FILE", custom)
    configured = Settings(_env_file=None, **settings.model_dump(exclude={"apple_api_key_file"}))
    assert configured.apple_api_key_file == Path(custom)
    assert (
        not {"apple_key_id", "apple_issuer_id", "apple_private_key_file"}
        & Settings.model_fields.keys()
    )


def test_apple_key_file_configuration_is_required(settings, monkeypatch):
    monkeypatch.delenv("APPLE_KEY_FILE", raising=False)
    with pytest.raises(ValidationError, match="APPLE_KEY_FILE"):
        Settings(_env_file=None, **settings.model_dump(exclude={"apple_api_key_file"}))


def test_runtime_reads_mounted_json_without_encoding_or_modifying_it(mounted_settings, monkeypatch):
    settings = mounted_settings

    def unexpected_command(*_args, **_kwargs):
        pytest.fail("Startup must not run a credential conversion command")

    monkeypatch.setattr(CommandRunner, "run", unexpected_command)
    redis = FakeRedis()
    monkeypatch.setattr("apple_remote_sign.runtime.build_redis_client", lambda _settings: redis)
    original = settings.apple_api_key_file.read_bytes()
    document = json.loads(original)
    with build_context(settings) as context:
        assert not redis.closed
        assert context.tool.api_key_file == settings.apple_api_key_file
        assert context.apple.key_id == document["key_id"]
        assert context.apple.issuer_id == document["issuer_id"]
        key = serialization.load_der_private_key(
            base64.b64decode(document["private_key"]), password=None
        )
        assert context.apple.private_key.public_key() == key.public_key()
        assert document["private_key"] not in context.redactor.clean(document["private_key"])
        assert not list(settings.rcodesign_work_dir.iterdir())
    assert settings.apple_api_key_file.read_bytes() == original
    assert redis.closed
    assert stat.S_IMODE(settings.apple_api_key_file.stat().st_mode) == 0o400


@pytest.mark.parametrize("enabled", [False, True])
def test_runtime_owns_optional_notification_client(mounted_settings, monkeypatch, enabled):
    settings = mounted_settings
    webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-robot-secret"
    settings.wecom_webhook_url = SecretStr(webhook) if enabled else None
    settings.wecom_timeout_seconds = 2.5
    monkeypatch.setattr("apple_remote_sign.runtime.build_redis_client", lambda _: FakeRedis())
    monkeypatch.setattr("apple_remote_sign.runtime.build_s3_client", lambda _: Mock())
    with build_context(settings) as context:
        notifier = context.notifier
        if enabled:
            assert notifier is not None
            assert notifier.client is not context.apple.client
            assert notifier.client.timeout.read == 2.5
            assert not notifier.client.is_closed
            assert "test-robot-secret" not in context.redactor.clean(webhook + " test-robot-secret")
        else:
            assert notifier is None
    if notifier is not None:
        assert notifier.client.is_closed


@pytest.mark.parametrize("kind", ["relative", "missing"])
def test_invalid_apple_key_file_fails_at_startup(mounted_settings, kind):
    settings = mounted_settings
    settings.apple_api_key_file = (
        Path("relative.json")
        if kind == "relative"
        else settings.apple_api_key_file.with_name("missing")
    )
    with pytest.raises(RuntimeError, match="mounted file"):
        validate_files(settings)


@pytest.mark.parametrize(
    "content",
    [
        b"not JSON: do-not-print-this-secret",
        b"[]",
        b'{"private_key":"do-not-print-this-secret"}',
        b'{"key_id":12,"issuer_id":"issuer","private_key":"do-not-print-this-secret"}',
        b'{"key_id":"KEY","issuer_id":"","private_key":"do-not-print-this-secret"}',
        b'{"key_id":"KEY","issuer_id":"issuer","private_key":"do-not-print-this-secret"}',
        b'{"key_id":"KEY","issuer_id":"issuer","private_key":"aW52YWxpZCBERVI="}',
    ],
)
def test_invalid_credential_json_is_actionable_without_exposing_secrets(content):
    with pytest.raises(RuntimeError, match="APPLE_KEY_FILE") as raised:
        AppleCredentials.from_json(content)
    assert "do-not-print-this-secret" not in str(raised.value)


def test_unsupported_key_curve_is_rejected():
    document = credential_document(ec.generate_private_key(ec.SECP384R1()))
    with pytest.raises(RuntimeError, match="EC P-256"):
        AppleCredentials.from_json(json.dumps(document).encode())
