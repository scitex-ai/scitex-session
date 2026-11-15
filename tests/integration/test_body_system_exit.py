"""Body SystemExit must retain its identity and describe the actual lifecycle exit."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = r'''import argparse, json, os, sys, traceback
from pathlib import Path
import scitex_session as sess
from scitex_session import _hooks
from scitex_session._manager import get_global_session_manager

interface = os.environ["HELPER_INTERFACE"]
mode = os.environ["HELPER_EXIT_MODE"]
collision = os.environ["HELPER_COLLISION"] == "1"
code = {"none": None, "nonzero": 7, "message": "owned deliberate exit"}[mode]
expected_status = 0 if code is None else (code if isinstance(code, int) else 1)
original_exit = SystemExit(code)
body, events = {"calls": 0}, []
def on_start(session_id, script_path, metadata):
    events.append({"kind": "start"})
def on_close(status, exit_code):
    events.append({"kind": "close", "status": status, "exit_code": exit_code})
sess.register_session_start_hook(on_start)
sess.register_session_close_hook(on_close)

def body_work(config, plotter):
    body["calls"] += 1
    source = Path(config["SDIR_RUN"])
    state = "FINISHED_SUCCESS" if expected_status == 0 else "FINISHED_ERROR"
    destination = Path(str(source).replace("RUNNING/", state + "/"))
    body.update(source=str(source), destination=str(destination), session_id=str(config["ID"]))
    (source / "blocked").mkdir()
    (source / "blocked/payload.bin").write_bytes(bytes(range(256)) * 16)
    plotter.subplots()
    if collision:
        (destination / "blocked").mkdir(parents=True)
        (destination / "blocked/existing.bin").write_bytes(b"keep prior completion")
    raise original_exit

if interface == "decorator":
    @sess.session(verbose=False, agg=True, sdir_suffix="bodyexit")
    def main(CONFIG=sess.INJECTED, plt=sess.INJECTED):
        return body_work(CONFIG, plt)
    def invoke():
        return main()
else:
    def main(args):
        record = next(iter(get_global_session_manager().get_active_sessions().values()))
        import matplotlib.pyplot as plotter
        return body_work(record["config"], plotter)
    def invoke():
        return sess._run(main, parse_args=lambda: argparse.Namespace(),
                         verbose=False, agg=True, sdir_suffix="bodyexit")

saved_stdout, saved_stderr = sys.stdout, sys.stderr
result, error, process_code = None, None, 1
try:
    result = invoke()
except BaseException as caught:
    process_code = caught.code if isinstance(caught, SystemExit) else 1
    error = {"type": type(caught).__name__, "original_exit": caught is original_exit,
             "code": caught.code if isinstance(caught, SystemExit) else None,
             "frames": [f.name for f in traceback.extract_tb(caught.__traceback__)]}
finally:
    sys.stdout, sys.stderr = saved_stdout, saved_stderr
    import matplotlib.pyplot as final_plt
    manager = get_global_session_manager()
    records = manager.list_sessions()
    record = next(iter(records.values())) if records else {}
    report = {"interface": interface, "mode": mode, "body": body, "error": error,
              "result": result, "events": events, "open_figures": final_plt.get_fignums(),
              "record_count": len(records), "record_status": record.get("status"),
              "record_exit_code": record.get("exit_code"),
              "record_ended": record.get("end_time") is not None,
              "still_active": bool(manager.get_active_sessions())}
    Path(os.environ["HELPER_EVENT_REPORT"]).write_text(json.dumps(report, default=str))
    _hooks.unregister_session_start_hook(on_start)
    _hooks.unregister_session_close_hook(on_close)
raise SystemExit(process_code)
'''


def run_case(tmp_path, interface, mode, collision):
    script = tmp_path / "body_exit_script.py"
    script.write_text(SCRIPT)
    report_path = tmp_path / "report.json"
    env = dict(os.environ, HELPER_EVENT_REPORT=str(report_path),
               HELPER_INTERFACE=interface, HELPER_EXIT_MODE=mode,
               HELPER_COLLISION=str(int(collision)),
               SCITEX_SESSION_OUT_DIR=str(tmp_path / "outputs"))
    completed = subprocess.run([sys.executable, str(script)], cwd=tmp_path,
                               env=env, capture_output=True, text=True, timeout=60)
    (tmp_path / "stdout.log").write_text(completed.stdout)
    (tmp_path / "stderr.log").write_text(completed.stderr)
    return completed, json.loads(report_path.read_text())


def files_after(report):
    source = Path(report["body"]["source"])
    destination = Path(report["body"]["destination"])
    return (source.exists(),
            {f.name: f.read_bytes() for f in (source / "blocked").glob("*")},
            {f.name: f.read_bytes() for f in (destination / "blocked").glob("*")})


def expected_files(collision):
    payload = {"payload.bin": bytes(range(256)) * 16}
    return (True, payload, {"existing.bin": b"keep prior completion"}) if collision else (
        False, {}, payload,
    )


@pytest.mark.parametrize("interface,mode,collision,code,status,closes", [
    ("decorator", "none", False, None, 0, 2),
    ("advanced", "none", False, None, 0, 2),
    ("decorator", "nonzero", False, 7, 7, 2),
    ("advanced", "nonzero", False, 7, 7, 2),
    ("decorator", "message", False, "owned deliberate exit", 1, 2),
    ("advanced", "message", False, "owned deliberate exit", 1, 2),
    ("decorator", "nonzero", True, 7, 7, 1),
    ("advanced", "nonzero", True, 7, 7, 1),
])
def test_body_system_exit_preserves_process_and_lifecycle_status(
    tmp_path, interface, mode, collision, code, status, closes
):
    # Arrange: actual decorator or imperative runner, real payload and optional collision.
    expected_events = [("success" if status == 0 else "failed", status)] * closes
    # Act: body raises a specific SystemExit object in a fresh child process.
    completed, report = run_case(tmp_path, interface, mode, collision)
    close_values = [(e["status"], e["exit_code"]) for e in report["events"]
                    if e["kind"] == "close"]
    frames = report["error"]["frames"]
    # Assert: Python process status, original exception and complete lifecycle agree.
    assert (completed.returncode, report["result"], report["body"]["calls"],
            report["error"]["type"], report["error"]["original_exit"],
            report["error"]["code"], "main" in frames and "body_work" in frames,
            files_after(report), report["open_figures"], report["record_count"],
            report["record_status"], report["record_exit_code"], report["record_ended"],
            report["still_active"], [e["kind"] for e in report["events"]],
            close_values) == (
        status, None, 1, "SystemExit", True, code, True,
        expected_files(collision), [], 1, "closed", status, True, False,
        ["start", "start"] + ["close"] * closes, expected_events,
    )
