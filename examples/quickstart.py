"""scitex-session quickstart: a minimal @session-decorated entry point.

The @session decorator wraps a function so that:
  - ./config/*.yaml is loaded into the injected CONFIG
    (config/QUICKSTART.yaml with N_SQUARES: 5 -> CONFIG.QUICKSTART.N_SQUARES)
  - Non-INJECTED function arguments are exposed as CLI flags
    (--label); keep these for inputs such as paths or ids, never for
    numeric analysis parameters (those live in config/*.yaml)
  - A reproducible run directory is created (quickstart_out/...)
  - CONFIG / logger are auto-injected
  - Output is organized on success vs failure

Run it from this directory, so ./config/ is found:

    python quickstart.py
    python quickstart.py --label pilot
    python quickstart.py --help        # lists CONFIG.QUICKSTART.* values

To change N_SQUARES, edit config/QUICKSTART.yaml.
"""

from scitex_session import INJECTED, session


@session(verbose=False)
def main(
    label: str = "demo",
    CONFIG=INJECTED,
    logger=INJECTED,
):
    """Print the first N_SQUARES squares, N_SQUARES read from CONFIG."""
    n_squares = CONFIG.QUICKSTART.N_SQUARES
    squares = [i * i for i in range(n_squares)]
    logger.info(f"label={label!r}  squares(0..{n_squares - 1})={squares}")
    print(f"label={label!r}  squares(0..{n_squares - 1})={squares}")
    return 0


if __name__ == "__main__":
    main()
