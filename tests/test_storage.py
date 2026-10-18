from io import BytesIO

import pytest
from sqlalchemy import func, select

from apple_remote_sign.domain import ApiError, OperationError
from apple_remote_sign.persistence.models import Job
from apple_remote_sign.services.submission import SubmissionService
from tests.fakes import TEST_APPLICATION_ID


@pytest.mark.parametrize("filename", [None, "", "dir/", "bad\x00.dmg", "bad\n.dmg", "bad\x7f.dmg"])
def test_invalid_filename_does_not_create_job(context, filename):
    with pytest.raises(ApiError) as raised:
        SubmissionService(context, TEST_APPLICATION_ID).submit(filename, BytesIO(b"original"))
    assert raised.value.status_code == 422
    with context.repository.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0
    assert not context.storage.root.exists()


def test_failed_upload_cleans_up_temporary_file(context):
    context.storage.max_upload_bytes = 3
    directory = f"{TEST_APPLICATION_ID}/2026/01/02/job"
    with pytest.raises(OperationError, match="MAX_UPLOAD_BYTES"):
        context.storage.save_upload(directory, "sample.dmg", BytesIO(b"too large"))
    assert list((context.storage.root / directory / "original").iterdir()) == []
