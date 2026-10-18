import os
import shutil
import stat
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from zipfile import ZIP_DEFLATED, BadZipFile, LargeZipFile, ZipFile, ZipInfo

from apple_remote_sign.archive_manifest import read_manifest
from apple_remote_sign.domain import OperationError
from apple_remote_sign.locks import ensure_ownership


@contextmanager
def _archive_errors() -> Iterator[None]:
    try:
        yield
    except (BadZipFile, LargeZipFile, EOFError, UnicodeError, zlib.error) as exc:
        raise OperationError(
            "ZIP is corrupt or unreadable; recreate it from the original app or DMG."
        ) from exc


class SigningArchive:
    def __init__(self, max_extracted_bytes: int = 4 * 1024**3, max_entries: int = 100_000) -> None:
        self.max_extracted_bytes = max_extracted_bytes
        self.max_entries = max_entries

    @staticmethod
    def validate_filename(filename: str) -> None:
        if not filename.lower().endswith(".zip"):
            raise OperationError(
                "Only ZIP uploads are supported. Package one .app directory or one .dmg file "
                "into a .zip archive (for example, using ditto -c -k --keepParent), then upload it."
            )

    def validate_upload(self, filename: str, source: BinaryIO) -> None:
        self.validate_filename(filename)
        source.seek(0)
        try:
            if source.read(4) not in {b"PK\x03\x04", b"PK\x05\x06"}:
                raise OperationError(
                    "Uploaded file is not a valid ZIP archive; renaming it is not enough."
                )
            source.seek(0)
            with _archive_errors(), ZipFile(source) as archive:
                read_manifest(archive, self.max_extracted_bytes, self.max_entries)
        finally:
            source.seek(0)

    def transform(self, path: Path, operation: Callable[[Path], str]) -> str:
        ensure_ownership()
        if path.suffix.lower() != ".zip":
            return operation(path)
        # Keep the archive unchanged until the operation and repack both succeed.
        with TemporaryDirectory(prefix=".app-", dir=path.parent) as directory:
            workspace = Path(directory)
            asset = self.extract(path, workspace / "unpacked")
            output = operation(asset)
            ensure_ownership()
            packed = workspace / "processed.zip"
            self.pack(asset, packed)
            ensure_ownership()
            os.replace(packed, path)
            return output

    def extract(self, source: Path, destination: Path) -> Path:
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        with _archive_errors(), ZipFile(source) as archive:
            manifest = read_manifest(archive, self.max_extracted_bytes, self.max_entries)
            total = 0
            for entry in manifest.entries:
                ensure_ownership(verify=False)
                path = destination / entry.path
                path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                if entry.kind == stat.S_IFDIR:
                    path.mkdir(mode=0o755, exist_ok=True)
                    path.chmod(entry.mode | 0o700)
                elif entry.kind == stat.S_IFREG:
                    with archive.open(entry.info) as stream, path.open("xb") as output:
                        while block := stream.read(1024 * 1024):
                            ensure_ownership(verify=False)
                            total += len(block)
                            if total > self.max_extracted_bytes:
                                raise OperationError("ZIP exceeds MAX_EXTRACTED_BYTES.")
                            output.write(block)
                    # Signing needs write access; retain all executable bits.
                    path.chmod(entry.mode | 0o600)
            # No writes can traverse links: create them only after all files/directories.
            for relative, target in manifest.links.items():
                (destination / relative).symlink_to(target)
        return destination / manifest.root

    def pack(self, asset: Path, destination: Path) -> None:
        with _archive_errors(), ZipFile(destination, "x", compression=ZIP_DEFLATED) as archive:
            for path in self._paths(asset):
                ensure_ownership(verify=False)
                name = path.relative_to(asset.parent).as_posix()
                if path.is_symlink():
                    info = ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, os.readlink(path).encode("utf-8"))
                else:
                    info = ZipInfo.from_file(path, name, strict_timestamps=False)
                    info.compress_type = ZIP_DEFLATED
                    if path.is_dir():
                        archive.writestr(info, b"")
                    else:
                        with path.open("rb") as stream, archive.open(info, "w") as output:
                            shutil.copyfileobj(stream, output, length=1024 * 1024)
        # Verify the generated archive can be read by the staple/retry path too.
        with destination.open("rb") as stream:
            self.validate_upload(destination.name, stream)
            os.fsync(stream.fileno())

    @staticmethod
    def _paths(asset: Path) -> Iterator[Path]:
        yield asset
        for directory, directories, files in os.walk(asset, followlinks=False):
            directories.sort()
            for name in sorted([*directories, *files]):
                yield Path(directory) / name
