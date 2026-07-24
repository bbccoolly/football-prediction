"""Deprecated compatibility entrypoint for the offline backtest CLI."""

import sys

from calibrate_cli import main


if __name__ == "__main__":
    print("[弃用] calibrate.py 已迁移到 calibrate_cli.py。", file=sys.stderr)
    raise SystemExit(main())
