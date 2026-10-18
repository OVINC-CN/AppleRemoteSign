"""Portable ditto stand-in for Linux client-script tests; macOS tests use native ditto."""

import stat
import sys
from pathlib import Path
from zipfile import ZipFile


def extract(source: Path, destination: Path) -> None:
    with ZipFile(source) as archive:
        for info in archive.infolist():
            path = destination / info.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                path.symlink_to(archive.read(info).decode())
            elif info.is_dir():
                path.mkdir(exist_ok=True)
            else:
                path.write_bytes(archive.read(info))
                path.chmod(mode & 0o777)


if __name__ == "__main__":
    extract(Path(sys.argv[-2]), Path(sys.argv[-1]))
