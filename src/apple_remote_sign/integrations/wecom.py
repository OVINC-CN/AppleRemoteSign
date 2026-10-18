"""Best-effort WeCom group robot notifications; webhook credentials stay out of logs."""

import logging
import re
from typing import Literal

import httpx
from opentelemetry.trace import SpanKind
from pydantic import SecretStr

from apple_remote_sign.domain import OperationError
from apple_remote_sign.observability import operation
from apple_remote_sign.persistence.models import Job

logger = logging.getLogger(__name__)
NotificationEvent = Literal["submitted", "completed", "failed"]


def markdown_text(value: str) -> str:
    return re.sub(r"([\\`*_\[\]<>])", r"\\\1", " ".join(value.splitlines()))


class WeComNotifier:
    def __init__(self, client: httpx.Client, webhook_url: SecretStr) -> None:
        self.client = client
        self.webhook_url = webhook_url

    def notify(
        self, job: Job, event: NotificationEvent, *, application_name: str, submitter: str
    ) -> None:
        title = {"submitted": "新签名提交", "completed": "签名成功", "failed": "签名失败"}[event]
        lines = [
            f"**AppleRemoteSign · {title}**",
            f"**文件名**: {markdown_text(job.filename)}",
            f"**应用名称**: {markdown_text(application_name)}",
            f"**任务 ID**: {markdown_text(job.id)}",
            f"**提交人**: {markdown_text(submitter)}",
        ]
        # WeCom Markdown messages allow at most 4096 UTF-8 bytes.
        content = "\n".join(lines).encode("utf-8")[:4096].decode("utf-8", errors="ignore")
        try:
            with operation("wecom.notify", kind=SpanKind.CLIENT, job_id=job.id, event=event):
                response = self.client.post(
                    self.webhook_url.get_secret_value(),
                    json={"msgtype": "markdown", "markdown": {"content": content}},
                    follow_redirects=False,
                )
                response.raise_for_status()
                result = response.json()
                code = result.get("errcode") if isinstance(result, dict) else None
                if type(code) is not int:
                    raise OperationError("invalid_response")
                if code != 0:
                    raise OperationError(f"errcode={code}")
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, OperationError) as exc:
            # Neither transport errors nor response bodies are safe to log verbatim.
            reason = str(exc) if isinstance(exc, OperationError) else type(exc).__name__
            logger.warning(
                "wecom.notification_failed\njob_id: %s\nevent: %s\nreason: %s",
                job.id,
                event,
                reason,
            )
            return
        logger.info("wecom.notification_sent\njob_id: %s\nevent: %s", job.id, event)
