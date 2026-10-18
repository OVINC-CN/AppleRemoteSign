import json
import os
import plistlib
import shlex
import shutil
import stat
import subprocess
import sys
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from tests.archive_helpers import dmg_zip, make_zip

SCRIPT = Path(__file__).resolve().parents[1] / "support-files/scripts/sign-and-notarize.sh"
HELPER = SCRIPT.with_name("archive-helper.py")
JOB_ID = "498bd264-2807-48c9-ad28-ccfe491b0ce6"
ARTIFACT = b"signed and stapled artifact"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_bytes(self, code, data, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def respond(self, code=200, status="processing", error="", identifier=JOB_ID):
        self.send_bytes(
            code,
            json.dumps(
                {
                    "data": {"id": identifier, "status": status, "submission_id": JOB_ID},
                    "error_message": error,
                }
            ).encode(),
        )

    def authorized(self):
        if self.headers.get("Authorization") != "Bearer test-token":
            self.respond(401, error="Invalid token")
            return False
        return True

    def transient_failure(self, stage, server=None):
        failures = (server or self.server).transient_failures.get(stage, [])
        if not failures:
            return False
        failure = failures.pop(0)
        if failure == "disconnect":
            self.close_connection = True
        elif failure == "truncated":
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"partial")
            self.close_connection = True
        else:
            self.send_bytes(failure, b"Temporary failure")
        return True

    def do_POST(self):
        self.server.counters["post"] += 1
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + body
        )
        part = next(message.iter_parts())
        self.server.upload_name = part.get_filename()
        self.server.upload = part.get_payload(decode=True)
        self.server.uploads.append(self.server.upload)
        if not self.authorized():
            return
        if self.transient_failure("post"):
            return
        if self.server.scenario == "submit_failure":
            self.respond(500, "failed", "Signing failed")
        else:
            self.respond()

    def do_GET(self):
        if self.path.startswith("/helpers/"):
            self.download_helper()
            return
        if not self.authorized():
            return
        if self.path.endswith("/artifact"):
            self.download()
            return
        self.server.counters["query"] += 1
        if self.transient_failure("query"):
            return
        self.query()

    def download_helper(self):
        self.server.helper_requests.append((self.path, self.headers.get("Authorization")))
        if self.transient_failure("helper"):
            return
        if self.path == "/helpers/redirect":
            self.send_response(302)
            self.send_header("Location", "/helpers/archive-helper.py")
            self.end_headers()
        elif self.path == "/helpers/missing":
            self.send_bytes(404, b"Helper not found")
        elif self.path == "/helpers/empty":
            self.send_bytes(200, b"")
        elif self.path == "/helpers/truncated":
            data = HELPER.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data) + 1))
            self.end_headers()
            self.wfile.write(data)
            self.close_connection = True
        else:
            self.send_bytes(200, HELPER.read_bytes(), "text/plain")

    def query(self):
        scenario = self.server.scenario
        if scenario == "job_failure":
            self.respond(500, "failed", "Apple notarization Invalid")
        elif scenario == "inconsistent_failure":
            self.respond(200, "failed", "Server incorrectly returned HTTP 200")
        elif scenario == "malformed":
            self.send_bytes(200, b"not JSON")
        elif scenario == "unknown_status":
            self.respond(status="surprise")
        elif scenario == "wrong_id":
            self.respond(identifier="00000000-0000-0000-0000-000000000000")
        elif scenario == "completed":
            self.respond(status="completed")
        else:
            self.respond(status="processing" if self.server.counters["query"] < 3 else "completed")

    def download(self):
        self.server.counters["download"] += 1
        if self.transient_failure("download"):
            return
        if self.server.scenario == "download_failure":
            self.respond(500, "failed", "Artifact unavailable")
        elif self.server.scenario == "download_no_redirect":
            self.send_bytes(200, b"unexpected file")
        else:
            self.send_response(302)
            self.send_header(
                "Location", self.server.cdn_url + "/cdn/artifact?eo_sign=123-random-0-digest"
            )
            self.send_header("Content-Length", "0")
            self.end_headers()


class CdnHandler(Handler):
    def do_GET(self):
        api = self.server.api
        api.cdn_requests.append((self.path, self.headers.get("Authorization")))
        if self.transient_failure("cdn", api):
            return
        scenario = api.scenario
        if scenario in {"cdn_forbidden", "cdn_missing"}:
            self.send_bytes(
                403 if scenario == "cdn_forbidden" else 404, b"CDN denied", "text/plain"
            )
        elif scenario == "cdn_redirect" and self.path.startswith("/cdn/artifact"):
            self.send_response(302)
            self.send_header("Location", "/cdn/final?eo_sign=123-random-0-digest")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif scenario == "truncated_download":
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"partial")
            self.close_connection = True
        else:
            if api.before_download:
                api.before_download()
            artifact = api.artifact_override
            if artifact is None:
                artifact = signed_archive(api.upload)
            self.send_bytes(200, artifact, "application/octet-stream")


def signed_archive(upload):
    output = BytesIO()
    with ZipFile(BytesIO(upload)) as original, ZipFile(output, "w") as signed:
        for entry in original.infolist():
            if entry.filename.startswith("__MACOSX/"):
                continue
            data = original.read(entry)
            if entry.filename.lower().endswith(".dmg") or entry.filename.endswith("/MacOS/Example"):
                data = ARTIFACT
            signed.writestr(entry, data)
    return output.getvalue()


class MockServer(ThreadingHTTPServer):
    def __init__(self, scenario):
        super().__init__(("127.0.0.1", 0), Handler)
        self.scenario = scenario
        self.transient_failures = {}
        self.counters = {"post": 0, "query": 0, "download": 0}
        self.helper_requests = []
        self.upload_name = None
        self.upload = None
        self.uploads = []
        self.artifact_override = None
        self.before_download = None
        self.cdn_requests = []
        self.cdn_url = ""


@pytest.fixture
def api_server():
    servers = []

    def start(scenario):
        server = MockServer(scenario)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        cdn = ThreadingHTTPServer(("127.0.0.1", 0), CdnHandler)
        cdn.api = server
        cdn_thread = threading.Thread(target=cdn.serve_forever, daemon=True)
        cdn_thread.start()
        servers.append((cdn, cdn_thread))
        server.cdn_url = f"http://localhost:{cdn.server_port}"
        return f"http://127.0.0.1:{server.server_port}/openapi/v1", server

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join()


def run_script(
    tmp_path,
    url,
    *,
    input_name="input file,with;punctuation-v1.2.3.dmg",
    input_content=None,
    output_name=None,
    script=SCRIPT,
    system=None,
    **overrides,
):
    input_file = tmp_path / input_name
    if not os.path.lexists(input_file):
        if input_name.lower().endswith(".app") and input_content is None:
            create_app(input_file)
        else:
            input_file.write_bytes(b"unsigned" if input_content is None else input_content)
    extension = ".app" if input_name.lower().endswith(".app") else ".dmg"
    output_file = tmp_path / (output_name or f"output{extension}")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_sleep = bin_dir / "sleep"
    fake_sleep.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$SLEEP_LOG"\n')
    fake_sleep.chmod(0o755)
    if system is not None or sys.platform != "darwin":
        # Exercise the client workflow on Linux CI while testing the platform guard separately.
        fake_uname = bin_dir / "uname"
        fake_uname.write_text(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(system or 'Darwin')}\n")
        fake_uname.chmod(0o755)
    if shutil.which("ditto") is None and not (bin_dir / "ditto").exists():
        # CI runs on Linux; macOS exercises the actual ditto binary.
        fake_ditto = bin_dir / "ditto"
        helper = Path(__file__).with_name("fake_ditto.py")
        fake_ditto.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(helper))} "$@"\n'
        )
        fake_ditto.chmod(0o755)
    environment = {
        **os.environ,
        "API_BASE_URL": url,
        "API_TOKEN": "test-token",
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SLEEP_LOG": str(tmp_path / "sleeps"),
    }
    for name in ("POLL_INTERVAL", "RESULT_HELPER", "RESULT_HELPER_URL"):
        environment.pop(name, None)
    environment.update(overrides)
    result = subprocess.run(
        ["bash", str(script), str(input_file), str(output_file)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=15,
    )
    return result, output_file


def create_app(path):
    executable = path / "Contents/MacOS/Example"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"unsigned")
    executable.chmod(0o755)
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Example"}))
    framework = path / "Contents/Frameworks/Test.framework"
    resources = framework / "Versions/A/Resources"
    resources.mkdir(parents=True)
    (resources / "data").write_bytes(b"keep resource")
    (framework / "Versions/Current").symlink_to("A")
    (framework / "Resources").symlink_to("Versions/Current/Resources")


def assert_no_staging(tmp_path):
    assert not list(tmp_path.glob(".apple-remote-sign.*"))


@pytest.mark.parametrize("system", ["Linux", "FreeBSD", "MINGW64_NT-10.0"])
def test_non_macos_is_rejected_before_requests(tmp_path, api_server, system):
    url, server = api_server("success")
    result, output = run_script(tmp_path, url, system=system, API_TOKEN="")
    assert result.returncode != 0
    assert "only supports macOS" in result.stderr
    assert server.counters == {"post": 0, "query": 0, "download": 0}
    assert not output.exists()
    assert_no_staging(tmp_path)


def test_missing_helper_without_url_stops_before_upload(tmp_path, api_server):
    url, server = api_server("success")
    script = shutil.copyfile(SCRIPT, tmp_path / SCRIPT.name)
    result, output = run_script(tmp_path, url, script=script)
    assert result.returncode != 0
    assert "Required script is missing" in result.stderr
    assert "RESULT_HELPER_URL" in result.stderr
    assert server.counters == {"post": 0, "query": 0, "download": 0}
    assert not output.exists()
    assert_no_staging(tmp_path)


def test_configured_local_helper_takes_precedence_over_url(tmp_path, api_server):
    url, server = api_server("success")
    script = shutil.copyfile(SCRIPT, tmp_path / SCRIPT.name)
    local_helper = tmp_path / "local helper.py"
    shutil.copyfile(HELPER, local_helper)
    result, output = run_script(
        tmp_path,
        url,
        script=script,
        RESULT_HELPER=str(local_helper),
        RESULT_HELPER_URL=f"{url.removesuffix('/openapi/v1')}/helpers/missing",
    )
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == ARTIFACT
    assert server.helper_requests == []
    assert local_helper.read_bytes() == HELPER.read_bytes()
    assert_no_staging(tmp_path)


@pytest.mark.parametrize("helper_path", ["archive-helper.py", "redirect"])
def test_missing_helper_is_downloaded_to_temporary_directory(tmp_path, api_server, helper_path):
    url, server = api_server("success")
    script = shutil.copyfile(SCRIPT, tmp_path / SCRIPT.name)
    staged_helpers = []
    server.before_download = lambda: staged_helpers.extend(
        tmp_path.glob(".apple-remote-sign.*/archive-helper.py")
    )
    result, output = run_script(
        tmp_path,
        url,
        script=script,
        RESULT_HELPER_URL=f"{url.removesuffix('/openapi/v1')}/helpers/{helper_path}",
    )
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == ARTIFACT
    expected_paths = ["/helpers/archive-helper.py"]
    if helper_path == "redirect":
        expected_paths.insert(0, "/helpers/redirect")
    assert server.helper_requests == [(path, None) for path in expected_paths]
    assert len(staged_helpers) == 1
    assert not staged_helpers[0].exists()
    assert not (tmp_path / HELPER.name).exists()
    assert_no_staging(tmp_path)


@pytest.mark.parametrize("helper_path", ["missing", "empty", "truncated"])
def test_helper_download_failure_cleans_up_before_upload(tmp_path, api_server, helper_path):
    url, server = api_server("success")
    missing_helper = tmp_path / "missing helper.py"
    result, output = run_script(
        tmp_path,
        url,
        RESULT_HELPER=str(missing_helper),
        RESULT_HELPER_URL=f"{url.removesuffix('/openapi/v1')}/helpers/{helper_path}",
    )
    assert result.returncode != 0
    expected = (
        "Downloaded helper is empty" if helper_path == "empty" else "Could not download helper"
    )
    assert expected in result.stderr
    attempts = 1 if helper_path == "empty" else 4
    assert server.helper_requests == [(f"/helpers/{helper_path}", None)] * attempts
    assert server.counters == {"post": 0, "query": 0, "download": 0}
    assert not output.exists()
    assert not missing_helper.exists()
    assert_no_staging(tmp_path)


@pytest.mark.parametrize(
    "input_name",
    [
        "input file,with;punctuation-1.2.3.dmg",
        "input file,with;punctuation-v1.2.3-alpha.1.dmg",
        "input file,with;punctuation-v1.2.3-beta.2.dmg",
        "input file,with;punctuation-v12.34.567-nightly+build.9.dmg",
    ],
)
def test_success_packages_dmg_then_extracts_with_default_five_second_polling(
    tmp_path, api_server, input_name
):
    url, server = api_server("success")
    result, output = run_script(tmp_path, url, input_name=input_name)
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == ARTIFACT
    assert server.counters == {"post": 1, "query": 3, "download": 1}
    assert server.upload_name == f"{input_name}.zip"
    with ZipFile(BytesIO(server.upload)) as uploaded:
        assert uploaded.read(input_name) == b"unsigned"
    assert (tmp_path / input_name).read_bytes() == b"unsigned"
    assert (tmp_path / "sleeps").read_text().splitlines() == ["5", "5"]
    assert_no_staging(tmp_path)


@pytest.mark.parametrize("scenario, requests", [("success", 1), ("cdn_redirect", 2)])
def test_cdn_download_redirects_never_receive_api_bearer(tmp_path, api_server, scenario, requests):
    url, server = api_server(scenario)
    result, output = run_script(tmp_path, url)
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == ARTIFACT
    assert len(server.cdn_requests) == requests
    assert all("eo_sign=" in path and token is None for path, token in server.cdn_requests)


@pytest.mark.parametrize("stage", ["helper", "post", "query", "download", "cdn"])
@pytest.mark.parametrize("failure", [503, "disconnect", "truncated"])
def test_transient_http_failures_are_retried(tmp_path, api_server, stage, failure):
    url, server = api_server("completed")
    server.transient_failures[stage] = [failure]
    script = shutil.copyfile(SCRIPT, tmp_path / SCRIPT.name)
    result, output = run_script(
        tmp_path,
        url,
        script=script,
        RESULT_HELPER_URL=f"{url.removesuffix('/openapi/v1')}/helpers/archive-helper.py",
    )
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == ARTIFACT
    assert server.counters == {
        "post": 2 if stage == "post" else 1,
        "query": 2 if stage == "query" else 1,
        "download": 2 if stage == "download" else 1,
    }
    assert all(upload == server.upload for upload in server.uploads)
    assert len(server.helper_requests) == (2 if stage == "helper" else 1)
    assert len(server.cdn_requests) == (2 if stage == "cdn" else 1)
    assert all(token is None for _, token in server.helper_requests + server.cdn_requests)
    assert_no_staging(tmp_path)


@pytest.mark.parametrize("failure", [503, "disconnect", "truncated"])
def test_job_creation_stops_after_three_retries(tmp_path, api_server, failure):
    url, server = api_server("completed")
    server.transient_failures["post"] = [failure] * 4
    result, output = run_script(tmp_path, url)
    assert result.returncode != 0
    assert server.counters == {"post": 4, "query": 0, "download": 0}
    assert all(upload == server.upload for upload in server.uploads)
    assert not output.exists()
    assert_no_staging(tmp_path)


def test_query_transfer_failure_stops_after_three_retries(tmp_path, api_server):
    url, server = api_server("completed")
    server.transient_failures["query"] = ["disconnect"] * 4
    result, output = run_script(tmp_path, url)
    assert result.returncode != 0
    assert "Network/transfer error" in result.stderr
    assert JOB_ID in result.stderr
    assert server.counters == {"post": 1, "query": 4, "download": 0}
    assert not output.exists()
    assert_no_staging(tmp_path)


def test_app_round_trip_preserves_executable_permissions_links_and_original(tmp_path, api_server):
    url, server = api_server("success")
    name = '测试应用 "quoted",semicolon;-v1.2.3-rc.1.app'
    result, output = run_script(tmp_path, url, input_name=name)
    assert result.returncode == 0, result.stderr
    assert output.is_dir()
    assert (output / "Contents/MacOS/Example").read_bytes() == ARTIFACT
    assert stat.S_IMODE((output / "Contents/MacOS/Example").stat().st_mode) == 0o755
    resources = output / "Contents/Frameworks/Test.framework/Resources"
    assert resources.is_symlink()
    assert os.readlink(resources) == "Versions/Current/Resources"
    assert (resources / "data").read_bytes() == b"keep resource"
    assert (tmp_path / name / "Contents/MacOS/Example").read_bytes() == b"unsigned"
    # Curl can escape quotes in the multipart filename; ZIP member names must be exact.
    assert server.upload_name.endswith(".app.zip")
    with ZipFile(BytesIO(server.upload)) as uploaded:
        assert uploaded.read(f"{name}/Contents/MacOS/Example") == b"unsigned"
    assert_no_staging(tmp_path)


@pytest.mark.parametrize(
    "scenario, expected",
    [
        ("submit_failure", "Signing failed"),
        ("job_failure", "Apple notarization Invalid"),
        ("inconsistent_failure", "Server incorrectly returned HTTP 200"),
        ("malformed", "Invalid JSON"),
        ("unknown_status", "Unrecognized task status"),
        ("wrong_id", "job ID changed"),
        ("download_failure", "Artifact unavailable"),
        ("download_no_redirect", "Expected HTTP 302"),
        ("cdn_forbidden", "HTTP 403"),
        ("cdn_missing", "HTTP 404"),
        ("truncated_download", "Network/transfer error"),
    ],
)
def test_failures_exit_without_partial_artifact(tmp_path, api_server, scenario, expected):
    url, server = api_server(scenario)
    result, output = run_script(tmp_path, url, POLL_INTERVAL="0.01")
    assert result.returncode != 0
    assert expected in result.stderr
    assert JOB_ID in result.stderr
    assert server.counters["post"] == (4 if scenario == "submit_failure" else 1)
    if scenario == "job_failure":
        assert server.counters["query"] == 4
    elif scenario == "download_failure":
        assert server.counters["download"] == 4
    elif scenario == "truncated_download":
        assert len(server.cdn_requests) == 4
    elif scenario in {"cdn_forbidden", "cdn_missing"}:
        assert len(server.cdn_requests) == 1
    assert not output.exists()
    assert_no_staging(tmp_path)


def test_authentication_failure_stops_without_query(tmp_path, api_server):
    url, server = api_server("success")
    result, output = run_script(tmp_path, url, API_TOKEN="wrong-token")
    assert result.returncode != 0
    assert "HTTP 401" in result.stderr
    assert server.counters["query"] == 0
    assert not output.exists()


def test_existing_output_is_not_overwritten(tmp_path, api_server):
    output = tmp_path / "output.dmg"
    output.write_bytes(b"keep me")
    url, server = api_server("success")
    result, _ = run_script(tmp_path, url)
    assert result.returncode != 0
    assert output.read_bytes() == b"keep me"
    assert server.counters["post"] == 0


@pytest.mark.parametrize(
    "filename, content, message",
    [
        ("input.zip", dmg_zip(), "Only .app directories or .dmg files"),
        ("input.pkg", b"installer", "Only .app directories or .dmg files"),
        ("empty.dmg", b"", "readable, nonempty regular file"),
        ("fake.app", b"not a directory", "App input must be a readable directory"),
    ],
)
def test_invalid_upload_format_is_rejected_before_request(
    tmp_path, api_server, filename, content, message
):
    url, server = api_server("success")
    result, output = run_script(tmp_path, url, input_name=filename, input_content=content)
    assert result.returncode != 0
    assert message in result.stderr
    assert server.counters == {"post": 0, "query": 0, "download": 0}
    assert not output.exists()


@pytest.mark.parametrize(
    "input_name", ["source.app", "source.dmg", "source-v1.2.dmg", "source-v1x2x3.app"]
)
def test_missing_version_is_rejected_before_packaging(tmp_path, api_server, input_name):
    # A version in the parent path must not satisfy the filename rule.
    directory = tmp_path / "release-1.2.3"
    directory.mkdir()
    url, server = api_server("success")
    result, output = run_script(directory, url, input_name=input_name)
    assert result.returncode != 0
    assert "version number (X.Y.Z)" in result.stderr
    assert "Packaging:" not in result.stderr
    assert server.counters == {"post": 0, "query": 0, "download": 0}
    assert not output.exists()
    assert_no_staging(directory)


@pytest.mark.parametrize("input_name", ["source-v1.2.3.app", "source-v1.2.3.dmg"])
def test_output_extension_must_match_input(tmp_path, api_server, input_name):
    url, server = api_server("success")
    result, output = run_script(tmp_path, url, input_name=input_name, output_name="result.zip")
    assert result.returncode != 0
    assert "output path must end" in result.stderr
    assert server.counters["post"] == 0
    assert not output.exists()
    assert_no_staging(tmp_path)


def test_output_cannot_be_inside_original_app(tmp_path, api_server):
    url, server = api_server("success")
    result, output = run_script(
        tmp_path,
        url,
        input_name="source-v1.2.3.app",
        output_name="source-v1.2.3.app/Contents/result.app",
    )
    assert result.returncode != 0
    assert "outside the input App" in result.stderr
    assert server.counters["post"] == 0
    assert not output.exists()
    assert (tmp_path / "source-v1.2.3.app/Contents/MacOS/Example").read_bytes() == b"unsigned"


@pytest.mark.parametrize("extension", ["app", "dmg"])
def test_target_created_during_download_is_never_replaced_or_merged(
    tmp_path, api_server, extension
):
    url, server = api_server("success")
    output = tmp_path / f"output.{extension}"

    def create_target():
        if extension == "app":
            output.mkdir()
        else:
            output.write_bytes(b"keep me")

    server.before_download = create_target
    result, _ = run_script(tmp_path, url, input_name=f"source-v1.2.3.{extension}")
    assert result.returncode != 0
    assert "File exists" in result.stderr
    assert JOB_ID in result.stderr
    if extension == "app":
        assert list(output.iterdir()) == []
    else:
        assert output.read_bytes() == b"keep me"
    assert_no_staging(tmp_path)


@pytest.mark.parametrize(
    "artifact",
    [
        b'{"error": "unexpected JSON with HTTP 200"}',
        dmg_zip(filename="wrong-name.dmg"),
        make_zip([("../escaped", b"bad", stat.S_IFREG | 0o644)]),
    ],
)
def test_bad_download_is_rejected_before_unpacking(tmp_path, api_server, artifact):
    url, server = api_server("success")
    server.artifact_override = artifact
    result, output = run_script(tmp_path, url)
    assert result.returncode != 0
    assert "Could not unpack or publish" in result.stderr
    assert JOB_ID in result.stderr
    assert not output.exists()
    assert not list(tmp_path.rglob("escaped"))
    assert_no_staging(tmp_path)


def test_failed_extraction_cleans_partial_files(tmp_path, api_server):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ditto = bin_dir / "ditto"
    ditto.write_text(
        "#!/bin/sh\nfor destination; do :; done\n"
        'mkdir -p "$destination"\nprintf partial > "$destination/partial"\nexit 1\n'
    )
    ditto.chmod(0o755)
    url, server = api_server("success")
    result, output = run_script(tmp_path, url)
    assert result.returncode != 0
    assert "Could not unpack or publish" in result.stderr
    assert server.counters["download"] == 1
    assert not output.exists()
    assert_no_staging(tmp_path)


def test_failed_packaging_does_not_upload_or_leave_files(tmp_path, api_server):
    app = tmp_path / "source-v1.2.3.app"
    create_app(app)
    os.mkfifo(app / "Contents/pipe")
    url, server = api_server("success")
    result, output = run_script(tmp_path, url, input_name=app.name)
    assert result.returncode != 0
    assert "Could not package input as ZIP" in result.stderr
    assert server.counters["post"] == 0
    assert not output.exists()
    assert_no_staging(tmp_path)


def test_unreadable_app_directory_is_not_silently_omitted(tmp_path, api_server):
    app = tmp_path / "source-v1.2.3.app"
    create_app(app)
    resources = app / "Contents/Resources"
    resources.mkdir()
    (resources / "data").write_bytes(b"required resource")
    resources.chmod(0o000)
    try:
        if os.access(resources, os.R_OK):
            pytest.skip("This user can read directories regardless of file permissions.")
        url, server = api_server("success")
        result, output = run_script(tmp_path, url, input_name=app.name)
        assert result.returncode != 0
        assert "Could not package input as ZIP" in result.stderr
        assert server.counters["post"] == 0
        assert not output.exists()
        assert_no_staging(tmp_path)
    finally:
        resources.chmod(0o755)
