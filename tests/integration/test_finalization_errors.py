"""Copy and cleanup failures remain observable through Python and real MCP."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

PREFIXES = ("Congratulations!", "Script failed:", "Script finished:")
PAYLOAD = bytes(range(256)) * 16


def make_case(tmp_path, scenario, code):
    state = "FINISHED" if code is None else (
        "FINISHED_SUCCESS" if code == 0 else "FINISHED_ERROR"
    )
    source = tmp_path / "work" / "RUNNING" / "owned"
    destination = tmp_path / "work" / state / "owned"
    source.parent.mkdir(parents=True)
    if scenario == "copy_conflict":
        (source / "logs").mkdir(parents=True)
        (destination / "logs").mkdir(parents=True)
        (destination / "logs" / "existing.bin").write_bytes(b"keep destination")
        expected_destination = {"logs/existing.bin": b"keep destination"}
        error_type, message = FileExistsError, "File exists"
    else:
        target = tmp_path / "original"
        (target / "logs").mkdir(parents=True)
        source.symlink_to(target, target_is_directory=True)
        expected_destination = {"logs/payload.bin": PAYLOAD}
        error_type, message = OSError, str(source)
    (source / "logs" / "payload.bin").write_bytes(PAYLOAD)
    # No trailing slash: rmtree must reject the symbolic link itself.
    config = {"SDIR_RUN": str(source)}
    return SimpleNamespace(source=source, destination=destination, code=code,
                           scenario=scenario, config=config, error_type=error_type,
                           message=message, expected_destination=expected_destination)


def files_under(root):
    resolved = root.resolve()
    return {f.relative_to(resolved).as_posix(): f.read_bytes()
            for f in resolved.rglob("*") if f.is_file()}


def capture_error(operation, expected_type):
    try:
        operation()
    except expected_type as error:
        return {"type": type(error).__name__, "message": str(error)}
    return None


async def mcp_finalize(config, code):
    from fastmcp import Client
    from scitex_session._mcp_server import mcp
    async with Client(mcp) as client:
        await client.call_tool("finalize_session", {
            "config": config, "exit_status": code, "max_wait": 5,
        })


def observation(tmp_path, case, error, caplog):
    messages = [r.getMessage() for r in caplog.records
                if r.getMessage().startswith(PREFIXES)]
    actual_source = files_under(case.source)
    actual_destination = files_under(case.destination)
    result = (error is not None and case.message in error["message"],
              actual_source, actual_destination, case.source.exists(),
              case.source.is_symlink(), messages, case.config)
    (tmp_path / "observation.json").write_text(json.dumps({
        "scenario": case.scenario, "exit_status": case.code, "error": error,
        "source": str(case.source), "destination": str(case.destination),
        "source_is_symlink": case.source.is_symlink(),
        "source_files": sorted(actual_source),
        "destination_files": sorted(actual_destination),
        "completion_messages": messages, "config": case.config,
    }, indent=2))
    return result


def expected(case):
    return (True, {"logs/payload.bin": PAYLOAD}, case.expected_destination,
            True, case.scenario == "cleanup_symlink", [],
            {"SDIR_RUN": str(case.source)})


@pytest.mark.parametrize("scenario", ["copy_conflict", "cleanup_symlink"])
@pytest.mark.parametrize("code", [None, 0, 7])
def test_python_finalization_reports_filesystem_failure(tmp_path, caplog, scenario, code):
    # Arrange: genuine copy collision or rmtree-rejected symbolic link.
    from scitex_session._lifecycle._close import running2finished
    case = make_case(tmp_path, scenario, code)
    caplog.set_level("INFO")
    # Act: the real provider must surface its original filesystem error.
    error = capture_error(lambda: running2finished(case.config, exit_status=code,
                                                  max_wait=5), case.error_type)
    # Assert: error, intact recoverable files, unchanged config, no completion claim.
    assert observation(tmp_path, case, error, caplog) == expected(case)


@pytest.mark.parametrize("scenario", ["copy_conflict", "cleanup_symlink"])
@pytest.mark.parametrize("code", [None, 0, 7])
def test_mcp_finalization_reports_filesystem_failure(tmp_path, caplog, scenario, code):
    # Arrange: real local Client/server with disposable filesystem fixtures.
    from fastmcp.exceptions import ToolError
    case = make_case(tmp_path, scenario, code)
    caplog.set_level("INFO")
    # Act: actual protocol dispatch must report failure rather than a success path.
    error = capture_error(lambda: asyncio.run(mcp_finalize(case.config, code)), ToolError)
    # Assert: same file/config preservation contract and visible protocol error.
    assert observation(tmp_path, case, error, caplog) == expected(case)
