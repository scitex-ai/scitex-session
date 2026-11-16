#!/usr/bin/env python3
# Timestamp: "2025-08-21 20:36:50 (ywatanabe)"
# File: /home/ywatanabe/proj/SciTeX-Code/src/scitex/session/_manager.py
# ----------------------------------------
from __future__ import annotations

import os

__FILE__ = __file__
__DIR__ = os.path.dirname(__FILE__)
# ----------------------------------------

"""Session manager for tracking active experiment sessions."""

from datetime import datetime
from typing import Any, Dict, List

from ._hooks import _fire_session_close_hooks, _fire_session_start_hooks


class SessionManager:
    """Manages experiment sessions with tracking and lifecycle management."""

    def __init__(self):
        self.active_sessions = {}

    def create_session(
        self,
        session_id: str,
        config: Dict[str, Any],
        script_path: str = None,
        fire_hooks: bool = True,
    ) -> List[Dict[str, str]]:
        """Register a new session.

        Parameters
        ----------
        session_id : str
            Unique identifier for the session
        config : Dict[str, Any]
            Session configuration dictionary
        script_path : str, optional
            Path to the script being run
        fire_hooks : bool, default True
            Fire the session-start hooks. The ``@session`` lifecycle passes
            ``False`` because it fires them itself (earlier, with re-run
            metadata); firing twice re-opened the observer's run record
            without its script path or metadata.

        Returns
        -------
        list of dict
            Failure records of start hooks that raised (each also warned).
        """
        self.active_sessions[session_id] = {
            "config": config,
            "start_time": datetime.now(),
            "status": "running",
            "script_path": script_path,
        }

        # Notify lifecycle subscribers of session start (e.g. clew lineage).
        # session never imports the subscriber; failures are warned and
        # returned by the registry. See _hooks.py.
        if not fire_hooks:
            return []
        return _fire_session_start_hooks(session_id, script_path=script_path)

    def close_session(
        self,
        session_id: str,
        status: str = "success",
        exit_code: int = 0,
        fire_hooks: bool = True,
    ) -> List[Dict[str, str]]:
        """Mark a session as closed.

        Parameters
        ----------
        session_id : str
            Unique identifier for the session to close
        status : str, optional
            Final status (success, failed, error)
        exit_code : int, optional
            Exit code of the session
        fire_hooks : bool, default True
            Fire the session-close hooks (the lifecycle passes ``False``; it
            fires them itself with the real exit status).

        Returns
        -------
        list of dict
            Failure records of close hooks that raised (each also warned).
        """
        if session_id in self.active_sessions:
            self.active_sessions[session_id]["status"] = "closed"
            self.active_sessions[session_id]["end_time"] = datetime.now()
            self.active_sessions[session_id]["exit_code"] = exit_code

        # Notify lifecycle subscribers of session close (e.g. clew lineage).
        if not fire_hooks:
            return []
        return _fire_session_close_hooks(status=status, exit_code=exit_code)

    def get_active_sessions(self) -> Dict[str, Any]:
        """Get all active sessions.

        Returns
        -------
        Dict[str, Any]
            Dictionary of active session information
        """
        return {
            k: v for k, v in self.active_sessions.items() if v["status"] == "running"
        }

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """Get specific session information.

        Parameters
        ----------
        session_id : str
            Session ID to retrieve

        Returns
        -------
        Dict[str, Any]
            Session information dictionary
        """
        return self.active_sessions.get(session_id, {})

    def list_sessions(self) -> Dict[str, Any]:
        """Get all sessions (active and closed).

        Returns
        -------
        Dict[str, Any]
            Dictionary of all session information
        """
        return self.active_sessions.copy()


# Global session manager
_session_manager = SessionManager()


def get_global_session_manager() -> SessionManager:
    """Get the global session manager instance.

    Returns
    -------
    SessionManager
        Global session manager instance
    """
    return _session_manager


# EOF
