"""Find Dota 2's Game State Integration folder and copy the autokey config there.

Usage:
    python installer.py
    python installer.py --dry-run
    python installer.py --cfg-file other.cfg
"""

import argparse
import shutil
import string
import sys
from pathlib import Path

MAX_DEPTH = 5
# "steamapps/common/dota 2 beta" is 3 levels, so it can sit under 0..2 wildcard levels.
PATTERNS = ["*/" * d + "steamapps/common/dota 2 beta" for d in range(MAX_DEPTH)]
GSI_SUBPATH = Path("game") / "dota" / "cfg" / "gamestate_integration"


def drives() -> list[Path]:
    return [Path(f"{d}:\\") for d in string.ascii_uppercase if Path(f"{d}:\\").is_dir()]

def find_dota() -> Path | None:
    """Search every drive for a "steamapps/common/dota 2 beta" folder."""
    # glob is case-insensitive on Windows and skips directories it cannot read.
    hits = (hit for drive in drives() for pat in PATTERNS for hit in drive.glob(pat))
    return next(hits, None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cfg-file",
        type=Path,
        default=Path(__file__).resolve().parent / "gamestate_integration_autokey.cfg",
        help="config file to install (default: the one next to this script)",
    )
    parser.add_argument("--dry-run", action="store_true", help="report without copying")
    args = parser.parse_args()

    if not args.cfg_file.is_file():
        print(f"Error: config file not found: {args.cfg_file}", file=sys.stderr)
        return 1

    print("Searching for Dota 2...")
    dota = find_dota()
    if dota is None:
        print("Error: could not find a 'dota 2 beta' folder", file=sys.stderr)
        return 1
    print(f"Found Dota 2: {dota}")

    target = dota / GSI_SUBPATH / args.cfg_file.name
    if args.dry_run:
        print(f"[dry-run] would copy {args.cfg_file} -> {target}")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.cfg_file, target)
    print(f"Installed: {target}")
    print(f"Remember to add -gamestateintegration to Dota 2 launch option.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
