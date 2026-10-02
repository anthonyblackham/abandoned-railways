"""python -m pipeline [railway ...] [--out dist]

Checks and builds every railway under railways/ (or just the ones named),
then copies the viewer from web/ so dist/ is the complete site.
Errors stop the build; warnings are printed and the build carries on.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import build, load

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(prog="python -m pipeline")
    ap.add_argument("railways", nargs="*", help="railway ids (default: all)")
    ap.add_argument("--out", default=ROOT / "dist", type=Path)
    args = ap.parse_args()

    folders = sorted(p.parent for p in (ROOT / "railways").glob("*/railway.yaml"))
    if args.railways:
        folders = [f for f in folders if f.name in args.railways]

    index, failed = [], False
    for folder in folders:
        report = load.Report()
        rw = load.load(folder, report)
        for w in report.warnings:
            print(f"  warning  {folder.name}: {w}")
        for e in report.errors:
            print(f"  ERROR    {folder.name}: {e}")
        if report.errors:
            failed = True
            continue
        index.append(build.write(rw, args.out))
        trips = sum(len(e.trips) for e in rw.eras)
        print(f"built {rw.id}: {len(rw.eras)} era(s), {trips} trips")

    if failed:
        sys.exit(1)
    (args.out / "data" / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf8")
    # The viewer is static files; copying it next to the data makes dist/ the whole site.
    shutil.copytree(ROOT / "web", args.out, dirs_exist_ok=True)


if __name__ == "__main__":
    main()
