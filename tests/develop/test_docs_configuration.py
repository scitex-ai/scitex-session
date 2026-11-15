"""Docs configuration must identify its checkout without importing runtime code."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("launch_from_conf_dir", [False, True])
def test_documented_version_belongs_to_selected_checkout(tmp_path, launch_from_conf_dir):
    # A distinct checkout version must win over an installed Session release.
    checkout = tmp_path / "selected checkout"
    conf_dir = checkout / "docs/sphinx"
    conf_dir.mkdir(parents=True)
    (checkout / "src").mkdir()
    conf = conf_dir / "conf.py"
    conf.write_bytes((ROOT / "docs/sphinx/conf.py").read_bytes())
    (checkout / "pyproject.toml").write_text('[project]\nversion = "7.8.9"\n')
    launch_dir = conf_dir if launch_from_conf_dir else tmp_path
    code = (
        "import json,runpy,sys; "
        "config=runpy.run_path(sys.argv[1]); "
        "print(json.dumps({'release':config['release'], 'source':sys.path[0], "
        "'runtime_imported':any(n=='scitex_session' or n.startswith('scitex_session.') "
        "for n in sys.modules)}))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(conf)],
        cwd=launch_dir, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "release": "7.8.9", "source": str(checkout / "src"),
        "runtime_imported": False,
    }
