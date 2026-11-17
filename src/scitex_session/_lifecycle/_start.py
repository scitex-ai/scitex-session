#!/usr/bin/env python3
# Timestamp: "2026-02-01 (ywatanabe)"
# File: /home/ywatanabe/proj/scitex-python/src/scitex/session/_lifecycle/_start.py
"""Session start function."""

from __future__ import annotations

import os as _os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from scitex_dev import try_import_optional
from scitex_dict import DotDict
from scitex_logging import getLogger
from scitex_repro import RandomStateManager
from scitex_str import clean_path

from .._hooks import _fire_session_close_hooks, _fire_session_start_hooks
from .._manager import get_global_session_manager
from ._caller import (  # noqa: F401 - re-exported (tests, _decorator)
    _INTERNAL_MODULE_PREFIXES,
    _cli_argv,
    _find_user_caller,
    _find_user_caller_file,
    _is_internal_frame_file,
    _is_internal_module_name,
    _is_own_package_file,
)
from ._config import derive_sdir_out, setup_configs
from ._entry import detect_entry
from ._io_scope import close_io_scope, open_io_scope
from ._matplotlib import setup_matplotlib
from ._provenance import collect_run_metadata
from ._utils import (
    clear_python_log_dir,
    get_debug_mode,
    initialize_env,
    print_header,
    simplify_relative_path,
)

logger = getLogger(__name__)

# For development code flow analysis
analyze_code_flow = try_import_optional(
    "scitex_dev._analyze_code_flow", "analyze_code_flow"
)
if analyze_code_flow is None:

    def analyze_code_flow(file):
        return "Code flow analysis not available"


#: Output-anchor stems for sessions entered without a script file.
_ENTRY_ANCHORS = {
    "command": "python_c",
    "stdin": "python_stdin",
    "interactive": "python_interactive",
    # a kernel whose notebook could not be resolved: ``<cwd>/notebook_out/``,
    # where scitex-io saves from such a notebook too
    "notebook": "notebook",
}


def _notebook_entry(entry: Dict[str, Any], invocation: str) -> Dict[str, Any]:
    """``entry`` (kind ``notebook``) completed with the notebook's identity.

    Resolution failure is not fatal: the run is recorded without a code file
    (``path: None`` + ``path_unresolved``), and a WARNING says how to name the
    notebook. See :mod:`._notebook`.
    """
    from ._notebook import NOTEBOOK_PATH_ENV, notebook_entry, resolve_notebook

    fields = notebook_entry(resolve_notebook(), invocation)
    if fields.get("path") is None:
        logger.warning(
            "scitex-session: could not tell which notebook this kernel is running "
            "(%s); the session is recorded WITHOUT its code file and its outputs "
            "go to <cwd>/notebook_out/. Set $%s=<path to the .ipynb> for the kernel.",
            fields.get("path_unresolved"),
            NOTEBOOK_PATH_ENV,
        )
    return {**entry, **fields}


def _entry_anchor(entry: Dict[str, Any]) -> str:
    """Pseudo script path that anchors ``<stem>_out/`` for a file-less entry.

    It lives in the working directory (where the command ran and where its
    relative paths resolve); it is used ONLY to place the output tree and is
    never recorded as the run's script.
    """
    stem = _ENTRY_ANCHORS.get(entry.get("kind"), "python_session")
    return _os.path.join(_os.getcwd(), stem + ".py")


# Environment variable that redirects the *whole* session ``_out`` base
# away from the script-adjacent default. Documented in the package
# env-vars skill leaf; see :func:`_resolve_out_base_dir`.
_OUT_DIR_ENV = "SCITEX_SESSION_OUT_DIR"


def _resolve_out_base_dir(caller_file: str, ID: str) -> str:
    """Compute the session output directory ``<...>_out/RUNNING/<ID>/``.

    By default the base is placed *adjacent to the caller script*:
    ``<script_without_ext>_out/RUNNING/<ID>/``. Keeping a script's
    outputs next to the script is the right default for interactive and
    small-scale work.

    When the environment variable ``SCITEX_SESSION_OUT_DIR`` is set (and
    non-empty), the whole ``_out`` base is redirected underneath that
    directory instead::

        $SCITEX_SESSION_OUT_DIR/<script_stem>_out/RUNNING/<ID>/

    This is the knob HPC / SLURM-array workloads use to point session
    output at node-local scratch (e.g. ``/tmp`` or ``$TMPDIR``) so the
    entire session lifecycle — the RUNNING tree, the running->finished
    copytree, and any archive step — stays off a shared parallel
    filesystem (GPFS/Lustre) whose inode budget the per-run session trees
    would otherwise exhaust.

    The trailing ``RUNNING/<ID>/`` segment is preserved in both cases, so
    every downstream consumer is unaffected — ``setup_configs`` still
    strips ``RUNNING/<ID>/`` to derive ``SDIR_OUT``, ``running2finished``
    still moves the run to a ``FINISHED_*`` sibling, and archiving still
    finds the same tree. Only the parent of ``_out`` changes. ``<ID>`` is
    unique per run (timestamp to the second + a random suffix), so
    concurrent array tasks never collide even when they share the
    redirected base.
    """
    stem = _os.path.splitext(caller_file)[0]
    out_base = _os.environ.get(_OUT_DIR_ENV)
    if out_base:
        script_stem = _os.path.basename(stem)
        return clean_path(
            _os.path.join(out_base, f"{script_stem}_out", "RUNNING", ID) + "/"
        )
    return clean_path(stem + f"_out/RUNNING/{ID}/")


def start(
    sys=None,
    plt=None,
    file: Optional[str] = None,
    sdir: Optional[Union[str, Path]] = None,
    sdir_suffix: Optional[str] = None,
    args: Optional[Any] = None,
    os: Optional[Any] = None,
    random: Optional[Any] = None,
    np: Optional[Any] = None,
    torch: Optional[Any] = None,
    seed: int = 42,
    agg: bool = False,
    fig_size_mm: Tuple[int, int] = (160, 100),
    fig_scale: float = 1.0,
    dpi_display: int = 100,
    dpi_save: int = 300,
    fontsize="small",
    autolayout=False,
    show_execution_flow=False,
    hide_top_right_spines: bool = True,
    alpha: float = 0.9,
    line_width: float = 1.0,
    clear_logs: bool = False,
    verbose: bool = True,
    project_root: Optional[Union[str, Path]] = None,
) -> Tuple[DotDict, Any, Any, Any, Optional[Dict[str, Any]], Any]:
    """Initialize experiment session with reproducibility settings.

    .. note::
       **INTERNAL / advanced.** This is the low-level lifecycle the
       ``@scitex_session.session`` decorator orchestrates — prefer the
       decorator. Note the signature is ``start(sys, plt, ...)``; it is
       *not* a decorator (``@scitex_session.start`` would bind your
       function to the ``sys`` parameter). Reach it as
       ``scitex_session._start`` for power-user access; the bare
       ``scitex_session.start`` name is deprecated.

    Parameters
    ----------
    sys : module, optional
        Python sys module for I/O redirection
    plt : module, optional
        Matplotlib pyplot module for plotting configuration
    file : str, optional
        Script file path. If None, automatically detected
    sdir : Union[str, Path], optional
        Save directory path
    sdir_suffix : str, optional
        Suffix to append to save directory
    args : object, optional
        Command line arguments or configuration object
    seed : int, default=42
        Random seed for reproducibility
    agg : bool, default=False
        Whether to use matplotlib Agg backend
    verbose : bool, default=True
        Whether to print detailed information
    project_root : str or Path, optional
        Explicit project root (the boundary provenance observers use for
        "project-local" files). Wins over ``$SCITEX_SESSION_PROJECT_ROOT`` and
        auto-detection; see :mod:`scitex_session._lifecycle._project_root`.

    Returns
    -------
    tuple
        (CONFIGS, stdout, stderr, plt, COLORS, rng)
    """
    IS_DEBUG = get_debug_mode()
    ID, PID = initialize_env(IS_DEBUG)
    entry = detect_entry()

    # Convert Path objects to strings for internal processing
    if sdir is not None and isinstance(sdir, Path):
        sdir = str(sdir)

    # Defines SDIR
    if sdir is None:
        # Define __file__.
        #
        # The naive `inspect.stack()[1].filename` (and the analogous
        # `frame.f_back` walk in the @stx.session wrapper) lands on the
        # internal scitex_session / scitex_dev wrapper frame when the
        # user's script is wrapped through one or more decorator layers
        # (operator-observed: figures saving to
        # ``site-packages/.../decorators_out/`` instead of
        # ``<user_script>_out/``). Walk the stack OUTWARD to the first
        # frame whose filename is NOT inside a scitex_session /
        # scitex_dev source path — that's the real user script. The
        # ``file=`` parameter (when supplied by a callsite that already
        # knows the user's path, e.g. notebook integrations) is used as
        # a hint, but if it still points at an internal scitex frame
        # we override it with the walked result.
        caller_file, invocation = _find_user_caller(hint=file)
        script_path = caller_file
        if entry.get("kind") == "notebook":
            # A Jupyter kernel: the code is the notebook (see _notebook.py).
            entry = _notebook_entry(entry, invocation or "cell")
            if invocation in (None, "cell"):
                script_path = caller_file = entry.get("path")

        if caller_file is None:
            # ``python -c`` / stdin / REPL: no script file to record.
            caller_file = _entry_anchor(entry)

        # Convert to absolute path if relative and resolve symlinks
        caller_file = _os.path.realpath(_os.path.abspath(caller_file))
        if script_path is not None:
            script_path = _os.path.realpath(_os.path.abspath(script_path))

        # Define sdir (honours $SCITEX_SESSION_OUT_DIR redirect)
        sdir = _resolve_out_base_dir(caller_file, ID)

        # Optional
        if sdir_suffix:
            sdir = sdir[:-1] + f"-{sdir_suffix}/"
    else:
        caller_file = file
        script_path = file
        if entry.get("kind") == "notebook":
            entry = _notebook_entry(entry, "file" if file else "cell")

    if clear_logs and caller_file:
        clear_python_log_dir(sdir + caller_file + "/")
    _os.makedirs(sdir, exist_ok=True)
    relative_sdir = simplify_relative_path(sdir)

    # Logging (needs only ``sdir``; set up first so provenance warnings
    # below land in the run's own logs).
    if sys is not None:
        from scitex_io._flush import flush
        from scitex_logging import tee

        flush(sys)
        # What close() puts back: a process that outlives the session (a
        # notebook kernel) must not keep writing through the closed tee — in
        # ipykernel that silently routed every later cell's output to the
        # finished cell (the tee has no ``set_parent``).
        restore = {"streams": (sys.stdout, sys.stderr)}
        sys.stdout, sys.stderr = tee(sys, sdir=sdir, verbose=verbose)
        restore["tees"] = (sys.stdout, sys.stderr)

        # Redirect logging handlers to use the tee-wrapped streams
        restore["handlers"] = _redirect_logging_handlers(sys)

    # Open the provenance record BEFORE loading ./config/*.yaml: config files
    # determine results, and they are read through ``scitex_io.load`` whose
    # post-load hook records each one as a run INPUT — but only while an
    # observer's run record is open.
    metadata = collect_run_metadata(
        script_path,
        entry=entry,
        args=args,
        seed=seed,
        sdir_run=sdir,
        sdir_out=derive_sdir_out(sdir),
        project_root=project_root,
    )
    hook_failures = _start_verification(ID, script_path, metadata)
    # The run's recording window for scitex-io: its load/save hook payloads
    # carry ``in_scope`` and its lost-provenance events are tallied per run
    # (reported at close as ``io_provenance``).
    open_io_scope(ID)

    # Setup configs - use caller_file (computed) instead of file (parameter)
    try:
        CONFIGS = setup_configs(
            IS_DEBUG, ID, PID, script_path, sdir, relative_sdir, verbose
        )
    except BaseException:
        # The run record is open; close it as failed instead of leaving it
        # dangling in "running" state, then let the config error propagate.
        _fire_session_close_hooks(status="failed", exit_code=1)
        close_io_scope(ID)
        raise

    if sys is not None:
        CONFIGS["_sys"] = sys
        CONFIGS["_sys_restore"] = restore
    CONFIGS["_PROVENANCE"] = metadata
    CONFIGS["_HOOK_FAILURES"] = hook_failures

    # Initialize RandomStateManager
    rng = RandomStateManager(seed=seed, verbose=verbose)
    if verbose:
        logger.info(f"Initialized RandomStateManager with seed {seed}")

    # Matplotlib configurations
    plt, COLORS = setup_matplotlib(
        plt,
        agg,
        fig_size_mm=fig_size_mm,
        fig_scale=fig_scale,
        dpi_display=dpi_display,
        dpi_save=dpi_save,
        hide_top_right_spines=hide_top_right_spines,
        alpha=alpha,
        line_width=line_width,
        fontsize=fontsize,
        autolayout=autolayout,
        verbose=verbose,
    )

    # Adds argument-parsed variables
    if args is not None:
        CONFIGS["ARGS"] = vars(args) if hasattr(args, "__dict__") else args

    CONFIGS = DotDict(CONFIGS)

    # Register session
    session_manager = get_global_session_manager()
    # Hooks already fired above (before config loading); do not re-fire.
    session_manager.create_session(
        ID, CONFIGS, script_path=script_path, fire_hooks=False
    )

    print_header(ID, PID, script_path or f"<{entry['kind']}>", args, CONFIGS, verbose)

    if show_execution_flow and script_path:
        from scitex_str import printc as _printc

        structure = analyze_code_flow(script_path)
        _printc(structure)

    # Return appropriate values based on whether sys was provided
    if sys is not None:
        return CONFIGS, sys.stdout, sys.stderr, plt, COLORS, rng
    else:
        return CONFIGS, None, None, plt, COLORS, rng


def _redirect_logging_handlers(sys) -> list:
    """Point StreamHandlers at the tee-wrapped streams.

    Returns ``[(handler, previous stream)]`` so close() can put them back.
    """
    import logging as _logging

    changed = []
    handlers = [getLogger().handlers]
    for name in list(_logging.Logger.manager.loggerDict.keys()):
        try:
            handlers.append(getLogger(name).handlers)
        except Exception:  # noqa: BLE001 - a broken logger is not ours to fix
            continue
    seen = set()
    for group in handlers:
        for handler in group:
            if id(handler) in seen or not isinstance(handler, _logging.StreamHandler):
                continue
            seen.add(id(handler))
            stream = getattr(handler, "stream", None)
            if stream is sys.__stderr__:
                changed.append((handler, stream))
                handler.stream = sys.stderr
            elif stream is sys.__stdout__:
                changed.append((handler, stream))
                handler.stream = sys.stdout
    return changed


def _start_verification(
    session_id: str, script_path: Optional[str], metadata: Dict[str, Any]
) -> List[Dict[str, str]]:
    """Fire the session-start hooks (e.g. clew lineage) with re-run metadata.

    Dispatches via the acyclic registry; session never imports the
    subscriber. See _hooks.py. Returns the failure records of any hook that
    raised (each one already logged as a WARNING) — never swallowed.
    """
    return _fire_session_start_hooks(
        session_id,
        script_path=str(script_path) if script_path is not None else None,
        metadata=metadata,
    )


# EOF
