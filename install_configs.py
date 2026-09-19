"""Copy the contents of dota_configs/ into Dota 2's config directory.

Usage:
    python install_configs.py
    python install_configs.py --dry-run
"""

import argparse
import shutil
import string
import sys
from pathlib import Path

CONFIG_SRC_DIR = Path(__file__).resolve().parent / "dota_configs"
CONFIG_DST_DIR = Path("game") / "dota" / "cfg"
LAUNCH_OPTIONS = "-gamestateintegration -condebug"

MAX_DEPTH = 5
# "steamapps/common/dota 2 beta" is 3 levels, so it can sit under 0..2 wildcard levels.
PATTERNS = ["*/" * d + "steamapps/common/dota 2 beta" for d in range(MAX_DEPTH)]


def drives() -> list[Path]:
    return [Path(f"{d}:\\") for d in string.ascii_uppercase if Path(f"{d}:\\").is_dir()]

def find_dota() -> Path | None:
    """Search every drive for a "steamapps/common/dota 2 beta" folder."""
    # glob is case-insensitive on Windows and skips directories it cannot read.
    hits = (hit for drive in drives() for pat in PATTERNS for hit in drive.glob(pat))
    return next(hits, None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="report without copying")
    args = parser.parse_args()

    config_src_files = sorted(path for path in CONFIG_SRC_DIR.rglob("*") if path.is_file())
    if not config_src_files:
        print(f"Error: no files in {CONFIG_SRC_DIR}", file=sys.stderr)
        return 1

    print("Searching for Dota 2...")
    dota = find_dota()
    if dota is None:
        print("Error: could not find a 'dota 2 beta' folder", file=sys.stderr)
        return 1
    print(f"Found Dota 2: {dota}")

    for config_src in config_src_files:
        config_dst = dota / CONFIG_DST_DIR / config_src.relative_to(CONFIG_SRC_DIR)
        if args.dry_run:
            print(f"[dry-run] would copy {config_src} -> {config_dst}")
            continue
        config_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_src, config_dst)
        print(f"Installed: {config_dst}")

    print(f"Remember to add {LAUNCH_OPTIONS} to Dota 2's launch options and restart Dota.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
