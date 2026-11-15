"""Standalone MCP operations over real subprocess stdin/stdout, without a host."""
import asyncio
from importlib import resources
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile

from anyio.from_thread import start_blocking_portal
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
import pytest

TOOLS = {"archive_existing", "restore_existing", "archive_session_dir",
         "restore_session_archive", "finalize_session", "skills_list", "skills_get"}
PAGES = {"SKILL", "01_installation", "02_quick-start", "03_python-api",
         "04_cli-reference", "05_mcp-tools", "06_env-vars"}
PAYLOAD = bytes(range(256)) * 32


def transport_for(interface, tmp_path):
    console = shutil.which("scitex-session-mcp")
    command, args = (console, []) if interface == "console" else (
        sys.executable, ["-m", "scitex_session._mcp_server"],
    )
    if command is None:
        raise RuntimeError("Install the declared scitex-session-mcp entry point")
    return StdioTransport(command=command, args=args, env=dict(os.environ),
                          cwd=str(tmp_path), keep_alive=False,
                          log_file=tmp_path / "server.stderr")


async def bounded_call(func, *args):
    return await asyncio.wait_for(func(*args), timeout=45)


@pytest.fixture(scope="module", params=["console", "module"])
def stdio_client(request, tmp_path_factory):
    work = tmp_path_factory.mktemp("stdio-" + request.param)
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(Client(transport_for(request.param, work))) as client:
            yield portal, client, request.param


async def request(client, name, args=None):
    response = await client.call_tool(name, {} if args is None else args)
    return json.loads(response.data) if isinstance(response.data, str) else response.data


def save_report(tmp_path, report):
    (tmp_path / "report.json").write_text(json.dumps(report, indent=2, default=str))


async def discovery(client, interface, tmp_path):
    tools = await client.list_tools()
    listed = await request(client, "skills_list")
    fetched = {}
    for name in ["03_python-api", "05_mcp-tools"]:
        fetched[name] = await request(client, "skills_get", {"name": name})
    schemas = {tool.name: tool.input_schema for tool in tools}
    report = {"interface": interface, "tools": sorted(t.name for t in tools),
              "schemas": schemas, "listed": listed, "fetched": fetched}
    save_report(tmp_path, report)
    return report


def test_stdio_discovery_and_metadata_owned_skill_bytes(tmp_path, stdio_client):
    portal, client, interface = stdio_client
    # Arrange: expected registered pages and bytes from this installed package.
    root = Path(str(resources.files("scitex_session")))
    expected = {name: (root / "_skills/scitex-session" / (name + ".md")).read_text()
                for name in ["03_python-api", "05_mcp-tools"]}
    # Act: genuine process transport and tool serialization.
    report = portal.call(bounded_call, discovery, client, interface, tmp_path)
    rows = report["listed"]["scitex-session"]
    paths_owned = all(Path(row["path"]).is_relative_to(root) for row in rows)
    fetched = {name: item["content"] for name, item in report["fetched"].items()}
    # Assert: complete discovery, defaults, namespace/owner and exact delivered content.
    assert (set(report["tools"]), set(report["listed"]),
            {row["name"] for row in rows}, {row["owner"] for row in rows}, paths_owned,
            report["schemas"]["archive_existing"]["properties"]["remove_src"]["default"],
            report["schemas"]["finalize_session"]["properties"]["exit_status"]["default"],
            fetched) == (
        TOOLS, {"scitex-session"}, PAGES, {"scitex-session"}, True, True, None, expected,
    )


async def archive_workflow(client, interface, tmp_path):
    single = tmp_path / "single"
    single.mkdir()
    (single / "payload.bin").write_bytes(PAYLOAD)
    bulk_root = tmp_path / "bulk"
    bulk = bulk_root / "2025Y-11M-12D-09h57m48s_STDIO"
    bulk.mkdir(parents=True)
    (bulk / "payload.bin").write_bytes(PAYLOAD)
    dest_root = tmp_path / "restored-bulk"
    archived = await request(client, "archive_session_dir", {"src_dir": str(single)})
    removed = not single.exists()
    restored = await request(client, "restore_session_archive", archived)
    bulk_result = await request(client, "archive_existing", {
        "root": str(bulk_root), "dry_run": False, "use_fd": False, "remove_src": False,
    })
    retained = (bulk / "payload.bin").read_bytes()
    restored_bulk = await request(client, "restore_existing", {
        "root": str(bulk_root), "dest_root": str(dest_root),
        "dry_run": False, "remove_archive": True,
    })
    archive = Path(archived["archive_path"])
    with tarfile.open(archive) as bundle:
        payloads = [bundle.extractfile(m).read() for m in bundle.getmembers() if m.isfile()]
    report = {"interface": interface, "archived": archived, "restored": restored,
              "bulk": bulk_result, "restored_bulk": restored_bulk,
              "single_source_was_removed": removed}
    save_report(tmp_path, report)
    return (removed, (Path(restored["dest_dir"]) / "payload.bin").read_bytes(), payloads,
            (bulk_result["archived"], bulk_result["failed"]), retained,
            (restored_bulk["restored"], restored_bulk["failed"]),
            (dest_root / bulk.name / "payload.bin").read_bytes(),
            (bulk_root / (bulk.name + ".tar.gz")).exists())


def test_stdio_all_archive_and_restore_tools_preserve_payload(tmp_path, stdio_client):
    portal, client, interface = stdio_client
    # Arrange: original bytes and declared default/removal policies.
    expected = (True, PAYLOAD, [PAYLOAD], (1, 0), PAYLOAD, (1, 0), PAYLOAD, False)
    # Act: all four owning tools round-trip over one real stdio connection.
    actual = portal.call(bounded_call, archive_workflow, client, interface, tmp_path)
    # Assert: source retention/removal and restored/archive bytes are complete.
    assert actual == expected


async def finalize(client, interface, tmp_path, code):
    source = tmp_path / "RUNNING/session"
    source.mkdir(parents=True)
    (source / "payload.bin").write_bytes(PAYLOAD)
    result = await request(client, "finalize_session", {
        "config": {"SDIR_RUN": str(source) + "/"}, "exit_status": code, "max_wait": 5,
    })
    dest = Path(result["sdir_run"])
    save_report(tmp_path, {"interface": interface, "code": code, "result": result,
                           "source": str(source), "destination": str(dest)})
    return dest.parent.name, source.exists(), (dest / "payload.bin").read_bytes()


@pytest.mark.parametrize("code,state", [(None, "FINISHED"), (0, "FINISHED_SUCCESS"),
                                        (7, "FINISHED_ERROR")])
def test_stdio_finalization_routes_exit_status_and_files(tmp_path, stdio_client, code, state):
    portal, client, interface = stdio_client
    # Arrange: explicit neutral, success and failure output routing.
    expected = (state, False, PAYLOAD)
    # Act: actual JSON request/response through the registered recovery tool.
    actual = portal.call(bounded_call, finalize, client, interface, tmp_path, code)
    # Assert: effective destination and every payload byte survive the move.
    assert actual == expected


async def finalization_error(client, interface, tmp_path):
    source = tmp_path / "RUNNING/session"
    dest = tmp_path / "FINISHED_ERROR/session"
    (source / "blocked").mkdir(parents=True)
    (source / "blocked/payload.bin").write_bytes(PAYLOAD)
    (dest / "blocked").mkdir(parents=True)
    (dest / "blocked/existing.bin").write_bytes(b"keep prior completion")
    error = None
    try:
        await request(client, "finalize_session", {
            "config": {"SDIR_RUN": str(source) + "/"}, "exit_status": 7, "max_wait": 5,
        })
    except ToolError as caught:
        error = {"type": type(caught).__name__, "message": str(caught)}
    tools_after = {tool.name for tool in await client.list_tools()}
    save_report(tmp_path, {"interface": interface, "error": error,
                           "tools_after_error": sorted(tools_after)})
    return (error["type"], "File exists" in error["message"], tools_after,
            (source / "blocked/payload.bin").read_bytes(),
            (dest / "blocked/existing.bin").read_bytes(),
            (dest / "blocked/payload.bin").exists())


def test_stdio_tool_error_keeps_files_and_connection_usable(tmp_path, stdio_client):
    portal, client, interface = stdio_client
    # Arrange: real destination collision and expected recoverable bytes.
    expected = ("ToolError", True, TOOLS, PAYLOAD, b"keep prior completion", False)
    # Act: error response followed by discovery on the same stdio connection.
    actual = portal.call(bounded_call, finalization_error, client, interface, tmp_path)
    # Assert: tool error survives serialization, bytes remain and protocol stays usable.
    assert actual == expected
