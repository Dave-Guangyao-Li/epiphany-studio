"""Convenience entrypoint for the guarded M3.4 Draft Quality E2E."""

from __future__ import annotations

import sys

from epiphany.quality_contract_e2e import main as quality_contract_main


def main() -> int:
    return quality_contract_main(["--quality-review", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
