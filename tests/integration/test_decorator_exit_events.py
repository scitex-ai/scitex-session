"""Exercise real decorated processes and terminal subscriber/manager state."""
import json
import os
import subprocess
import sys

import pytest


SCRIPT = r'''import json, os, sys
from pathlib import Path
import scitex_session as sess
from scitex_session import _hooks
from scitex_session._manager import get_global_session_manager

assert not _hooks._SESSION_START_HOOKS and not _hooks._SESSION_CLOSE_HOOKS
events = []
body = {}
def on_start(session_id, script_path, metadata):
    events.append({"kind": "start", "session_id": str(session_id),
                   "script_path": script_path, "metadata": metadata})
def on_close(status, exit_code):
    events.append({"kind": "close", "status": status, "exit_code": exit_code})
sess.register_session_start_hook(on_start)
sess.register_session_close_hook(on_close)

@sess.session(notify=False, verbose=False, agg=True)
def main(mode: str = "success"):
    body["session_id"] = str(CONFIG["ID"])
    body["run_dir"] = str(CONFIG["SDIR_RUN"])
    if mode == "raised_error":
        raise RuntimeError("owned body failure")
    return 7 if mode == "returned_error" else 0

saved_stdout, saved_stderr = sys.stdout, sys.stderr
result, error, exit_code = None, None, 1
try:
    result = main()
    exit_code = result if isinstance(result, int) else 0
except Exception as caught:
    error = {"type": type(caught).__name__, "message": str(caught)}
finally:
    sys.stdout, sys.stderr = saved_stdout, saved_stderr
    record = get_global_session_manager().get_session(body.get("session_id"))
    payload = {"events": events, "body": body, "result": result, "error": error,
               "record_status": record.get("status"),
               "record_exit_code": record.get("exit_code")}
    Path(os.environ["HELPER_EVENT_REPORT"]).write_text(json.dumps(payload, default=str))
    _hooks.unregister_session_start_hook(on_start)
    _hooks.unregister_session_close_hook(on_close)
raise SystemExit(exit_code)
'''


@pytest.mark.parametrize("mode,code,error", [
    ("success", 0, None),
    ("returned_error", 7, None),
    ("raised_error", 1, {"type": "RuntimeError", "message": "owned body failure"}),
])
def test_decorated_terminal_events_and_record_keep_actual_exit(tmp_path, mode, code, error):
    # Arrange: real script, private output tree and local-only subscribers.
    script = tmp_path / "event_script.py"
    script.write_text(SCRIPT)
    report_path = tmp_path / "report.json"
    env = dict(os.environ, HELPER_EVENT_REPORT=str(report_path),
               SCITEX_SESSION_OUT_DIR=str(tmp_path / "outputs"))
    # Act: the same 60-second bound as the owning example subprocess tests.
    completed = subprocess.run([sys.executable, str(script), "--mode", mode],
                              cwd=tmp_path, env=env, capture_output=True,
                              text=True, timeout=60)
    (tmp_path / "stdout.log").write_text(completed.stdout)
    (tmp_path / "stderr.log").write_text(completed.stderr)
    report = json.loads(report_path.read_text())
    close_values = {(e["status"], e["exit_code"]) for e in report["events"]
                    if e["kind"] == "close"}
    # Assert: every emitted close value and stored exit code must agree.
    assert (completed.returncode, report["error"], report["record_status"],
            report["record_exit_code"], close_values) == (
                code, error, "closed", code,
                {("success" if code == 0 else "failed", code)},
            )
