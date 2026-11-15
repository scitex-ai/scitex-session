"""Real manual lifecycle close defaults, observer values and retained output."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = r'''import json, os, sys
from pathlib import Path
import scitex_session as sess
from scitex_session import _hooks
from scitex_session._manager import get_global_session_manager

assert not _hooks._SESSION_START_HOOKS and not _hooks._SESSION_CLOSE_HOOKS
events = []
def on_start(session_id, script_path, metadata):
    events.append({"kind": "start"})
def on_close(status, exit_code):
    events.append({"kind": "close", "status": status, "exit_code": exit_code})
sess.register_session_start_hook(on_start)
sess.register_session_close_hook(on_close)
saved_stdout, saved_stderr = sys.stdout, sys.stderr
mode = os.environ["HELPER_CLOSE_MODE"]
CONFIG, *_ = sess._start(sys, file=__file__, verbose=False, agg=True)
run_dir = str(CONFIG["SDIR_RUN"])
Path(run_dir, "owned-payload.txt").write_text("manual lifecycle payload")
try:
    kwargs = {} if mode == "default" else {"exit_status": int(mode)}
    sess.close(CONFIG, notify=False, verbose=False, **kwargs)
finally:
    sys.stdout, sys.stderr = saved_stdout, saved_stderr
    record = get_global_session_manager().get_session(str(CONFIG["ID"]))
    payload = {"events": events, "run_dir": run_dir,
               "config_exit_status": CONFIG["EXIT_STATUS"],
               "record_status": record.get("status"),
               "record_exit_code": record.get("exit_code")}
    Path(os.environ["HELPER_EVENT_REPORT"]).write_text(json.dumps(payload, default=str))
    _hooks.unregister_session_start_hook(on_start)
    _hooks.unregister_session_close_hook(on_close)
'''


@pytest.mark.parametrize("mode,code,config_status,destination", [
    ("default", 0, None, "FINISHED"),
    ("0", 0, 0, "FINISHED_SUCCESS"),
    ("7", 7, 7, "FINISHED_ERROR"),
])
def test_manual_close_events_match_record_and_preserve_output(
    tmp_path, mode, code, config_status, destination
):
    # Arrange: genuine lifecycle, local subscribers and owned output.
    script = tmp_path / "manual_script.py"
    script.write_text(SCRIPT)
    report_path = tmp_path / "report.json"
    env = dict(os.environ, HELPER_EVENT_REPORT=str(report_path),
               HELPER_CLOSE_MODE=mode,
               SCITEX_SESSION_OUT_DIR=str(tmp_path / "outputs"))
    # Act: preserve the owning integration subprocess deadline.
    completed = subprocess.run([sys.executable, str(script)], cwd=tmp_path,
                               env=env, capture_output=True, text=True, timeout=60)
    (tmp_path / "stdout.log").write_text(completed.stdout)
    (tmp_path / "stderr.log").write_text(completed.stderr)
    report = json.loads(report_path.read_text())
    finished = Path(report["run_dir"].replace("RUNNING/", destination + "/"))
    close_values = [(e["status"], e["exit_code"]) for e in report["events"]
                    if e["kind"] == "close"]
    # Assert: both dispatches agree; neutral output/config semantics survive.
    assert (completed.returncode, report["record_status"],
            report["record_exit_code"], report["config_exit_status"],
            [e["kind"] for e in report["events"]], close_values,
            Path(report["run_dir"]).exists(),
            (finished / "owned-payload.txt").read_text()) == (
                0, "closed", code, config_status,
                ["start", "start", "close", "close"],
                [("success" if code == 0 else "failed", code)] * 2,
                False, "manual lifecycle payload",
            )
