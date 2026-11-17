#!/usr/bin/env python3
# Timestamp: "2026-02-01 (ywatanabe)"
# File: /home/ywatanabe/proj/scitex-python/src/scitex/session/_lifecycle/_close.py
"""Session close functions."""

from __future__ import annotations

import os
import os as _os
import shutil
import time
from glob import glob as _glob
from pathlib import Path

from scitex_logging import getLogger


def scitex_utils_notify(*args, **kwargs):
    """Best-effort notify shim. Calls ``scitex.utils._notify.notify`` when
    the umbrella ``scitex`` package is installed; otherwise silently no-ops
    so session lifecycle keeps working in standalone mode.
    """
    try:
        from scitex.utils._notify import notify as _notify
    except ImportError:
        return None
    return _notify(*args, **kwargs)


from .._manager import get_global_session_manager
from ._config import save_configs
from ._io_scope import close_io_scope
from ._utils import args_to_str, escape_ansi_from_log_files, process_timestamp

logger = getLogger(__name__)


def finished_run_dir(sdir_run, exit_status=None) -> str:
    """Final location of a run directory ``.../RUNNING/<ID>/`` for a status.

    ``0`` -> ``FINISHED_SUCCESS``, ``1`` -> ``FINISHED_ERROR``, anything else
    -> ``FINISHED``.
    """
    if exit_status == 0:
        status_dir = "FINISHED_SUCCESS/"
    elif exit_status == 1:
        status_dir = "FINISHED_ERROR/"
    else:
        status_dir = "FINISHED/"
    run = str(sdir_run)
    if not run.endswith("/"):
        run += "/"
    return run.replace("RUNNING/", status_dir)


def running2finished(
    CONFIG,
    exit_status=None,
    remove_src_dir=True,
    max_wait=60,
    archive_format=None,
):
    """Move session from RUNNING to FINISHED directory.

    The directory is renamed in place when possible (one ``rename(2)``: no
    copy, and files still open in the run — the tee'd logs — keep being
    written at their new location); it falls back to copying item by item
    when the rename is impossible (destination already exists, another
    filesystem).

    Parameters
    ----------
    CONFIG : dict
        Session configuration dictionary
    exit_status : int, optional
        Exit status code (0=success, 1=error, None=finished)
    remove_src_dir : bool, default=True
        Whether to remove source directory after copy
    max_wait : int, default=60
        Maximum seconds to wait for copy operation
    archive_format : str, optional
        If set (e.g. "tar.gz"), replace the FINISHED dest dir with a
        single archive file (1 inode instead of N). ``None`` (default)
        preserves the original copytree-only behavior bit-for-bit.

    Returns
    -------
    dict
        Updated configuration with new SDIR
    """
    dest_dir = finished_run_dir(CONFIG["SDIR_RUN"], exit_status)
    src_dir = str(CONFIG["SDIR_RUN"])
    try:
        moved = remove_src_dir and _rename_dir(src_dir, dest_dir)
        if not moved:
            _os.makedirs(dest_dir, exist_ok=True)
            # Copy files individually
            for item in _os.listdir(src_dir):
                s = _os.path.join(src_dir, item)
                d = _os.path.join(dest_dir, item)
                if _os.path.isdir(s):
                    shutil.copytree(s, d)
                else:
                    shutil.copy2(s, d)

        start_time = time.time()
        while not _os.path.exists(dest_dir) and time.time() - start_time < max_wait:
            time.sleep(0.1)
        if _os.path.exists(dest_dir):
            if exit_status == 1:
                logger.error(
                    f"Script failed: {dest_dir}",
                )
            elif exit_status == 0:
                logger.info(
                    f"Congratulations! The script completed: {dest_dir}",
                )
            else:
                logger.info(
                    f"Script finished: {dest_dir}",
                )

            if remove_src_dir and not moved:
                shutil.rmtree(src_dir)

            # Cleanup RUNNING when empty
            running_base = os.path.dirname(src_dir.rstrip("/"))
            if remove_src_dir and os.path.basename(running_base) == "RUNNING":
                try:
                    os.rmdir(running_base)
                except OSError:
                    pass

        else:
            logger.error(f"Copy operation timed out after {max_wait} seconds")

        CONFIG["SDIR_RUN"] = Path(dest_dir)

        # Opt-in: collapse the FINISHED/<session>/ dir into a single
        # FINISHED/<session>.tar.gz archive. ``archive_format=None``
        # keeps the original behavior unchanged.
        if archive_format is not None and _os.path.isdir(dest_dir):
            _archive_into(CONFIG, dest_dir, archive_format)
    except Exception as e:
        logger.error("%s", e)

    finally:
        return CONFIG


def _rename_dir(src_dir: str, dest_dir: str) -> bool:
    """Rename ``src_dir`` to ``dest_dir``; False when a rename cannot do it."""
    dest = dest_dir.rstrip("/")
    if _os.path.lexists(dest):
        return False
    _os.makedirs(_os.path.dirname(dest), exist_ok=True)
    try:
        _os.rename(src_dir.rstrip("/"), dest)
    except OSError:  # EXDEV (another filesystem) and friends: copy instead
        return False
    return True


def _archive_into(CONFIG, dest_dir: str, archive_format: str) -> None:
    try:
        from ._archive import archive_session_dir

        archive_path = archive_session_dir(
            dest_dir,
            format=archive_format,
            remove_src=True,
        )
        CONFIG["SDIR_RUN"] = Path(archive_path)
    except Exception as e:
        logger.warning(
            "archive_format=%r failed for %s: %s",
            archive_format,
            dest_dir,
            e,
        )


def close(
    CONFIG,
    message=":)",
    notify=False,
    verbose=True,
    exit_status=None,
    archive_format=None,
):
    """Close experiment session and finalize logging.

    Order matters for provenance (scitex-session#66): the run directory is
    moved from ``RUNNING/<ID>`` to its ``FINISHED_*/<ID>`` location FIRST,
    and only then are the close hooks fired — with ``relocations`` /
    ``sdir_run_final`` in their metadata — so an observer that recorded
    files under the transient ``RUNNING/<ID>`` path is told, at the moment
    it finalises the run, where they now live (on every status: success,
    error, other). The run config is saved after the hooks, in the final
    directory, so it carries their failures and the final paths.

    Parameters
    ----------
    CONFIG : DotDict
        Configuration dictionary from start()
    message : str, default=':)'
        Completion message
    notify : bool, default=False
        Whether to send notification
    verbose : bool, default=True
        Whether to print verbose output
    exit_status : int, optional
        Exit status code (0=success, 1=error, None=finished)
    archive_format : str, optional
        If set (e.g. "tar.gz"), replace the FINISHED dest dir with a
        single archive file. ``None`` (default) preserves the original
        copytree-only behavior bit-for-bit.
    """
    sys = None
    hooks_fired = False
    session_id = CONFIG.get("ID")
    try:
        CONFIG.EXIT_STATUS = exit_status
        # include_private: keep ``_PROVENANCE`` / ``_HOOK_FAILURES`` for
        # CONFIGS/CONFIG.yaml, and ``_sys`` so the tee'd log streams are
        # actually closed below (a plain to_dict() dropped every ``_`` key,
        # so ``pop("_sys")`` always returned None and the logs leaked).
        CONFIG = CONFIG.to_dict(include_private=True)
        CONFIG = process_timestamp(CONFIG, verbose=verbose)
        sys = CONFIG.pop("_sys", None)
        restore = CONFIG.pop("_sys_restore", None)

        # CRITICAL: Close matplotlib BEFORE closing streams to prevent segfault
        _cleanup_matplotlib(verbose)

        # If archive_format wasn't passed explicitly, fall back to
        # ``CONFIG.SESSION.ARCHIVE_FORMAT`` if it's been set via user
        # config. Keeps backward compat when nothing is set.
        effective_archive_format = archive_format
        if effective_archive_format is None:
            try:
                session_cfg = CONFIG.get("SESSION")
                if isinstance(session_cfg, dict):
                    effective_archive_format = session_cfg.get("ARCHIVE_FORMAT")
            except Exception:
                effective_archive_format = None

        # RUNNING to FINISHED (archiving, if any, comes after the hooks)
        running_dir = _real_dir(CONFIG["SDIR_RUN"])
        CONFIG = running2finished(CONFIG, exit_status=exit_status)
        final_dir = _real_dir(CONFIG["SDIR_RUN"])
        relocations = []
        if final_dir != running_dir and _os.path.isdir(final_dir):
            relocation = {"from": running_dir, "to": final_dir}
            if effective_archive_format:
                relocation["archive"] = _archive_path(
                    final_dir, effective_archive_format
                )
            relocations.append(relocation)

        close_metadata = _close_metadata(CONFIG, relocations, final_dir)
        hooks_fired = True
        close_failures = _stop_verification(exit_status, close_metadata)

        hook_failures = list(CONFIG.get("_HOOK_FAILURES") or []) + close_failures
        CONFIG["_HOOK_FAILURES"] = hook_failures
        if hook_failures:
            logger.warning(
                "scitex-session: %d provenance hook failure(s) in session %s — "
                "its provenance record is INCOMPLETE; details in "
                "CONFIGS/CONFIG.yaml (_HOOK_FAILURES): %s",
                len(hook_failures),
                CONFIG.get("ID"),
                "; ".join(
                    f"{f['phase']}:{f['hook']}: {f['error']}" for f in hook_failures
                ),
            )

        save_configs(CONFIG)

        if effective_archive_format is not None and _os.path.isdir(final_dir):
            _archive_into(CONFIG, final_dir, effective_archive_format)

        # ANSI code escape
        log_files = _glob(_os.path.join(str(CONFIG["SDIR_RUN"]), "logs", "*.log"))
        escape_ansi_from_log_files(log_files)

        if CONFIG.get("ARGS"):
            message += f"\n{args_to_str(CONFIG.get('ARGS'))}"

        if notify:
            try:
                message = (
                    "[DEBUG]\n" + str(message)
                    if CONFIG.get("DEBUG", False)
                    else str(message)
                )
                scitex_utils_notify(
                    message=message,
                    ID=CONFIG["ID"],
                    file=CONFIG.get("FILE"),
                    attachment_paths=log_files,
                    verbose=verbose,
                )
            except Exception as e:
                logger.error("%s", e)

        # Close session
        session_manager = get_global_session_manager()
        session_manager.close_session(
            CONFIG["ID"], exit_code=exit_status or 0, fire_hooks=False
        )

    finally:
        # Observers must always see the session end, even when closing
        # failed before the hooks were reached (their run record would
        # otherwise stay "running" forever).
        if not hooks_fired:
            _stop_verification(exit_status, None)
        close_io_scope(session_id)
        # Only close if they're custom file objects (Tee objects)
        if sys:
            _close_streams(sys)
            _restore_streams(sys, restore)


def _restore_streams(sys, restore) -> None:
    """Put back the streams and logging handlers start() replaced."""
    if not restore:
        return
    stdout, stderr = restore.get("streams") or (None, None)
    tee_out, tee_err = restore.get("tees") or (None, None)
    # only where our tee is still installed (a caller may have swapped it)
    if stdout is not None and sys.stdout is tee_out:
        sys.stdout = stdout
    if stderr is not None and sys.stderr is tee_err:
        sys.stderr = stderr
    for handler, stream in restore.get("handlers") or []:
        handler.stream = stream


def _real_dir(path) -> str:
    """Real path of a run directory, without a trailing separator."""
    return _os.path.realpath(str(path).rstrip("/"))


def _archive_path(final_dir: str, archive_format: str) -> str:
    from ._archive._core import format_to_suffix

    return final_dir + format_to_suffix(archive_format)


def _close_metadata(CONFIG, relocations, final_dir) -> dict:
    """Close-time run metadata; also folded into ``CONFIG['_PROVENANCE']``."""
    from ._provenance import collect_close_metadata

    start = CONFIG.get("_PROVENANCE") or {}
    script = CONFIG.get("FILE")
    meta = collect_close_metadata(
        start,
        script_path=str(script) if script else None,
        relocations=relocations,
        sdir_run_final=final_dir,
    )
    # scitex-io's lost-provenance tally for this run (see _io_scope.py)
    meta["io_provenance"] = close_io_scope(CONFIG.get("ID"))
    if isinstance(start, dict):
        merged = dict(start)
        merged.update(meta)
        CONFIG["_PROVENANCE"] = merged
    return meta


def _cleanup_matplotlib(verbose: bool) -> None:
    """Clean up matplotlib resources."""
    try:
        import gc

        import matplotlib
        import matplotlib.pyplot as plt

        # Close all figures
        plt.close("all")

        # CRITICAL: Unregister matplotlib's atexit handlers to prevent segfault
        try:
            if hasattr(matplotlib, "_pylab_helpers"):
                matplotlib._pylab_helpers.Gcf.destroy_all()

            if hasattr(plt, "get_fignums"):
                for fignum in plt.get_fignums():
                    plt.close(fignum)

        except Exception:
            pass

        # Force garbage collection
        gc.collect()

        if verbose:
            logger.info("Matplotlib cleanup completed")

    except Exception as e:
        if verbose:
            logger.warning(f"Could not close matplotlib: {e}")


def _close_streams(sys) -> None:
    """Close tee-wrapped streams."""
    try:
        # First, flush all outputs
        if hasattr(sys, "stdout") and hasattr(sys.stdout, "flush"):
            sys.stdout.flush()
        if hasattr(sys, "stderr") and hasattr(sys.stderr, "flush"):
            sys.stderr.flush()

        # Then close Tee objects
        if hasattr(sys, "stdout") and hasattr(sys.stdout, "_log_file"):
            sys.stdout.close()
        if hasattr(sys, "stderr") and hasattr(sys.stderr, "_log_file"):
            sys.stderr.close()
    except Exception:
        pass


def _stop_verification(exit_status: int, metadata=None) -> list:
    """Fire the session-close hooks (e.g. clew lineage).

    Dispatches via the acyclic registry; session never imports the
    subscriber. See _hooks.py. ``metadata`` (close-time run metadata, see
    :func:`_close_metadata`) reaches every hook that accepts it. Returns the
    failure records of any hook that raised (each one already logged as a
    WARNING) — never swallowed.
    """
    from .._hooks import _fire_session_close_hooks

    status = "success" if exit_status == 0 else "failed"
    return _fire_session_close_hooks(
        status=status, exit_code=exit_status or 0, metadata=metadata
    )


# EOF
