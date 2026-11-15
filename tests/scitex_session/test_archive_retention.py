"""Real bulk-archive retention through Python, CLI and in-memory MCP."""
import asyncio
import json
import os
import subprocess
import sys
import tarfile

import pytest


async def call_mcp(arguments):
    from fastmcp import Client
    from scitex_session._mcp_server import mcp

    async with Client(mcp) as client:
        result = await client.call_tool("archive_existing", arguments)
    return json.loads(result.data) if isinstance(result.data, str) else result.data


def prepare_case(tmp_path, archive_format, removal):
    name = "2025Y-11M-12D-09h57m48s_RETAIN"
    source = tmp_path / name
    source.mkdir()
    payload = bytes(range(256)) * 32
    (source / "payload.bin").write_bytes(payload)
    arguments = dict(root=str(tmp_path), dry_run=False,
                     format=archive_format, use_fd=False)
    if removal != "default":
        arguments["remove_src"] = removal
    return name, source, payload, arguments


def observed_files(tmp_path, archive_format, name, source):
    archive = tmp_path / (name + "." + archive_format)
    with tarfile.open(archive, "r:*") as bundle:
        members = [m for m in bundle.getmembers() if m.isfile()]
        archived = {m.name: bundle.extractfile(m).read() for m in members}
    retained = (source / "payload.bin").read_bytes() if source.exists() else None
    return archived, source.exists(), retained


def expected_files(name, payload, removal):
    return {name + "/payload.bin": payload}, removal is False, (
        payload if removal is False else None
    )


def run_cli(tmp_path, archive_format, removal):
    flags = [] if removal == "default" else [
        "--remove-src" if removal else "--no-remove-src"
    ]
    completed = subprocess.run(
        [sys.executable, "-m", "scitex_session.archive", "compress",
         str(tmp_path), "--execute", "--no-use-fd", "--format",
         archive_format, *flags], env=dict(os.environ),
        capture_output=True, text=True, timeout=60,
    )
    (tmp_path / "stdout.log").write_text(completed.stdout)
    (tmp_path / "stderr.log").write_text(completed.stderr)
    return completed


@pytest.mark.parametrize("archive_format", ["tar.gz", "tar", "tar.xz"])
@pytest.mark.parametrize("removal", ["default", True, False])
def test_python_bulk_archive_source_policy(tmp_path, archive_format, removal):
    # Arrange: real session-shaped source and all bytes expected to survive.
    from scitex_session.archive import archive_existing
    name, source, payload, arguments = prepare_case(tmp_path, archive_format, removal)
    # Act: invoke the owning bulk provider directly.
    summary = archive_existing(**arguments)
    # Assert: default/explicit source policy plus complete archive payload.
    assert ((summary["archived"], summary["failed"]),
            observed_files(tmp_path, archive_format, name, source)) == (
        (1, 0), expected_files(name, payload, removal),
    )


@pytest.mark.parametrize("archive_format", ["tar.gz", "tar", "tar.xz"])
@pytest.mark.parametrize("removal", ["default", True, False])
def test_cli_bulk_archive_source_policy(tmp_path, archive_format, removal):
    # Arrange: owned fixture and genuine child interpreter.
    name, source, payload, _ = prepare_case(tmp_path, archive_format, removal)
    # Act: use the advertised CLI flags with the existing60-second bound.
    completed = run_cli(tmp_path, archive_format, removal)
    # Assert: successful summary, source policy and byte-exact archive content.
    assert ((completed.returncode, "archived=1" in completed.stdout,
             "failed=0" in completed.stdout),
            observed_files(tmp_path, archive_format, name, source)) == (
        (0, True, True), expected_files(name, payload, removal),
    )


@pytest.mark.parametrize("archive_format", ["tar.gz", "tar", "tar.xz"])
@pytest.mark.parametrize("removal", ["default", True, False])
def test_mcp_bulk_archive_source_policy(tmp_path, archive_format, removal):
    # Arrange: local-only real MCP Client/server and disposable files.
    name, source, payload, arguments = prepare_case(tmp_path, archive_format, removal)
    # Act: schema validation and tool dispatch through FastMCP.
    summary = asyncio.run(call_mcp(arguments))
    # Assert: tool summary, requested policy and complete binary payload.
    assert ((summary["archived"], summary["failed"]),
            observed_files(tmp_path, archive_format, name, source)) == (
        (1, 0), expected_files(name, payload, removal),
    )
