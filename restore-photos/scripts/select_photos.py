#!/usr/bin/env python3
"""Copy the photos listed in a restoration CSV into a working folder, one
sub-folder per priority tier, without touching the library.

The CSV needs the columns `priority`, `issues`, `file`, `full_path` (the
format of the Claude Cowork restoration scan). A tier is the value of
`priority` up to its first space or dash: Severe, Moderate, Light, Digital.

    select_photos.py <csv> [--tier Severe,Moderate] [--output DIR] [--dry-run]

Results: <output>/<tier>/originals/<file>  (default output: ~/Downloads/photo-restore,
$PHOTO_RESTORE_OUT replaces that). <output>/manifest.csv lists every copied
photo with its tier, issues and source path so the later steps do not need the CSV.
Missing sources are reported, existing copies are skipped.
"""
import argparse
import csv
import os
import shutil
import sys


def out_root(arg=None):
    return os.path.abspath(os.path.expanduser(arg or os.environ.get("PHOTO_RESTORE_OUT") or "~/Downloads/photo-restore"))


def tier_of(priority):
    p = priority.strip()
    for sep in (" ", "—", "-", "–"):
        p = p.split(sep)[0]
    return p.capitalize() or "Other"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--tier", default="", help="comma list of tiers to copy (default: all)")
    ap.add_argument("--output", help="working folder root")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    want = {t.strip().capitalize() for t in a.tier.split(",") if t.strip()}
    root = out_root(a.output)
    rows = list(csv.DictReader(open(a.csv, newline="", encoding="utf-8")))
    need = {"priority", "issues", "file", "full_path"}
    if rows and not need <= set(rows[0]):
        sys.exit(f"CSV must have the columns {sorted(need)}; found {sorted(rows[0])}")
    copied, skipped, missing = 0, 0, []
    manifest_path = os.path.join(root, "manifest.csv")
    manifest = {}
    if os.path.exists(manifest_path):
        for r in csv.DictReader(open(manifest_path, newline="", encoding="utf-8")):
            manifest[r["file"]] = r
    for r in rows:
        tier = tier_of(r["priority"])
        if want and tier not in want:
            continue
        src = r["full_path"]
        if not os.path.isfile(src):
            missing.append(src)
            continue
        dst_dir = os.path.join(root, tier, "originals")
        dst = os.path.join(dst_dir, r["file"])
        if os.path.exists(dst):
            skipped += 1
        else:
            copied += 1
            print(f"{tier:<9} {r['file']}")
            if not a.dry_run:
                os.makedirs(dst_dir, exist_ok=True)
                shutil.copy2(src, dst)
        manifest[r["file"]] = dict(tier=tier, file=r["file"], issues=r["issues"], source=src, copy=dst)
    if not a.dry_run and manifest:
        os.makedirs(root, exist_ok=True)
        with open(manifest_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["tier", "file", "issues", "source", "copy"])
            w.writeheader()
            for k in sorted(manifest):
                w.writerow(manifest[k])
    print(f"\n{copied} copied, {skipped} already there, {len(missing)} missing -> {root}")
    for m in missing:
        print(f"  missing: {m}")


if __name__ == "__main__":
    main()
