import contextlib
import logging
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from apple_remote_sign.domain import OperationError
from apple_remote_sign.locks import ensure_ownership
from apple_remote_sign.observability import operation
from apple_remote_sign.redaction import ANSI, Redactor

type LineCallback = Callable[[str], None]
logger = logging.getLogger(__name__)


class CommandError(OperationError):
    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


class OutputCollector:
    def __init__(self, redactor: Redactor, callback: LineCallback | None) -> None:
        self.redactor = redactor
        self.callback = callback
        self.pending = b""
        self.output = ""

    def add(self, block: bytes) -> None:
        self.pending += block
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            self.deliver(line)
        if len(self.pending) > 16384:
            self.deliver(self.pending[:8192])
            self.pending = self.pending[8192:]

    def deliver(self, line: bytes) -> None:
        text = ANSI.sub("", line.decode("utf-8", errors="replace"))
        self.output = (self.output + self.redactor.clean(text) + "\n")[-32768:]
        if self.callback:
            self.callback(text)

    def finish(self) -> None:
        if self.pending:
            self.deliver(self.pending)
            self.pending = b""


class CommandRunner:
    def __init__(self, cwd: Path, redactor: Redactor) -> None:
        self.cwd = cwd
        self.redactor = redactor

    def run(
        self,
        args: Sequence[str],
        timeout: float,
        on_line: LineCallback | None = None,
    ) -> str:
        with operation("command.run", executable=Path(args[0]).name, timeout_seconds=timeout):
            return self._run(args, timeout, on_line)

    def _run(self, args: Sequence[str], timeout: float, on_line: LineCallback | None) -> str:
        collector = OutputCollector(self.redactor, on_line)
        ensure_ownership()
        try:
            process = subprocess.Popen(
                args,
                cwd=self.cwd,
                env=self._environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            logger.error("command.start_failed\nreason: %s", exc.strerror)
            raise CommandError(f"Unable to start rcodesign: {exc.strerror}") from None
        deadline = time.monotonic() + timeout
        try:
            self._read(process, collector, deadline)
            code = self._wait(process, deadline)
            ensure_ownership()
            if code:
                raise CommandError(f"rcodesign exited with code {code}.", collector.output)
            return collector.output
        except TimeoutError, subprocess.TimeoutExpired:
            logger.error("command.timed_out\npid: %d\ntimeout_seconds: %s", process.pid, timeout)
            raise CommandError(
                f"rcodesign timed out after {timeout:g}s.", collector.output
            ) from None
        finally:
            self._stop(process)
            if process.stdout:
                process.stdout.close()

    @staticmethod
    def _read(
        process: subprocess.Popen[bytes], collector: OutputCollector, deadline: float
    ) -> None:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                ensure_ownership(verify=False)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                if not selector.select(min(remaining, 0.2)):
                    continue
                block = os.read(process.stdout.fileno(), 4096)
                if not block:
                    collector.finish()
                    return
                collector.add(block)

    @staticmethod
    def _wait(process: subprocess.Popen[bytes], deadline: float) -> int:
        # A tool may close stdout/stderr before it exits. Keep observing lease
        # cancellation after EOF too, rather than blocking for the whole timeout.
        while True:
            ensure_ownership(verify=False)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            try:
                return process.wait(timeout=min(remaining, 0.2))
            except subprocess.TimeoutExpired:
                continue

    @staticmethod
    def _stop(process: subprocess.Popen[bytes]) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()

    @staticmethod
    def _environment() -> dict[str, str]:
        allowed = (
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        )
        return {name: os.environ[name] for name in allowed if name in os.environ}
