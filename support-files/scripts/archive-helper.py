"""Pack uploads, then validate/unpack/publish results for the shortcut (stdlib only)."""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import stat
import subprocess
import sys
import unicodedata
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo


def input_paths(source: Path) -> Iterator[Path]:
    def fail_on_error(error: OSError) -> None:
        raise error

    yield source
    if not source.is_dir():
        return
    for directory, directories, files in os.walk(source, followlinks=False, onerror=fail_on_error):
        directories.sort()
        for name in sorted([*directories, *files]):
            yield Path(directory) / name


def pack(source: Path, destination: Path) -> None:
    # zipfile marks Unicode names as UTF-8, including on macOS. Stream large files.
    # Keep the asset itself at the ZIP root for both an app directory and a DMG file.
    with ZipFile(destination, "x", compression=ZIP_DEFLATED) as archive:
        for path in input_paths(source):
            name = path.relative_to(source.parent).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                info = ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, os.readlink(path).encode("utf-8"))
            elif stat.S_ISDIR(mode) or stat.S_ISREG(mode):
                info = ZipInfo.from_file(path, name, strict_timestamps=False)
                info.compress_type = ZIP_DEFLATED
                if stat.S_ISDIR(mode):
                    archive.writestr(info, b"")
                else:
                    with path.open("rb") as stream, archive.open(info, "w") as output:
                        shutil.copyfileobj(stream, output, length=1024 * 1024)
            else:
                raise ValueError("Input contains a special file that cannot be packaged.")


def entry_path(info: ZipInfo, expected_name: str) -> tuple[PurePosixPath, int]:
    name = info.orig_filename.removesuffix("/")
    parts = name.split("/")
    if (
        not name
        or parts[0] != expected_name
        or any(part in {"", ".", ".."} for part in parts)
        or re.search(r"[\\:\x00-\x1f\x7f]", name)
    ):
        raise ValueError("Downloaded ZIP contains an unexpected or unsafe path.")
    mode = info.external_attr >> 16 if info.create_system == 3 else 0
    kind = stat.S_IFMT(mode) or (stat.S_IFDIR if info.is_dir() else stat.S_IFREG)
    if kind not in {stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK} or info.flag_bits & 1:
        raise ValueError("Downloaded ZIP contains a special file or an encrypted entry.")
    if info.is_dir() and kind != stat.S_IFDIR:
        raise ValueError("Downloaded ZIP entry type does not match its path.")
    return PurePosixPath(name), kind


def index_entry(
    path: PurePosixPath,
    kind: int,
    nodes: dict[PurePosixPath, int],
    spellings: dict[str, PurePosixPath],
) -> None:
    parents = [(parent, stat.S_IFDIR) for parent in path.parents if parent.parts]
    for node, node_kind in [*parents, (path, kind)]:
        key = unicodedata.normalize("NFD", str(node)).casefold()
        if key in spellings and spellings[key] != node:
            raise ValueError("Downloaded ZIP contains colliding paths.")
        if node in nodes and nodes[node] != node_kind:
            raise ValueError("Downloaded ZIP uses a file or symlink as a parent directory.")
        spellings[key] = node
        nodes[node] = node_kind


def validate_archive(path: Path, expected_name: str, kind: str) -> None:
    with ZipFile(path) as archive:
        nodes: dict[PurePosixPath, int] = {}
        names: set[PurePosixPath] = set()
        spellings: dict[str, PurePosixPath] = {}
        for info in archive.infolist():
            relative, entry_kind = entry_path(info, expected_name)
            if relative in names:
                raise ValueError("Downloaded ZIP contains duplicate entries.")
            names.add(relative)
            index_entry(relative, entry_kind, nodes, spellings)
        expected_kind = stat.S_IFDIR if kind == "app" else stat.S_IFREG
        if nodes.get(PurePosixPath(expected_name)) != expected_kind:
            raise ValueError("Downloaded ZIP does not contain the expected App/DMG.")
        if kind == "dmg" and len(names) != 1:
            raise ValueError("Downloaded ZIP must contain exactly the expected DMG file.")


def publish(source: Path, destination: Path) -> None:
    # renamex_np/renameat2 support directories and fail atomically if ANY target exists.
    # Plain mv can move an app inside an existing directory instead of rejecting it.
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = library.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        args = (os.fsencode(source), os.fsencode(destination), 0x00000004)  # RENAME_EXCL
    elif sys.platform == "linux":
        rename = library.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        args = (-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # RENAME_NOREPLACE
    else:
        raise ValueError("Atomic output publication is only supported on macOS and Linux.")
    rename.restype = ctypes.c_int
    if rename(*args) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "pack":
        pack(Path(sys.argv[2]), Path(sys.argv[3]))
        return
    if len(sys.argv) != 7 or sys.argv[1] != "unpack":
        raise ValueError("Expected pack INPUT ZIP or unpack ZIP DIRECTORY NAME KIND OUTPUT.")
    archive, directory, name, kind, output = sys.argv[2:]
    if kind not in {"app", "dmg"}:
        raise ValueError("Expected app or dmg output.")
    validate_archive(Path(archive), name, kind)
    subprocess.run(["ditto", "-x", "-k", archive, directory], check=True)
    asset = Path(directory) / name
    if asset.is_symlink() or not (asset.is_dir() if kind == "app" else asset.is_file()):
        raise ValueError("Extracted result does not have the expected App/DMG type.")
    publish(asset, Path(output))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, BadZipFile, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
