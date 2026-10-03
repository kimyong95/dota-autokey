import string
from pathlib import Path

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
