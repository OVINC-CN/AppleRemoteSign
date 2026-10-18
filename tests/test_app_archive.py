import os
import stat
import warnings
from io import BytesIO
from zipfile import ZipFile

import pytest

from apple_remote_sign.archives import SigningArchive
from apple_remote_sign.domain import OperationError
from tests.archive_helpers import APP, BINARY, RESOURCE, app_entries, make_zip


def test_archive_round_trip_preserves_bundle_contents_modes_and_framework_links(tmp_path):
    source = tmp_path / "source.zip"
    source.write_bytes(make_zip())
    archives = SigningArchive()
    bundle = archives.extract(source, tmp_path / "first")
    assert bundle.name == APP
    binary = bundle / "Contents/MacOS/example"
    assert stat.S_IMODE(binary.stat().st_mode) == 0o755
    framework = bundle / "Contents/Frameworks/Example.framework"
    assert (framework / "Resources/data").read_bytes() == b"framework resource"
    assert (framework / "Resources").is_symlink()
    assert os.readlink(framework / "Resources") == "Versions/Current/Resources"
    binary.write_bytes(binary.read_bytes() + b"|signed")
    packed = tmp_path / "result.zip"
    archives.pack(bundle, packed)
    second = archives.extract(packed, tmp_path / "second")
    assert (second / "Contents/MacOS/example").read_bytes().endswith(b"|signed")
    assert (second / "Contents/Frameworks/Example.framework/Resources").is_symlink()
    with ZipFile(packed) as archive:
        assert archive.testzip() is None
        assert archive.read(RESOURCE) == b"framework resource"
        assert stat.S_IMODE(archive.getinfo(BINARY).external_attr >> 16) == 0o755


@pytest.mark.parametrize(
    "name, content, mode, message",
    [
        ("../outside", b"bad", stat.S_IFREG | 0o644, "unsafe entry path"),
        ("/absolute", b"bad", stat.S_IFREG | 0o644, "unsafe entry path"),
        (f"{APP}/Contents/../../escape", b"bad", stat.S_IFREG | 0o644, "unsafe entry path"),
        (f"{APP}\\escape", b"bad", stat.S_IFREG | 0o644, "unsafe entry path"),
        (f"{APP}/drive:C", b"bad", stat.S_IFREG | 0o644, "unsafe entry path"),
        (f"{APP}/fifo", b"bad", stat.S_IFIFO | 0o644, "special file"),
        (f"{APP}/outside", b"../outside", stat.S_IFLNK | 0o777, "escapes"),
        (f"{APP}/absolute", b"/tmp", stat.S_IFLNK | 0o777, "relative paths"),
        (f"{APP}/loop", b"loop", stat.S_IFLNK | 0o777, "cyclic"),
        (f"{APP}/ancestor", b".", stat.S_IFLNK | 0o777, "ancestor"),
        (f"{APP}/missing", b"not-present", stat.S_IFLNK | 0o777, "dangling"),
        (f"{APP}/contents/info.plist", b"bad", stat.S_IFREG | 0o644, "collide"),
        ("Another.app/Contents/file", b"bad", stat.S_IFREG | 0o644, "one top-level"),
    ],
)
def test_invalid_archive_entries_are_rejected(name, content, mode, message):
    stream = BytesIO(make_zip([*app_entries(), (name, content, mode)]))
    with pytest.raises(OperationError, match=message):
        SigningArchive().validate_upload("app.zip", stream)
    assert stream.tell() == 0


def test_entries_cannot_write_through_symlinks_in_either_order():
    entries = app_entries()
    entries.append(
        (
            f"{APP}/Contents/Frameworks/Example.framework/Resources/injected",
            b"bad",
            stat.S_IFREG | 0o644,
        )
    )
    for sequence in (entries, list(reversed(entries))):
        with pytest.raises(OperationError, match="parent directory"):
            SigningArchive().validate_upload("app.zip", BytesIO(make_zip(sequence)))


def test_duplicate_entries_are_rejected():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        data = make_zip([*app_entries(), app_entries()[2]])
    with pytest.raises(OperationError, match="duplicate"):
        SigningArchive().validate_upload("app.zip", BytesIO(data))


@pytest.mark.parametrize(
    "limit, message",
    [
        (dict(max_extracted_bytes=10), "MAX_EXTRACTED_BYTES"),
        (dict(max_entries=2), "MAX_ARCHIVE_ENTRIES"),
    ],
)
def test_extraction_limits_are_enforced_before_writing(tmp_path, limit, message):
    with pytest.raises(OperationError, match=message):
        SigningArchive(**limit).validate_upload("app.zip", BytesIO(make_zip()))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "entries, message",
    [
        ([], "one top-level"),
        ([("Fake.app", b"not a directory", stat.S_IFREG | 0o644)], "must be a directory"),
        (app_entries()[2:], "Info.plist"),
        ([entry for entry in app_entries() if entry[0] != BINARY], "missing the executable"),
        (
            [
                (name, data, stat.S_IFREG | 0o644) if name == BINARY else (name, data, mode)
                for name, data, mode in app_entries()
            ],
            "execute permission",
        ),
        (
            [
                (name, b"<plist><broken", mode)
                if name.endswith("Info.plist")
                else (name, data, mode)
                for name, data, mode in app_entries()
            ],
            "invalid Info.plist",
        ),
    ],
)
def test_incomplete_or_invalid_app_is_rejected(entries, message):
    with pytest.raises(OperationError, match=message):
        SigningArchive().validate_upload("app.zip", BytesIO(make_zip(entries)))


def test_finder_metadata_does_not_count_as_another_app(tmp_path):
    entries = [
        *app_entries(),
        ("__MACOSX/._App.app", b"metadata", stat.S_IFREG | 0o644),
        (".DS_Store", b"finder", stat.S_IFREG | 0o644),
    ]
    source = tmp_path / "source.zip"
    source.write_bytes(make_zip(entries))
    SigningArchive().extract(source, tmp_path / "unpacked")
    assert [p.name for p in (tmp_path / "unpacked").iterdir()] == [APP]


def test_inline_appledouble_is_removed_without_dropping_ordinary_dot_files(tmp_path):
    metadata = b"\x00\x05\x16\x07\x00\x02\x00\x00" + b"\0" * 24
    source = tmp_path / "source.zip"
    source.write_bytes(
        make_zip(
            [
                *app_entries(),
                (f"{APP}/Contents/._Info.plist", metadata, stat.S_IFREG | 0o644),
                (f"{APP}/Contents/._ordinary", b"real application resource", stat.S_IFREG | 0o644),
            ]
        )
    )
    bundle = SigningArchive().extract(source, tmp_path / "unpacked")
    assert not (bundle / "Contents/._Info.plist").exists()
    assert (bundle / "Contents/._ordinary").read_bytes() == b"real application resource"


def test_dmg_appledouble_sidecar_is_not_an_extra_payload(tmp_path):
    source = tmp_path / "image.zip"
    source.write_bytes(
        make_zip(
            [
                ("image.dmg", b"image", stat.S_IFREG | 0o644),
                ("._image.dmg", b"\x00\x05\x16\x07\x00\x02\x00\x00", stat.S_IFREG | 0o644),
            ]
        )
    )
    image = SigningArchive().extract(source, tmp_path / "unpacked")
    assert image.read_bytes() == b"image"
    assert list(image.parent.iterdir()) == [image]


def test_failed_transform_keeps_original_zip_and_removes_workspace(tmp_path):
    source = tmp_path / "app.zip"
    original = make_zip()
    source.write_bytes(original)

    def fail(bundle):
        (bundle / "Contents/MacOS/example").write_bytes(b"partial modification")
        raise OperationError("signing failed")

    with pytest.raises(OperationError, match="signing failed"):
        SigningArchive().transform(source, fail)
    assert source.read_bytes() == original
    assert list(tmp_path.iterdir()) == [source]


def test_corrupt_member_is_caught_during_extraction_and_workspace_is_cleaned(tmp_path):
    data = bytearray(make_zip())
    with ZipFile(BytesIO(data)) as archive:
        entry = archive.getinfo(RESOURCE)
        offset = entry.header_offset
        start = offset + 30 + int.from_bytes(data[offset + 26 : offset + 28], "little")
        start += int.from_bytes(data[offset + 28 : offset + 30], "little")
        data[start + entry.compress_size // 2] ^= 0xFF
    source = tmp_path / "corrupt.zip"
    source.write_bytes(data)
    calls = []
    with pytest.raises(OperationError, match="corrupt or unreadable"):
        SigningArchive().transform(source, lambda path: calls.append(path))
    assert calls == []
    assert source.read_bytes() == data
    assert list(tmp_path.iterdir()) == [source]
