from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .infer import infer_from_patch_files, to_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Patch-only F2P/P2P inference (no docker, no test execution)"
    )
    parser.add_argument("--full-patch", required=True, help="Path to full patch file")
    parser.add_argument("--test-patch", required=True, help="Path to test-only patch file")
    parser.add_argument("--code-patch", required=True, help="Path to code-only patch file")
    parser.add_argument("--language", required=True, help="Primary repository language")
    parser.add_argument("--output", help="Optional output JSON path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    for p in [args.full_patch, args.test_patch, args.code_patch]:
        if not Path(p).exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            return 2

    result = infer_from_patch_files(
        full_patch_path=args.full_patch,
        test_patch_path=args.test_patch,
        code_patch_path=args.code_patch,
        language=args.language,
    )
    payload = to_json(result)

    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(f"Wrote: {args.output}")
    else:
        print(payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
