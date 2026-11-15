"""Real callable shapes and parser dispatch; bodies must execute exactly once."""
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

shape = os.environ["HELPER_SHAPE"]
parser_mode = os.environ["HELPER_PARSER"]
mode = os.environ["HELPER_MODE"]
collision = os.environ["HELPER_COLLISION"] == "1"
body = {"calls": 0, "namespace": None}
events = []
original_body_error = (TypeError("owned body TypeError") if mode == "type_error"
                       else RuntimeError("owned body RuntimeError"))
def on_start(session_id, script_path, metadata):
    events.append({"kind": "start"})
def on_close(status, exit_code):
    events.append({"kind": "close", "status": status, "exit_code": exit_code})
sess.register_session_start_hook(on_start)
sess.register_session_close_hook(on_close)

def body_work(namespace=None):
    body["calls"] += 1
    body["namespace"] = None if namespace is None else vars(namespace)
    record = next(iter(get_global_session_manager().get_active_sessions().values()))
    config = record["config"]
    source = Path(config["SDIR_RUN"])
    code = 7 if mode == "nonzero" else (1 if mode in ("raised", "type_error") else 0)
    state = "FINISHED_SUCCESS" if code == 0 else "FINISHED_ERROR"
    destination = Path(str(source).replace("RUNNING/", state + "/"))
    body.update(session_id=str(config["ID"]), source=str(source), destination=str(destination))
    (source / "blocked").mkdir()
    (source / "blocked/payload.bin").write_bytes(bytes(range(256)) * 16)
    import matplotlib.pyplot as body_plt
    body_plt.subplots()
    if collision:
        (destination / "blocked").mkdir(parents=True)
        (destination / "blocked/existing.bin").write_bytes(b"keep prior completion")
    if mode in ("raised", "type_error"):
        raise original_body_error
    return code

if shape == "noargs":
    def main():
        return body_work()
else:
    def main(args):
        return body_work(args)

def parse_custom():
    parser = argparse.ArgumentParser()
    parser.add_argument("--marker", default="custom")
    return parser.parse_args()

saved_stdout, saved_stderr = sys.stdout, sys.stderr
result, error, exit_code = None, None, 1
try:
    parser = None if parser_mode == "auto" else parse_custom
    result = sess._run(main, parse_args=parser, verbose=False, agg=True,
                       sdir_suffix="dispatch")
    exit_code = result if isinstance(result, int) else 0
except SystemExit as caught:
    exit_code = caught.code if isinstance(caught.code, int) else 1
    error = {"type": type(caught).__name__, "message": str(caught),
             "original_body_error": False,
             "frames": [f.name for f in traceback.extract_tb(caught.__traceback__)]}
except Exception as caught:
    error = {"type": type(caught).__name__, "message": str(caught),
             "original_body_error": caught is original_body_error,
             "frames": [f.name for f in traceback.extract_tb(caught.__traceback__)]}
finally:
    sys.stdout, sys.stderr = saved_stdout, saved_stderr
    import matplotlib.pyplot as final_plt
    manager = get_global_session_manager()
    records = manager.list_sessions()
    record = next(iter(records.values())) if records else {}
    if record:
        config = record["config"]
        body.setdefault("session_id", str(config["ID"]))
        body.setdefault("source", str(Path(config["SDIR_RUN"])))
        planned = 7 if mode == "nonzero" else (1 if mode in ("raised", "type_error") else 0)
        state = "FINISHED_SUCCESS" if planned == 0 else "FINISHED_ERROR"
        body.setdefault("destination", body["source"].replace("RUNNING/", state + "/"))
    payload = {"body": body, "result": result, "error": error, "events": events,
               "open_figures": final_plt.get_fignums(), "record_count": len(records),
               "record_status": record.get("status"),
               "record_exit_code": record.get("exit_code"),
               "record_ended": record.get("end_time") is not None,
               "still_active": bool(manager.get_active_sessions())}
    Path(os.environ["HELPER_EVENT_REPORT"]).write_text(json.dumps(payload, default=str))
    _hooks.unregister_session_start_hook(on_start)
    _hooks.unregister_session_close_hook(on_close)
raise SystemExit(exit_code)
'''


def run_case(tmp_path, shape, parser_mode, mode, collision, argv):
    script = tmp_path / "dispatch_script.py"
    script.write_text(SCRIPT)
    report_path = tmp_path / "report.json"
    env = dict(os.environ, HELPER_EVENT_REPORT=str(report_path),
               HELPER_SHAPE=shape, HELPER_PARSER=parser_mode, HELPER_MODE=mode,
               HELPER_COLLISION=str(int(collision)),
               SCITEX_SESSION_OUT_DIR=str(tmp_path / "outputs"))
    completed = subprocess.run([sys.executable, str(script), *argv],
                               cwd=tmp_path, env=env, capture_output=True,
                               text=True, timeout=60)
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


def error_details(report):
    error = report["error"]
    if error is None:
        return None, False, None
    if error["original_body_error"]:
        return error["type"], True, "main" in error["frames"] and "body_work" in error["frames"]
    destination = Path(report["body"]["destination"]) / "blocked"
    return error["type"], False, ("File exists" in error["message"] and
                                 str(destination) in error["message"] and
                                 "running2finished" in error["frames"])


@pytest.mark.parametrize("shape,parser_mode,mode,collision,argv,namespace,code,result,error_kind,original,checked,record_exit,closes", [
    ("namespace", "custom", "ok", False, [], {"marker": "custom"},
     0, 0, None, False, None, 0, 2),
    ("namespace", "auto", "nonzero", False, ["--args", "automatic"],
     {"args": "automatic"}, 7, 7, None, False, None, 7, 2),
    ("noargs", "auto", "ok", False, [], None, 0, 0, None, False, None, 0, 2),
    ("noargs", "custom", "ok", False, [], None, 0, 0, None, False, None, 0, 2),
    ("noargs", "auto", "raised", True, [], None,
     1, None, "RuntimeError", True, True, 1, 1),
    ("noargs", "custom", "nonzero", True, [], None,
     1, None, "FileExistsError", False, True, 7, 1),
    ("namespace", "custom", "type_error", True, [], {"marker": "custom"},
     1, None, "TypeError", True, True, 1, 1),
])
def test_callable_shape_executes_once_with_expected_parser_contract(
    tmp_path, shape, parser_mode, mode, collision, argv, namespace, code,
    result, error_kind, original, checked, record_exit, closes
):
    # Arrange: zero-parameter or Namespace body, real parser and owned files.
    requested = {"ok": 0, "nonzero": 7, "raised": 1, "type_error": 1}[mode]
    # Act: actual advanced API in a child, with declared startup-only options.
    completed, report = run_case(tmp_path, shape, parser_mode, mode, collision, argv)
    close_values = [(e["status"], e["exit_code"]) for e in report["events"]
                    if e["kind"] == "close"]
    # Assert: exactly-once dispatch, namespace identity/data contract and full lifecycle.
    assert (report["body"]["calls"], report["body"]["namespace"],
            completed.returncode, report["result"], error_details(report),
            files_after(report), report["open_figures"], report["record_count"],
            report["record_status"], report["record_exit_code"],
            report["record_ended"], report["still_active"],
            [e["kind"] for e in report["events"]], close_values) == (
        1, namespace, code, result, (error_kind, original, checked),
        expected_files(collision), [], 1, "closed", record_exit, True, False,
        ["start", "start"] + ["close"] * closes,
        [("success" if requested == 0 else "failed", requested)] * closes,
    )


def test_automatic_parser_rejects_unknown_cli_before_session_start(tmp_path):
    # Arrange: no-argument callable with an unknown automatic CLI option.
    output_root = tmp_path / "outputs"
    # Act: use the real generated ArgumentParser.
    completed, report = run_case(tmp_path, "noargs", "auto", "ok", False,
                                 ["--definitely-unknown"])
    # Assert: parser SystemExit2 with no body, hooks, records, figures or experiment output.
    assert (completed.returncode, report["error"]["type"], report["error"]["message"],
            report["body"]["calls"], report["events"], report["record_count"],
            report["still_active"], report["open_figures"], output_root.exists()) == (
        2, "SystemExit", "2", 0, [], 0, False, [], False,
    )
