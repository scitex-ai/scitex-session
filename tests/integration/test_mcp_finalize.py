"""Real MCP discovery and finalization against disposable session files."""
import asyncio
import json
from pathlib import Path
import tarfile

import pytest


async def tool_request(arguments):
    from fastmcp import Client
    from scitex_session._mcp_server import mcp

    async with Client(mcp) as client:
        response = await client.call_tool("finalize_session", arguments)
    return json.loads(response.data) if isinstance(response.data, str) else response.data


async def discovered_tools():
    from fastmcp import Client
    from scitex_session._mcp_server import mcp

    async with Client(mcp) as client:
        return await client.list_tools()


def test_discovery_exposes_seven_tools_and_finalization_defaults(tmp_path):
    # Arrange: the genuine local MCP server.
    expected = {"archive_existing", "restore_existing", "archive_session_dir",
                "restore_session_archive", "finalize_session", "skills_list", "skills_get"}
    # Act: protocol discovery exposes the effective registered schema.
    tools = asyncio.run(discovered_tools())
    (tmp_path / "discovery.json").write_text(json.dumps(
        [t.model_dump(mode="json") for t in tools], indent=2
    ))
    schema = next(t.input_schema for t in tools if t.name == "finalize_session")
    defaults = {name: schema["properties"][name].get("default") for name in
                ["exit_status", "remove_src_dir", "max_wait", "archive_format"]}
    # Assert: every documented tool is discoverable and defaults are actionable.
    assert ({t.name for t in tools}, schema["required"], defaults) == (
        expected, ["config"], {"exit_status": None, "remove_src_dir": True,
                               "max_wait": 60, "archive_format": None},
    )


def finalization_arguments(source, code, archive_format):
    args = {"config": {"SDIR_RUN": str(source) + "/"}, "max_wait": 5,
            "archive_format": archive_format}
    if code != "omitted":
        args["exit_status"] = code
    return args


def read_payload(destination, archive_format):
    if archive_format is None:
        return {"payload.bin": (destination / "payload.bin").read_bytes()}
    with tarfile.open(destination, "r:gz") as archive:
        return {Path(m.name).name: archive.extractfile(m).read()
                for m in archive.getmembers() if m.isfile()}


def invoke_without_lifecycle_side_effects(arguments):
    from scitex_session import _hooks
    from scitex_session._manager import get_global_session_manager

    manager = get_global_session_manager()
    before = manager.list_sessions()
    events = []
    def on_close(status, exit_code):
        events.append((status, exit_code))
    _hooks.register_session_close_hook(on_close)
    try:
        result = asyncio.run(tool_request(arguments))
    finally:
        _hooks.unregister_session_close_hook(on_close)
    return result, events, manager.list_sessions() == before


@pytest.mark.parametrize("code,state", [
    ("omitted", "FINISHED"), (None, "FINISHED"),
    (0, "FINISHED_SUCCESS"), (1, "FINISHED_ERROR"),
    (7, "FINISHED_ERROR"), (-9, "FINISHED_ERROR"),
])
@pytest.mark.parametrize("archive_format", [None, "tar.gz"])
def test_finalize_tool_moves_files_without_session_close(
    tmp_path, code, state, archive_format
):
    # Arrange: existing RUNNING files; no live experiment or external services.
    source = tmp_path / "RUNNING" / "owned"
    source.mkdir(parents=True)
    payload = bytes(range(256)) * 16
    (source / "payload.bin").write_bytes(payload)
    arguments = finalization_arguments(source, code, archive_format)
    original = json.loads(json.dumps(arguments))
    # Act: schema validation and actual file movement through MCP.
    result, events, records_unchanged = invoke_without_lifecycle_side_effects(arguments)
    destination = Path(result["sdir_run"])
    suffix = ".tar.gz" if archive_format else ""
    # Assert: routing, return value, exact payload and file-only operation.
    assert (result, destination.parent.name, source.exists(),
            read_payload(destination, archive_format), events,
            records_unchanged, arguments) == (
        {"sdir_run": str(tmp_path / state / ("owned" + suffix))}, state,
        False, {"payload.bin": payload}, [], True, original,
    )


def test_finalize_missing_config_path_reports_error_and_preserves_files(tmp_path):
    # Arrange: an unrelated owned file must survive an invalid request.
    sentinel = tmp_path / "payload.bin"
    sentinel.write_bytes(b"preserve invalid request payload")
    # Act: the real MCP server rejects missing SDIR_RUN without touching files.
    from fastmcp.exceptions import ToolError
    error = missing_path_error(ToolError)
    # Assert: the expected protocol error and unchanged surrounding files.
    assert (error is not None and "SDIR_RUN" in error,
            {f.name: f.read_bytes() for f in tmp_path.iterdir()}) == (
        True, {"payload.bin": b"preserve invalid request payload"},
    )


def missing_path_error(error_type):
    try:
        asyncio.run(tool_request({"config": {}}))
    except error_type as error:
        return str(error)
    return None
