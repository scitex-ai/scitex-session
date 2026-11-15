"""Real decorated processes preserve body errors and expose cleanup failures."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = r'''import json, os, sys, traceback
from pathlib import Path
import scitex_session as sess
from scitex_session import _hooks
from scitex_session._manager import get_global_session_manager

body = {}
events = []
original_body_error = RuntimeError("original experiment failure")
def on_start(session_id, script_path, metadata):
    events.append({"kind": "start"})
def on_close(status, exit_code):
    events.append({"kind": "close", "status": status, "exit_code": exit_code})
sess.register_session_start_hook(on_start)
sess.register_session_close_hook(on_close)

@sess.session(notify=False, verbose=False, agg=True)
def main(mode: str = "ok"):
    source = Path(CONFIG["SDIR_RUN"])
    body["session_id"] = str(CONFIG["ID"])
    body["source"] = str(source)
    body["mode"] = mode
    state = "FINISHED_SUCCESS" if mode == "ok" else "FINISHED_ERROR"
    destination = Path(str(source).replace("RUNNING/", state + "/"))
    body["destination"] = str(destination)
    (source / "blocked").mkdir()
    (source / "blocked" / "payload.bin").write_bytes(bytes(range(256)) * 16)
    plt.subplots()
    if os.environ["HELPER_COLLISION"] == "1":
        (destination / "blocked").mkdir(parents=True)
        (destination / "blocked" / "existing.bin").write_bytes(b"keep prior completion")
    if mode == "raised":
        raise original_body_error
    return 7 if mode == "nonzero" else 0

def invoke_main():
    if os.environ["HELPER_OUTER_HANDLER"] == "1":
        try:
            raise LookupError("caller handler sentinel")
        except LookupError:
            return main()
    return main()

saved_stdout, saved_stderr = sys.stdout, sys.stderr
result, error, exit_code = None, None, 1
try:
    result = invoke_main()
    exit_code = result if isinstance(result, int) else 0
except Exception as caught:
    error = {"type": type(caught).__name__, "message": str(caught),
             "original_body_error": caught is original_body_error,
             "frames": [f.name for f in traceback.extract_tb(caught.__traceback__)]}
finally:
    sys.stdout, sys.stderr = saved_stdout, saved_stderr
    import matplotlib.pyplot as final_plt
    manager = get_global_session_manager()
    record = manager.get_session(body.get("session_id"))
    payload = {"body": body, "result": result, "error": error, "events": events,
               "open_figures": final_plt.get_fignums(),
               "record_status": record.get("status"),
               "record_exit_code": record.get("exit_code"),
               "record_ended": record.get("end_time") is not None,
               "still_active": body.get("session_id") in manager.get_active_sessions()}
    Path(os.environ["HELPER_EVENT_REPORT"]).write_text(json.dumps(payload, default=str))
    _hooks.unregister_session_start_hook(on_start)
    _hooks.unregister_session_close_hook(on_close)
raise SystemExit(exit_code)
'''


def blocked_payloads(report):
    source = Path(report["body"]["source"])
    destination = Path(report["body"]["destination"])
    source_files = {f.name: f.read_bytes() for f in (source / "blocked").glob("*")}
    destination_files = {f.name: f.read_bytes() for f in (destination / "blocked").glob("*")}
    return source.exists(), source_files, destination_files


def expected_payloads(collision):
    payload = {"payload.bin": bytes(range(256)) * 16}
    return (True, payload, {"existing.bin": b"keep prior completion"}) if collision else (
        False, {}, payload,
    )


def error_details(report):
    error = report["error"]
    if error is None:
        return None, False, None
    origin = "main" if error["original_body_error"] else "running2finished"
    message_ok = error["message"] == "original experiment failure" if error["original_body_error"] else (
        "File exists" in error["message"] and
        str(Path(report["body"]["destination"]) / "blocked") in error["message"]
    )
    return error["type"], error["original_body_error"], message_ok and origin in error["frames"]


@pytest.mark.parametrize("mode,collision,code,result,error_kind,original,error_checked,closes,outer_handler,record_exit", [
    ("ok", False, 0, 0, None, False, None, 2, False, 0),
    ("nonzero", False, 7, 7, None, False, None, 2, False, 7),
    ("raised", False, 1, None, "RuntimeError", True, True, 2, False, 1),
    ("ok", True, 1, None, "FileExistsError", False, True, 1, False, 1),
    ("nonzero", True, 1, None, "FileExistsError", False, True, 1, False, 7),
    ("raised", True, 1, None, "RuntimeError", True, True, 1, False, 1),
    pytest.param("ok", True, 1, None, "FileExistsError", False, True, 1, True, 1,
                 id="cleanup_inside_outer_handler"),
])
def test_decorator_cleanup_failure_and_body_error_precedence(
    tmp_path, mode, collision, code, result, error_kind, original,
    error_checked, closes, outer_handler, record_exit
):
    # Arrange: real script and local subscribers; no live experiment or messages.
    script = tmp_path / "cleanup_script.py"
    script.write_text(SCRIPT)
    report_path = tmp_path / "report.json"
    env = dict(os.environ, HELPER_EVENT_REPORT=str(report_path),
               HELPER_COLLISION=str(int(collision)),
               HELPER_OUTER_HANDLER=str(int(outer_handler)),
               SCITEX_SESSION_OUT_DIR=str(tmp_path / "outputs"))
    # Act: retain the owning child-interpreter60-second deadline.
    completed = subprocess.run([sys.executable, str(script), "--mode", mode],
                               cwd=tmp_path, env=env, capture_output=True,
                               text=True, timeout=60)
    (tmp_path / "stdout.log").write_text(completed.stdout)
    (tmp_path / "stderr.log").write_text(completed.stderr)
    report = json.loads(report_path.read_text())
    close_code = {"ok": 0, "nonzero": 7, "raised": 1}[mode]
    close_values = [(e["status"], e["exit_code"]) for e in report["events"]
                    if e["kind"] == "close"]
    # Assert: error/trace identity, real files, callback multiplicity and no leaked figures.
    assert (completed.returncode, report["result"], error_details(report),
            blocked_payloads(report), report["open_figures"],
            [e["kind"] for e in report["events"]], close_values,
            report["record_status"], report["record_exit_code"],
            report["record_ended"], report["still_active"]) == (
        code, result, (error_kind, original, error_checked),
        expected_payloads(collision), [], ["start", "start"] + ["close"] * closes,
        [("success" if close_code == 0 else "failed", close_code)] * closes,
        "closed", record_exit, True, False,
    )
