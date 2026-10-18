import plistlib
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from apple_remote_sign.domain import OperationError
from apple_remote_sign.integrations.process import CommandError
from apple_remote_sign.integrations.s3 import artifact_key

TEST_APPLICATION_ID = "00000000-0000-4000-8000-000000000001"


class FakeArtifacts:
    def __init__(self, prefix: str = "app-dist/") -> None:
        self.prefix = prefix
        self.objects: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.lookups: list[str] = []
        self.error: str | None = None
        self.before_upload: Callable[[], None] | None = None

    def key_for(self, directory: str, filename: str) -> str:
        return artifact_key(self.prefix, directory, filename)

    def exists(self, key: str) -> bool:
        self.lookups.append(key)
        if self.error:
            raise OperationError(self.error)
        return key in self.objects

    def upload(self, path: Path, key: str, filename: str, sha256: str) -> None:
        self.calls.append(key)
        if self.before_upload:
            self.before_upload()
        if self.error:
            raise OperationError(self.error)
        self.objects[key] = path.read_bytes()


class FakeTool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path]] = []
        self.fail_sign = False
        self.fail_submit = False
        self.fail_staple = False
        self.staple_delay = 0.0

    def sign(self, path: Path) -> str:
        self.calls.append(("sign", path))
        if self.fail_sign:
            raise CommandError("Signing failed", "invalid signature input")
        self._append(path, b"|signed")
        return "signed"

    def submit(self, path: Path, on_submission: Callable[[str], None]) -> str:
        self.calls.append(("submit", path))
        on_submission(str(uuid4()))
        if self.fail_submit:
            raise CommandError("Upload interrupted", "S3 transfer failed")
        return "submitted"

    def staple(self, path: Path) -> str:
        self.calls.append(("staple", path))
        time.sleep(self.staple_delay)
        self._append(path, b"|stapled")
        if self.fail_staple:
            raise CommandError("Stapling failed", "ticket not available yet")
        return "stapled"

    @staticmethod
    def _append(path: Path, marker: bytes) -> None:
        if path.is_dir():
            info = plistlib.loads((path / "Contents/Info.plist").read_bytes())
            path = path / "Contents/MacOS" / info["CFBundleExecutable"]
        path.write_bytes(path.read_bytes() + marker)


class FakeApple:
    def __init__(self) -> None:
        self.current = "In Progress"
        self.calls: list[str] = []
        self.logs: list[str] = []
        self.error: str | None = None
        self.log_error = False
        self.log_data: dict[str, object] | None = None

    def status(self, submission_id: str) -> str:
        self.calls.append(submission_id)
        if self.error:
            raise OperationError(self.error)
        return self.current

    def log(self, submission_id: str) -> dict[str, object]:
        self.logs.append(submission_id)
        if self.log_error:
            raise OperationError("Log unavailable")
        if self.log_data is not None:
            return self.log_data
        if self.current == "Accepted":
            return {
                "archiveFilename": "Test-v1.2.3.zip",
                "jobId": submission_id,
                "logFormatVersion": 1,
                "sha256": "a" * 64,
                "status": "Accepted",
                "statusCode": 0,
                "statusSummary": "Ready for distribution",
                "uploadDate": "2026-10-15T09:46:43.002Z",
                "issues": None,
                "ticketContents": [],
            }
        return {
            "statusSummary": "Package Invalid",
            "issues": [{"message": "The executable does not have the hardened runtime enabled."}],
        }
