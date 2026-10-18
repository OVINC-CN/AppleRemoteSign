import plistlib
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.parsers.expat import ExpatError
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from apple_remote_sign.domain import OperationError


@dataclass(frozen=True)
class ArchiveEntry:
    info: ZipInfo
    path: PurePosixPath
    kind: int
    mode: int


@dataclass(frozen=True)
class ArchiveManifest:
    root: str
    entries: list[ArchiveEntry]
    links: dict[PurePosixPath, str]


def _entry(info: ZipInfo) -> ArchiveEntry:
    name = info.orig_filename.removesuffix("/")
    parts = name.split("/")
    if (
        not name
        or "\\" in name
        or re.search(r"[\x00-\x1f\x7f]", name)
        or len(parts) > 128
        or any(part in {"", ".", ".."} or ":" in part for part in parts)
        or any(len(part.encode("utf-8")) > 255 for part in parts)
    ):
        raise OperationError("ZIP contains an invalid or unsafe entry path.")
    mode = info.external_attr >> 16 if info.create_system == 3 else 0
    kind = stat.S_IFMT(mode) or (stat.S_IFDIR if info.is_dir() else stat.S_IFREG)
    if kind not in {stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK}:
        raise OperationError("ZIP contains a special file; only files, directories and links work.")
    if info.is_dir() and kind != stat.S_IFDIR:
        raise OperationError("ZIP entry type does not match its directory path.")
    if info.flag_bits & 1 or info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
        raise OperationError("ZIP must be unencrypted and use stored or deflate compression.")
    permissions = mode & 0o777 if mode & 0o777 else (0o755 if kind == stat.S_IFDIR else 0o644)
    return ArchiveEntry(info, PurePosixPath(name), kind, permissions)


def _index(entries: list[ArchiveEntry]) -> dict[PurePosixPath, int]:
    nodes: dict[PurePosixPath, int] = {}
    spellings: dict[str, PurePosixPath] = {}
    seen: set[PurePosixPath] = set()
    for entry in entries:
        if entry.path in seen:
            raise OperationError("ZIP contains duplicate entries.")
        seen.add(entry.path)
        parents = [(parent, stat.S_IFDIR) for parent in entry.path.parents if parent.parts]
        for path, kind in [*parents, (entry.path, entry.kind)]:
            key = unicodedata.normalize("NFD", str(path)).casefold()
            if key in spellings and spellings[key] != path:
                raise OperationError("ZIP contains paths that collide on macOS filesystems.")
            if path in nodes and nodes[path] != kind:
                raise OperationError("ZIP contains a file or symlink used as a parent directory.")
            spellings[key] = path
            nodes[path] = kind
    return nodes


def _resolve(parts: list[str], links: dict[PurePosixPath, str]) -> PurePosixPath:
    resolved: list[str] = []
    followed = 0
    pending = list(reversed(parts))
    while pending:
        part = pending.pop()
        if part in {"", "."}:
            continue
        if part == "..":
            if len(resolved) <= 1:
                raise OperationError("ZIP symlink escapes the app bundle.")
            resolved.pop()
            continue
        resolved.append(part)
        target = links.get(PurePosixPath(*resolved))
        if target is not None:
            followed += 1
            if followed > 40:
                raise OperationError("ZIP contains a cyclic or excessively deep symlink.")
            resolved.pop()
            pending.extend(reversed(target.split("/")))
    return PurePosixPath(*resolved)


def _links(
    archive: ZipFile, entries: list[ArchiveEntry], nodes: dict[PurePosixPath, int]
) -> dict[PurePosixPath, str]:
    links: dict[PurePosixPath, str] = {}
    for entry in entries:
        if entry.kind != stat.S_IFLNK:
            continue
        if not 0 < entry.info.file_size <= 4096:
            raise OperationError("ZIP contains an invalid symlink target.")
        target = archive.read(entry.info).decode("utf-8")
        if target.startswith("/") or "\\" in target or re.search(r"[\x00-\x1f\x7f:]", target):
            raise OperationError("ZIP symlinks must use relative paths within the app bundle.")
        links[entry.path] = target
    for path in links:
        resolved = _resolve(list(path.parts), links)
        if resolved not in nodes or path.parent.is_relative_to(resolved):
            raise OperationError("ZIP contains a dangling symlink or a link to an ancestor.")
    return links


def _validate_app(archive: ZipFile, manifest: ArchiveManifest) -> None:
    entries = {entry.path: entry for entry in manifest.entries}
    contents = PurePosixPath(manifest.root) / "Contents"
    info = entries.get(contents / "Info.plist")
    if info is None or info.kind != stat.S_IFREG or info.info.file_size > 1024 * 1024:
        raise OperationError("ZIP must contain a macOS app with Contents/Info.plist (up to 1 MiB).")
    try:
        metadata = plistlib.loads(archive.read(info.info))
    except (plistlib.InvalidFileException, ValueError, OverflowError, ExpatError) as exc:
        raise OperationError("ZIP contains an invalid Info.plist.") from exc
    executable = metadata.get("CFBundleExecutable") if isinstance(metadata, dict) else None
    if (
        not isinstance(executable, str)
        or executable in {"", ".", ".."}
        or re.search(r"[/\\\x00-\x1f\x7f:]", executable)
    ):
        raise OperationError("App Info.plist must declare a valid CFBundleExecutable.")
    path = _resolve(list((contents / "MacOS" / executable).parts), manifest.links)
    binary = entries.get(path)
    if binary is None or binary.kind != stat.S_IFREG:
        raise OperationError("ZIP is missing the executable declared in Info.plist.")
    if not binary.mode & 0o111:
        raise OperationError(
            "App executable lacks execute permission; recreate the ZIP with ditto."
        )


def _is_metadata(archive: ZipFile, entry: ArchiveEntry) -> bool:
    if entry.path.parts[0] in {"__MACOSX", ".DS_Store"}:
        return True
    if entry.kind != stat.S_IFREG or not entry.path.name.startswith("._"):
        return False
    # ditto can store AppleDouble beside each file instead of under __MACOSX.
    # macOS consumes these on extraction, so signing them as resources breaks the seal.
    with archive.open(entry.info) as stream:
        return stream.read(8) == b"\x00\x05\x16\x07\x00\x02\x00\x00"


def read_manifest(archive: ZipFile, max_bytes: int, max_entries: int) -> ArchiveManifest:
    infos = archive.infolist()
    if len(infos) > max_entries:
        raise OperationError("ZIP exceeds MAX_ARCHIVE_ENTRIES.")
    if sum(info.file_size for info in infos) > max_bytes:
        raise OperationError("ZIP exceeds MAX_EXTRACTED_BYTES.")
    entries = [_entry(info) for info in infos]
    entries = [entry for entry in entries if not _is_metadata(archive, entry)]
    roots = {entry.path.parts[0] for entry in entries}
    if len(roots) != 1 or not next(iter(roots)).lower().endswith((".app", ".dmg")):
        raise OperationError("ZIP must contain exactly one top-level .app directory or .dmg file.")
    root = next(iter(roots))
    nodes = _index(entries)
    manifest = ArchiveManifest(root, entries, _links(archive, entries, nodes))
    if root.lower().endswith(".app"):
        if nodes.get(PurePosixPath(root)) != stat.S_IFDIR:
            raise OperationError("The .app inside the ZIP must be a directory, not a file.")
        _validate_app(archive, manifest)
    elif len(entries) != 1 or entries[0].kind != stat.S_IFREG or entries[0].info.file_size == 0:
        raise OperationError("The .dmg inside the ZIP must be a nonempty regular file.")
    return manifest
