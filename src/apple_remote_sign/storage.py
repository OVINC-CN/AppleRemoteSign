import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from apple_remote_sign.domain import OperationError
from apple_remote_sign.locks import ensure_ownership


@dataclass(frozen=True)
class StoredFile:
    path: str
    sha256: str
    size: int


def job_directory(application_id: str, job_id: str, created_at: datetime) -> str:
    """Use the job's persisted UTC creation date for every storage stage."""
    return f"{application_id}/{created_at:%Y/%m/%d}/{job_id}"


def safe_filename(filename: str | None) -> str:
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if name in {"", ".", ".."} or re.search(r"[\x00-\x1f\x7f]", name):
        raise OperationError("Filename is empty or contains invalid characters.")
    if len(name.encode("utf-8")) > 255:
        raise OperationError("Filename exceeds 255 UTF-8 bytes.")
    return name


class FileStorage:
    def __init__(self, root: Path, max_upload_bytes: int) -> None:
        self.root = root.resolve()
        self.max_upload_bytes = max_upload_bytes

    def resolve(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise OperationError("Invalid stored file path.")
        return path

    def path_for(self, directory: str, stage: str, filename: str) -> str:
        relative = f"{directory}/{stage}/{filename}"
        self.resolve(relative).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return relative

    def fingerprint(self, source: BinaryIO) -> str:
        digest = hashlib.sha256()
        size = 0
        source.seek(0)
        try:
            while block := source.read(1024 * 1024):
                size += len(block)
                if size > self.max_upload_bytes:
                    raise OperationError("Upload exceeds MAX_UPLOAD_BYTES.")
                digest.update(block)
            if size == 0:
                raise OperationError("Uploaded file is empty; upload a file containing data.")
        finally:
            source.seek(0)
        return digest.hexdigest()

    def save_upload(self, directory: str, filename: str, source: BinaryIO) -> StoredFile:
        relative = self.path_for(directory, "original", filename)
        destination = self.resolve(relative)
        temporary = destination.with_name(f".upload-{uuid4().hex}.part")
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as output:
                while block := source.read(1024 * 1024):
                    ensure_ownership(verify=False)
                    size += len(block)
                    if size > self.max_upload_bytes:
                        raise OperationError("Upload exceeds MAX_UPLOAD_BYTES.")
                    output.write(block)
                    digest.update(block)
                output.flush()
                os.fsync(output.fileno())
            ensure_ownership()
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredFile(relative, digest.hexdigest(), size)

    def copy(self, source: str, directory: str, stage: str, filename: str) -> str:
        relative = self.path_for(directory, stage, filename)
        shutil.copyfile(self.require_file(source), self.resolve(relative))
        return relative

    def inspect(self, relative: str) -> StoredFile:
        path = self.require_file(relative)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return StoredFile(relative, digest, path.stat().st_size)

    def publish(self, candidate: str, directory: str, filename: str) -> StoredFile:
        relative = self.path_for(directory, "artifact", filename)
        path = self.require_file(candidate)
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(path, self.resolve(relative))
        return self.inspect(relative)

    def require_file(self, relative: str | None) -> Path:
        if relative is None:
            raise OperationError("Stored file reference is missing.")
        path = self.resolve(relative)
        if not path.is_file():
            raise OperationError(f"Stored file is unavailable: {relative}")
        return path
