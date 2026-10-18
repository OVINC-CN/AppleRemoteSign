import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import UUID

from apple_remote_sign.config import Settings
from apple_remote_sign.integrations.process import CommandError, CommandRunner

SUBMISSION = re.compile(r"\bcreated submission ID:\s*([0-9a-fA-F-]{36})\b")


class SigningTool(Protocol):
    def sign(self, path: Path) -> str: ...

    def submit(self, path: Path, on_submission: Callable[[str], None]) -> str: ...

    def staple(self, path: Path) -> str: ...


class Rcodesign:
    def __init__(self, settings: Settings, runner: CommandRunner) -> None:
        self.settings = settings
        self.runner = runner
        self.api_key_file = settings.apple_api_key_file

    def _command(self, operation: str) -> list[str]:
        config = self.settings.rcodesign_config_file if operation == "sign" else None
        return [
            str(self.settings.rcodesign_bin),
            "--config-file",
            str(config or "/dev/null"),
            operation,
        ]

    def sign(self, path: Path) -> str:
        args = [
            *self._command("sign"),
            "--for-notarization",
            "--p12-file",
            str(self.settings.signing_certificate_file),
            "--p12-password-file",
            str(self.settings.signing_password_file),
            str(path),
        ]
        return self.runner.run(args, self.settings.command_timeout_seconds)

    def submit(self, path: Path, on_submission: Callable[[str], None]) -> str:
        found: str | None = None

        def observe(line: str) -> None:
            nonlocal found
            match = SUBMISSION.search(line)
            if match is None:
                return
            identifier = str(UUID(match.group(1)))
            if found is not None and found != identifier:
                raise CommandError("rcodesign reported multiple submission IDs.")
            if found is None:
                found = identifier
                on_submission(identifier)

        args = [
            *self._command("notary-submit"),
            "--api-key-file",
            str(self.api_key_file),
            str(path),
        ]
        output = self.runner.run(args, self.settings.command_timeout_seconds, observe)
        if found is None:
            raise CommandError("rcodesign finished without reporting a submission ID.", output)
        return output

    def staple(self, path: Path) -> str:
        return self.runner.run(
            [*self._command("staple"), str(path)], self.settings.staple_timeout_seconds
        )
