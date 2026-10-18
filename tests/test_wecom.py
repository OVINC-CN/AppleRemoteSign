import json
import logging

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from apple_remote_sign.config import Settings
from apple_remote_sign.integrations.wecom import WeComNotifier
from apple_remote_sign.persistence.models import Job
from apple_remote_sign.redaction import Redactor
from tests.fakes import TEST_APPLICATION_ID

WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-robot-secret"


@pytest.fixture
def job():
    return Job(
        id="00000000-0000-4000-8000-000000000002",
        application_id=TEST_APPLICATION_ID,
        filename="应用 v1.2.3.zip",
        submission_id="00000000-0000-4000-8000-000000000003",
    )


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_webhook_disables_notifications(settings, value):
    configured = Settings(_env_file=None, **(settings.model_dump() | {"wecom_webhook_url": value}))
    assert configured.wecom_webhook_url is None
    assert configured.wecom_secrets == []


def test_webhook_environment_and_secret_redaction(settings, monkeypatch):
    monkeypatch.setenv("WECOM_WEBHOOK_URL", WEBHOOK_URL)
    monkeypatch.setenv("WECOM_TIMEOUT_SECONDS", "2.5")
    configured = Settings(
        _env_file=None,
        **settings.model_dump(exclude={"wecom_webhook_url", "wecom_timeout_seconds"}),
    )
    assert configured.wecom_webhook_url.get_secret_value() == WEBHOOK_URL
    assert configured.wecom_timeout_seconds == 2.5
    assert "test-robot-secret" not in repr(configured)
    assert "test-robot-secret" not in configured.model_dump_json()
    assert "test-robot-secret" not in Redactor(configured.wecom_secrets).clean(
        f"{WEBHOOK_URL} test-robot-secret"
    )


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret",
        "https:///missing-host?key=secret",
        "https://user:secret@qyapi.weixin.qq.com/webhook",
        "https://qyapi.weixin.qq.com/webhook?key=secret#fragment",
        "https://qyapi.weixin.qq.com:invalid/webhook?key=secret",
        "https://[invalid?key=secret",
        "https://qyapi.weixin.qq.com/webhook?key=sec ret",
    ],
)
def test_invalid_webhook_is_rejected_without_exposing_value(settings, url):
    with pytest.raises(ValidationError, match="WECOM_WEBHOOK_URL") as raised:
        Settings(_env_file=None, **(settings.model_dump() | {"wecom_webhook_url": url}))
    assert url not in str(raised.value)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_webhook_timeout_must_be_positive_and_finite(settings, timeout):
    with pytest.raises(ValidationError, match="wecom_timeout_seconds"):
        Settings(_env_file=None, **(settings.model_dump() | {"wecom_timeout_seconds": timeout}))


@pytest.mark.parametrize("event", ["submitted", "completed", "failed"])
@pytest.mark.parametrize("submitter", ["用户 · alice", "OpenAPI · Test key"])
def test_sends_only_requested_markdown_fields_without_forwarding_auth(job, event, submitter):
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == WEBHOOK_URL
        assert request.method == "POST"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        payload = json.loads(request.content)
        title = {"submitted": "新签名提交", "completed": "签名成功", "failed": "签名失败"}[event]
        assert payload == {
            "msgtype": "markdown",
            "markdown": {
                "content": "\n".join(
                    [
                        f"**AppleRemoteSign · {title}**",
                        f"**文件名**: {job.filename}",
                        "**应用名称**: 测试应用",
                        f"**任务 ID**: {job.id}",
                        f"**提交人**: {submitter}",
                    ]
                )
            },
        }
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        WeComNotifier(client, SecretStr(WEBHOOK_URL)).notify(
            job, event, application_name="测试应用", submitter=submitter
        )
    assert len(requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, text=WEBHOOK_URL),
        httpx.Response(302, headers={"location": "https://other.example/webhook"}),
        httpx.Response(200, json={"errcode": 93000, "errmsg": WEBHOOK_URL}),
        httpx.Response(200, text=WEBHOOK_URL),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"errcode": "0"}),
        httpx.Response(200, json={"errcode": False}),
    ],
)
def test_bad_responses_are_logged_without_secrets_retries_or_redirects(job, response, caplog):
    requests = []

    def handle(request):
        requests.append(request)
        return response

    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        notifier = WeComNotifier(client, SecretStr(WEBHOOK_URL))
        with caplog.at_level(logging.INFO, logger="apple_remote_sign.integrations.wecom"):
            notifier.notify(
                job, "completed", application_name="测试应用", submitter="OpenAPI · Test key"
            )
    assert len(requests) == 1
    assert "notification_failed" in caplog.text
    assert "notification_sent" not in caplog.text
    assert "test-robot-secret" not in caplog.text
    assert WEBHOOK_URL not in caplog.text


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError, httpx.InvalidURL])
@pytest.mark.parametrize("event", ["submitted", "completed", "failed"])
def test_transport_failure_is_best_effort_without_logging_details(job, caplog, error, event):
    requests = []

    def handle(request):
        requests.append(request)
        raise error(WEBHOOK_URL)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        WeComNotifier(client, SecretStr(WEBHOOK_URL)).notify(
            job, event, application_name="测试应用", submitter="OpenAPI · Test key"
        )
    assert len(requests) == 1
    assert error.__name__ in caplog.text
    assert "test-robot-secret" not in caplog.text


def test_markdown_is_bounded_in_utf8_bytes(job):
    contents = []

    def handle(request):
        contents.append(json.loads(request.content)["markdown"]["content"])
        return httpx.Response(200, json={"errcode": 0})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        notifier = WeComNotifier(client, SecretStr(WEBHOOK_URL))
        notifier.notify(
            job, "submitted", application_name="长" * 2000, submitter="OpenAPI · Test key"
        )
    assert len(contents[0].encode("utf-8")) <= 4096
    assert "\ufffd" not in contents[0]


def test_markdown_values_stay_plain_text_without_links_or_extra_lines(job):
    contents = []
    job.filename = "应用_[链接](demo)-v1.2.3.zip"

    def handle(request):
        contents.append(json.loads(request.content)["markdown"]["content"])
        return httpx.Response(200, json={"errcode": 0})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        WeComNotifier(client, SecretStr(WEBHOOK_URL)).notify(
            job,
            "submitted",
            application_name="**Demo**\n额外内容",
            submitter="用户 · a_lice",
        )
    assert contents[0].splitlines() == [
        "**AppleRemoteSign · 新签名提交**",
        r"**文件名**: 应用\_\[链接\](demo)-v1.2.3.zip",
        r"**应用名称**: \*\*Demo\*\* 额外内容",
        f"**任务 ID**: {job.id}",
        r"**提交人**: 用户 · a\_lice",
    ]
