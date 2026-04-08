"""Create a local .env file from .env.example.

Run with:
  uv run friday_init_env
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys


def main() -> int:
    project_root = Path(__file__).resolve().parent
    example_file = project_root / ".env.example"
    target_file = project_root / ".env"

    if not example_file.exists():
        print(".env.example was not found.", file=sys.stderr)
        return 1

    if target_file.exists():
        print(".env already exists. Leaving it unchanged.")
        return 0

    shutil.copyfile(example_file, target_file)
    print("Created .env from .env.example. Fill in your real credentials, then rerun uv run friday_voice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())