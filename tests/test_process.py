import os
import sys
import time
from pathlib import Path

import pytest

from apple_remote_sign.domain import CoordinationError
from apple_remote_sign.integrations.process import CommandError, CommandRunner
from apple_remote_sign.integrations.rcodesign import Rcodesign
from apple_remote_sign.locks import JobLocks
from apple_remote_sign.redaction import Redactor

IDENTIFIER = "498bd264-2807-48c9-ad28-ccfe491b0ce6"


def test_streaming_callback_runs_before_command_exit(tmp_path):
    runner = CommandRunner(tmp_path, Redactor(["my-secret"]))
    observed = []
    output = runner.run(
        [sys.executable, "-u", "-c", "import time; print('my-secret'); time.sleep(.1)"],
        5,
        lambda line: observed.append((line, time.monotonic())),
    )
    assert observed[0][0] == "my-secret"
    assert time.monotonic() - observed[0][1] >= 0.08
    assert "my-secret" not in output
    assert "[REDACTED]" in output


def test_timeout_terminates_process_group_and_captures_output(tmp_path):
    runner = CommandRunner(tmp_path, Redactor())
    code = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print('partial output', flush=True)"
    )
    start = time.monotonic()
    with pytest.raises(CommandError, match="timed out") as raised:
        runner.run([sys.executable, "-c", code], 0.4)
    assert "partial output" in raised.value.output
    assert time.monotonic() - start < 3


def test_command_nonzero_is_not_success_and_tail_is_bounded(tmp_path):
    runner = CommandRunner(tmp_path, Redactor())
    code = "import sys; print('x' * 100000); print('last line'); sys.exit(7)"
    with pytest.raises(CommandError, match="code 7") as raised:
        runner.run([sys.executable, "-c", code], 5)
    assert len(raised.value.output) <= 32768
    assert "last line" in raised.value.output


def test_application_secrets_are_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "secret-api-token")
    runner = CommandRunner(tmp_path, Redactor())
    output = runner.run(
        [sys.executable, "-c", "import os; print(os.environ.get('API_TOKEN', 'absent'))"], 5
    )
    assert output.strip() == "absent"


def test_submission_id_saved_before_failing_upload(settings, tmp_path):
    executable = tmp_path / "fake-rcodesign"
    executable.write_text(
        f"#!{sys.executable}\nimport sys, time\n"
        f"print('created submission ID: {IDENTIFIER}', file=sys.stderr, flush=True)\n"
        "time.sleep(.05)\nsys.exit(1)\n"
    )
    executable.chmod(0o755)
    settings.rcodesign_bin = executable
    tool = Rcodesign(settings, CommandRunner(tmp_path, Redactor()))
    identifiers = []
    with pytest.raises(CommandError):
        tool.submit(tmp_path / "sample.dmg", identifiers.append)
    assert identifiers == [IDENTIFIER]


def test_exact_command_contract_without_polling_flags(settings):
    class Runner:
        def __init__(self):
            self.commands = []

        def run(self, args, timeout, on_line=None):
            self.commands.append(args)
            if on_line:
                on_line(f"WARN created submission ID: {IDENTIFIER}")
            return "ok"

    runner = Runner()
    settings.apple_api_key_file = Path("/tmp/custom credentials.json")
    settings.rcodesign_config_file = Path("/run/secrets/signing.toml")
    tool = Rcodesign(settings, runner)
    path = Path("/data/file with spaces; $(echo nope).dmg")
    tool.sign(path)
    tool.submit(path, lambda _identifier: None)
    tool.staple(path)
    assert all(command[-1] == str(path) for command in runner.commands)
    assert "--for-notarization" in runner.commands[0]
    assert "notary-submit" in runner.commands[1]
    assert runner.commands[1][4:6] == ["--api-key-file", str(settings.apple_api_key_file)]
    assert "--api-key" not in runner.commands[1]
    assert "--api-issuer" not in runner.commands[1]
    assert "--wait" not in runner.commands[1]
    assert "--staple" not in runner.commands[1]
    assert "notary-wait" not in str(runner.commands)
    assert runner.commands[0][2] == "/run/secrets/signing.toml"
    assert all(command[2] == "/dev/null" for command in runner.commands[1:])


def test_signing_does_not_load_implicit_configuration(settings):
    class Runner:
        def run(self, args, _timeout):
            assert args[:4] == [str(settings.rcodesign_bin), "--config-file", "/dev/null", "sign"]
            assert "--for-notarization" in args
            return "ok"

    assert Rcodesign(settings, Runner()).sign(Path("/tmp/example.app")) == "ok"


def test_missing_submission_id_is_error(settings):
    class Runner:
        def run(self, *_args):
            return f"unrelated UUID: {IDENTIFIER}"

    with pytest.raises(CommandError, match="without reporting"):
        Rcodesign(settings, Runner()).submit(Path("/tmp/file"), lambda _: None)


def test_failed_callback_always_stops_child(tmp_path):
    runner = CommandRunner(tmp_path, Redactor())

    def broken_callback(_line):
        raise RuntimeError("database write failed")

    with pytest.raises(RuntimeError, match="database write failed"):
        runner.run(
            [sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(60)"],
            5,
            broken_callback,
        )


@pytest.mark.parametrize("close_output", [False, True])
def test_lost_lease_terminates_the_running_process_group(
    tmp_path, redis, monkeypatch, close_output
):
    runner = CommandRunner(tmp_path, Redactor())
    locks = JobLocks(redis, ttl_seconds=1, renew_interval_seconds=0.01)
    stopped = []
    original_stop = runner._stop

    def stop(process):
        original_stop(process)
        stopped.append(process.returncode)

    monkeypatch.setattr(runner, "_stop", stop)
    code = "import os,time; print('started', flush=True); "
    if close_output:
        code += "os.close(1); os.close(2); "
    code += "time.sleep(60)"
    started = time.monotonic()
    with pytest.raises(CoordinationError), locks.hold("task"):
        runner.run(
            [sys.executable, "-u", "-c", code],
            5,
            lambda _line: redis.state.expire("task"),
        )
    assert stopped and stopped[0] is not None
    assert time.monotonic() - started < 3


def test_redacts_bearer_jwt_and_presigned_urls():
    cleaned = Redactor(["password-value"]).clean(
        "password-value Bearer abc123 https://s3.example/file?X-Amz-Credential=secret"
    )
    assert "password-value" not in cleaned
    assert "abc123" not in cleaned
    assert "X-Amz-Credential" not in cleaned


def test_failure_before_start_is_actionable(tmp_path):
    with pytest.raises(CommandError, match="Unable to start"):
        CommandRunner(tmp_path, Redactor()).run([os.fspath(tmp_path / "missing")], 1)
