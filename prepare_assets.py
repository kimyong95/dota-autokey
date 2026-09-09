"""Download the Invoker spell icons into assets/.

Usage:
    python prepare_assets.py
    python prepare_assets.py --force
"""

import argparse
import urllib.request

from invoker_overlay import ASSETS, LAYOUT

ICON_URL = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/abilities/{}.png"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="re-download existing icons")
    args = parser.parse_args()

    ASSETS.mkdir(exist_ok=True)
    for spell in LAYOUT:
        target = ASSETS / f"{spell}.png"
        if target.exists() and not args.force:
            print(f"skip {target.name}")
            continue
        urllib.request.urlretrieve(ICON_URL.format(spell), target)
        print(f"got  {target.name}")

    print(f"Icons in {ASSETS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
