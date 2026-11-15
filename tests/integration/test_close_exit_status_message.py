"""Real completion routing/logging, replacing brittle source-string checks."""
from pathlib import Path
import tarfile

import pytest

from scitex_session._lifecycle._close import running2finished


PREFIXES = ("Script failed:", "Congratulations! The script completed:", "Script finished:")


def payload_content(destination, archive_format):
    if archive_format is None:
        return 1, (destination / "payload.txt").read_text()
    with tarfile.open(destination, "r:gz") as archive:
        members = [m for m in archive.getmembers()
                   if m.isfile() and Path(m.name).name == "payload.txt"]
        return len(members), archive.extractfile(members[0]).read().decode()


@pytest.mark.parametrize("code,state,level,prefix", [
    (0, "FINISHED_SUCCESS", 20, "Congratulations! The script completed:"),
    (None, "FINISHED", 20, "Script finished:"),
    (1, "FINISHED_ERROR", 40, "Script failed:"),
    (7, "FINISHED_ERROR", 40, "Script failed:"),
    (-9, "FINISHED_ERROR", 40, "Script failed:"),
])
@pytest.mark.parametrize("archive_format", [None, "tar.gz"])
def test_completion_path_payload_and_message_follow_actual_exit(
    tmp_path, caplog, code, state, level, prefix, archive_format,
):
    # Arrange: real owned files, default source removal, real optional archive.
    source = tmp_path / "outputs" / "RUNNING" / "owned"
    source.mkdir(parents=True)
    (source / "payload.txt").write_text("owned payload")
    config = {"SDIR_RUN": str(source) + "/"}
    caplog.set_level("INFO")
    # Act: preserve the existing5-second copy-wait bound in owning unit tests.
    result = running2finished(config, exit_status=code, max_wait=5,
                              archive_format=archive_format)
    destination = Path(result["SDIR_RUN"])
    messages = [(record.levelno, next(p for p in PREFIXES
                 if record.getMessage().startswith(p)))
                for record in caplog.records
                if record.getMessage().startswith(PREFIXES)]
    observed_payload = payload_content(destination, archive_format)
    # Assert: route, complete payload, source cleanup and exactly one true message.
    assert (destination.parent.name, observed_payload, source.exists(), messages) == (
        state, (1, "owned payload"), False, [(level, prefix)],
    )
