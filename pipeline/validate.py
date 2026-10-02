"""python -m pipeline.validate <validator.jar> [--out dist]

Runs the MobilityData GTFS validator on every feed in dist/feeds and fails
if any feed has ERROR notices. Warnings and info notices are printed.
The calendar deliberately runs to 2099, so "service_extends_far_in_the_future"
is expected on every feed.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(prog="python -m pipeline.validate")
    ap.add_argument("jar", type=Path)
    ap.add_argument("--out", default=ROOT / "dist", type=Path)
    args = ap.parse_args()

    failed = False
    feeds = sorted((args.out / "feeds").glob("*.zip"))
    if not feeds:
        sys.exit("no feeds found; run python -m pipeline first")
    for feed in feeds:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["java", "-jar", str(args.jar), "-i", str(feed), "-o", tmp],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            notices = json.loads((Path(tmp) / "report.json").read_text(encoding="utf8"))["notices"]
        errors = [n for n in notices if n["severity"] == "ERROR"]
        print(f"{feed.name}: {len(errors)} error(s)")
        for n in notices:
            print(f"  {n['severity']:7} {n['code']} x{n['totalNotices']}")
        failed |= bool(errors)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
