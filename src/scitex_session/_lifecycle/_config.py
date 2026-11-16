#!/usr/bin/env python3
# Timestamp: "2026-02-01 (ywatanabe)"
# File: /home/ywatanabe/proj/scitex-python/src/scitex/session/_lifecycle/_config.py
"""Configuration setup for session lifecycle."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from scitex_logging import getLogger

logger = getLogger(__name__)


def derive_sdir_out(sdir) -> Optional[Path]:
    """Base output directory for a run directory ``<base>/RUNNING/<ID>/``.

    Returns ``<base>`` (``SDIR_OUT``), or the parent when the run directory
    has no ``RUNNING`` segment, or ``None`` when ``sdir`` is empty.
    """
    if not sdir:
        return None
    parts = Path(sdir).parts
    if "RUNNING" in parts:
        return Path(*parts[: parts.index("RUNNING")])
    return Path(sdir).parent


def setup_configs(
    IS_DEBUG: bool,
    ID: str,
    PID: int,
    file: str,
    sdir: str,
    relative_sdir: str,
    verbose: bool,
) -> Dict[str, Any]:
    """Setup configuration dictionary with basic parameters.

    Parameters
    ----------
    IS_DEBUG : bool
        Debug mode flag
    ID : str
        Unique identifier
    PID : int
        Process ID
    file : str
        File path
    sdir : str
        Save directory path
    relative_sdir : str
        Relative save directory path
    verbose : bool
        Verbosity flag

    Returns
    -------
    dict
        Configuration dictionary
    """
    sdir_path = Path(sdir) if sdir else None
    sdir_out = derive_sdir_out(sdir)

    # Load YAML configs from ./config/*.yaml. Each file is read through
    # ``scitex_io.load``, whose post-load hook lets a provenance observer
    # (clew) record it as a run INPUT — start() opens the provenance record
    # before calling us precisely so these reads are captured.
    from scitex_io import load_configs

    CONFIGS = load_configs(IS_DEBUG).to_dict()

    # Add session-specific config with clean structure (Path objects only)
    CONFIGS.update(
        {
            "ID": ID,
            "PID": PID,
            "START_DATETIME": datetime.now(),
            "FILE": Path(file) if file else None,
            "SDIR_OUT": sdir_out,
            "SDIR_RUN": sdir_path,
        }
    )
    return CONFIGS


def save_configs(CONFIG) -> None:
    """Save configuration to files.

    Note: track=False prevents verification tracking of CONFIG files,
    which would cause false "missing" errors since these files are saved
    in RUNNING/ but then moved to FINISHED_SUCCESS/.
    """
    from scitex_io import save as scitex_io_save

    # Convert to dict with all keys (including private ones) for saving
    config_dict = (
        CONFIG.to_dict(include_private=True) if hasattr(CONFIG, "to_dict") else CONFIG
    )

    # track=False: Don't track internal config files in verification DB
    scitex_io_save(
        config_dict,
        str(CONFIG["SDIR_RUN"] / "CONFIGS/CONFIG.pkl"),
        verbose=False,
        track=False,
    )
    scitex_io_save(
        config_dict,
        str(CONFIG["SDIR_RUN"] / "CONFIGS/CONFIG.yaml"),
        verbose=False,
        track=False,
    )


# EOF
