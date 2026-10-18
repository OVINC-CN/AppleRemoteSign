import plistlib
import stat
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

APP = "测试 App.app"
BINARY = f"{APP}/Contents/MacOS/example"
RESOURCE = f"{APP}/Contents/Frameworks/Example.framework/Versions/A/Resources/data"


def app_entries():
    return [
        (f"{APP}/", b"", stat.S_IFDIR | 0o755),
        (
            f"{APP}/Contents/Info.plist",
            plistlib.dumps({"CFBundleExecutable": "example", "CFBundlePackageType": "APPL"}),
            stat.S_IFREG | 0o644,
        ),
        (BINARY, b"original executable", stat.S_IFREG | 0o755),
        (RESOURCE, b"framework resource", stat.S_IFREG | 0o644),
        (
            f"{APP}/Contents/Frameworks/Example.framework/Versions/Current",
            b"A",
            stat.S_IFLNK | 0o777,
        ),
        (
            f"{APP}/Contents/Frameworks/Example.framework/Resources",
            b"Versions/Current/Resources",
            stat.S_IFLNK | 0o777,
        ),
    ]


def make_zip(entries=None):
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        for name, data, mode in app_entries() if entries is None else entries:
            info = ZipInfo(name)
            info.create_system = 3
            info.external_attr = mode << 16
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, data)
    return stream.getvalue()


def dmg_zip(content=b"original", filename="sample.dmg"):
    return make_zip([(filename, content, stat.S_IFREG | 0o644)])


def read_dmg(content, filename="sample.dmg"):
    with ZipFile(BytesIO(content)) as archive:
        return archive.read(filename)
