#!/usr/bin/env python3
# Timestamp: "2025-11-18 09:08:38 (ywatanabe)"
# File: /home/ywatanabe/proj/scitex-code/src/scitex/session/template.py


from pprint import pformat

from scitex_logging import getLogger

from scitex_session import INJECTED, session


@session(verbose=False)
def main(
    arg1=None,
    arg2=None,
    CONFIG=INJECTED,
    plt=INJECTED,
    COLORS=INJECTED,
    rngg=INJECTED,
    logger=INJECTED,
):
    """Demonstration for scitex.session.session"""
    active_logger = (
        getLogger(__name__) if logger is INJECTED or logger is None else logger
    )
    active_logger.info("%s", pformat(CONFIG))


if __name__ == "__main__":
    main()

# EOF
