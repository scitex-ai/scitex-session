#!/usr/bin/env python3
# File: ./tests/scitex_session/_lifecycle/test__entry.py

"""How the interpreter was entered (scitex-session#65).

``detect_entry`` takes the interpreter state as arguments; the tests pass
real values in the shapes CPython produces (and one real ``python -c``
subprocess), no mocks.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import scitex_session
from scitex_session._lifecycle._entry import MAX_COMMAND_CHARS, detect_entry

_SRC = str(Path(scitex_session.__file__).resolve().parent.parent)


def _main(file=None, spec_name=None):
    module = types.ModuleType("__main__")
    if file is not None:
        module.__file__ = file
    module.__spec__ = (
        types.SimpleNamespace(name=spec_name) if spec_name is not None else None
    )
    return module


class TestCommandEntry:
    def test_kind_is_command(self):
        # Arrange
        argv, orig = ["-c"], ["python", "-c", "print(1)"]
        # Act
        entry = detect_entry(main_module=_main(), argv=argv, orig_argv=orig)
        # Assert
        assert entry["kind"] == "command"

    def test_command_text_is_recovered_after_combined_options(self):
        # Arrange
        argv, orig = ["-c", "a1"], ["python", "-Bc", "print(2)", "a1"]
        # Act
        entry = detect_entry(main_module=_main(), argv=argv, orig_argv=orig)
        # Assert
        assert entry["command"] == "print(2)"

    def test_command_hash_is_sha256_of_the_text(self):
        # Arrange
        argv, orig = ["-c"], ["python", "-c", "x = 1"]
        # Act
        entry = detect_entry(main_module=_main(), argv=argv, orig_argv=orig)
        # Assert
        assert entry["command_sha256"] == hashlib.sha256(b"x = 1").hexdigest()

    def test_long_command_is_identified_by_hash_only(self):
        # Arrange
        text = "#" * (MAX_COMMAND_CHARS + 1)
        # Act
        entry = detect_entry(
            main_module=_main(), argv=["-c"], orig_argv=["python", "-c", text]
        )
        # Assert
        assert ("command" in entry, entry["command_length"]) == (False, len(text))

    def test_real_python_c_process_is_a_command_entry(self, tmp_path):
        # Arrange
        code = (
            "import json; from scitex_session._lifecycle._entry import "
            "detect_entry; print(json.dumps(detect_entry()))"
        )
        env = dict(
            os.environ,
            PYTHONPATH=os.pathsep.join(
                p for p in (_SRC, os.environ.get("PYTHONPATH")) if p
            ),
        )
        # Act
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
            timeout=120,
        )
        entry = json.loads(out.stdout.strip().splitlines()[-1])
        # Assert
        assert (entry["kind"], entry.get("command")) == ("command", code)


class TestFileEntries:
    def test_existing_main_file_is_a_script(self, tmp_path):
        # Arrange
        script = tmp_path / "run.py"
        script.write_text("")
        # Act
        entry = detect_entry(
            main_module=_main(str(script)), argv=[str(script)], orig_argv=None
        )
        # Assert
        assert entry == {"kind": "script", "path": os.path.realpath(script)}

    def test_dash_m_is_a_module_entry(self, tmp_path):
        # Arrange
        mod = tmp_path / "pkg" / "run.py"
        mod.parent.mkdir()
        mod.write_text("")
        # Act
        entry = detect_entry(
            main_module=_main(str(mod), spec_name="pkg.run"),
            argv=[str(mod)],
            orig_argv=None,
        )
        # Assert
        assert (entry["kind"], entry["module"]) == ("module", "pkg.run")


class TestFilelessEntries:
    def test_stdin_program_is_a_stdin_entry(self):
        # Arrange
        # Act
        entry = detect_entry(
            main_module=_main(), argv=["-"], orig_argv=None, interactive=False
        )
        # Assert
        assert entry == {"kind": "stdin"}

    def test_repl_is_an_interactive_entry(self):
        # Arrange
        # Act
        entry = detect_entry(
            main_module=_main(), argv=[""], orig_argv=None, interactive=True
        )
        # Assert
        assert entry == {"kind": "interactive"}


# EOF
