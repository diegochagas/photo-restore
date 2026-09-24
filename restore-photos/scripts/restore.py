#!/usr/bin/env python3
"""Restore scanned photo prints: damage (emulsion loss, water blotches,
stains, scratches, cut corners) is repainted by a local image-edit model,
but the model's pixels are used ONLY where the print is damaged - every
undamaged pixel, every face, stays the original's. Then the colours are fixed.

    restore.py <image-or-folder>... [--mode full|color] [--backend klein|qwen]
               [--recursive] [--output DIR] [--seed N] [--reuse-raw] [--threshold 22]
               [--min-area 300] [--drop 3,5] [--include 2] [--add x,y,w,h]...
               [--fix-color] [--levels 1] [--wb 0.5] [--contrast 1.2] [--sat 1.1]
               [--hires] [--prompt "..."] [--force]

--mode full (default): model pass -> damage mask -> composite. The colours
  stay the original's: the model's pixels are colour-matched to the scan
  before they are pasted, and nothing else is touched. --fix-color adds the
  colour fix on top (faded prints that are also damaged).
--mode color: colour fix only (faded / colour-cast prints, no damage).

How the damage mask is made (mode full):
  1. the whole photo goes to the model with a "repair this damaged print"
     prompt (~30 s klein / ~100 s qwen per photo at 1 MP);
  2. the output is aligned to the original (ECC affine) and its colours
     matched to the original (per-channel linear fit on the pixels the model
     left alone);
  3. where the two still differ (mean |diff| blurred sigma 4 > --threshold)
     the model changed something = damage it repaired. Blobs under --min-area
     px are ignored (a moved highlight, a redrawn button). Regions are
     numbered in <work>/<name>.regions.jpg: fix a mask with --drop N,
     --include N (a region under the threshold), --add x,y,w,h, --protect
     x,y,w,h (keeps the original inside the box, e.g. a face the model
     shifted), then re-run with --reuse-raw (no new model call).
  4. composite: model pixels (colour-matched) inside the mask, feathered
     4 px; original everywhere else, original colours. --hires re-runs the model on native-
     resolution crops of the masked regions for scans above ~1.3 MP.

Results: <out>/restored/<name>.jpg (q95, EXIF copied from the original by
exiftool), <out>/work/<name>.raw.png / .mask.png / .regions.jpg /
.compare.jpg, and for a folder <out>/sheets/qc_NN.jpg (original | result,
4 per sheet). <out> is ~/Downloads/photo-restore/<folder name> for a folder,
~/Downloads/photo-restore for a loose image ($PHOTO_RESTORE_OUT replaces the
root, --output names any directory). Sources are never modified; existing
results are skipped unless --force.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comfy_client  # noqa: E402
import fix_color  # noqa: E402

PROMPT = ("This is a scan of an old damaged photo print. The white and brown blotches, flakes, stains, "
          "scratches, creases and specks are damage where the picture is missing, and the plain areas "
          "outside the print's edges are missing parts of the photo. Repair the photo: fill every damaged "
          "or missing area with what would naturally be there, continuing the surrounding people, clothes, "
          "floor and background seamlessly. Keep everything that is not damaged exactly as it is: same "
          "framing, same colors, same faces. No text.")
EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp")
FEATHER = 4
HIRES_MP = 1.3
GROUP_PX = 20      # changed blobs closer than this form one region


def out_root(image, arg=None, folder=None):
    """Where a photo's restored/ and work/ go:
    --output DIR                     -> DIR
    an image given inside a folder   -> <root>/<folder name>
    a loose image                    -> <root>
    root = $PHOTO_RESTORE_OUT or ~/Downloads/photo-restore."""
    if arg:
        return os.path.abspath(os.path.expanduser(arg))
    root = os.path.abspath(os.path.expanduser(os.environ.get("PHOTO_RESTORE_OUT") or "~/Downloads/photo-restore"))
    if folder:
        return os.path.join(root, os.path.basename(os.path.abspath(folder)))
    return root


def align(orig, gen):
    g1 = cv2.cvtColor(orig, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    g2 = cv2.cvtColor(gen, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        cv2.findTransformECC(g1, g2, warp, cv2.MOTION_AFFINE,
                             (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5), None, 5)
    except cv2.error:
        return gen
    return cv2.warpAffine(gen, warp, (orig.shape[1], orig.shape[0]),
                          flags=cv2.INTER_LANCZOS4 + cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)


def match_colors(orig, gen):
    """Per-channel linear fit of gen onto orig, using only the pixels the
    model did not change much (found iteratively). Damage-like pixels of the
    original (bright and colourless: the paper base showing through) never
    take part - on a print that is half white blotches they would pull the
    fit toward white and wash out the repair. A fit that is not plausible
    for a colour match (slope far from 1, big offset) is dropped and the
    model's own colours are kept for that channel."""
    a, b = orig.astype(np.float32), gen.astype(np.float32)
    hsv = cv2.cvtColor(orig, cv2.COLOR_BGR2HSV)
    ok = ~((hsv[:, :, 2] >= 215) & (hsv[:, :, 1] <= 50))
    d = cv2.GaussianBlur(np.abs(b - a).mean(axis=2), (0, 0), 3)
    keep = ok & (d <= np.percentile(d[ok], 60)) if ok.sum() > 2000 else ok
    fitted = b.copy()
    for _ in range(3):
        if keep.sum() < 2000:
            break
        fitted = b.copy()
        for c in range(3):
            x, y = b[:, :, c][keep], a[:, :, c][keep]
            if x.std() < 1:
                continue
            k, m = np.polyfit(x, y, 1)
            if 0.6 <= k <= 1.5 and abs(m) <= 60:
                fitted[:, :, c] = b[:, :, c] * k + m
        d = cv2.GaussianBlur(np.abs(fitted - a).mean(axis=2), (0, 0), 3)
        keep = ok & (d < 20)
        if keep.mean() < 0.05:
            keep = ok & (d <= np.percentile(d[ok], 30))
    return np.clip(fitted + 0.5, 0, 255).astype(np.uint8)


def diff_regions(orig, gen_m, threshold, min_area):
    """Where the model's (aligned, colour-matched) output differs from the
    original. Blobs closer than GROUP_PX form one region, so the stains
    around a blotch count with it; a region is accepted when its changed
    area reaches min_area, or when it is small but the change is strong."""
    d = cv2.GaussianBlur(np.abs(gen_m.astype(np.float32) - orig.astype(np.float32)).mean(axis=2), (0, 0), 4)
    hard = (d > threshold).astype(np.uint8) * 255
    hard = cv2.morphologyEx(hard, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    grouped = cv2.dilate(hard, np.ones((2 * GROUP_PX + 1,) * 2, np.uint8))
    n, glabels, stats, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    labels = np.where(hard > 0, glabels, 0)
    regions = []
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        sel = labels == i
        area = int(sel.sum())
        if area == 0:
            continue
        ys, xs = np.where(sel)
        box = (int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
        strength = float(d[sel].mean())
        accepted = area >= min_area or (area >= 80 and strength >= 2 * threshold)
        regions.append(dict(n=len(regions) + 1, label=i, box=box, area=area, strength=round(strength, 1),
                            accepted=bool(accepted)))
    return labels, regions, d


def build_mask(shape, labels, regions, drop, include, add, grow=3, protect=()):
    H, W = shape
    mask = np.zeros((H, W), np.uint8)
    for r in regions:
        if (r["accepted"] and r["n"] not in drop) or r["n"] in include:
            mask[labels == r["label"]] = 255
    for (x, y, w, h) in add:
        mask[max(0, y):min(H, y + h), max(0, x):min(W, x + w)] = 255
    if grow:
        mask = cv2.dilate(mask, np.ones((2 * grow + 1,) * 2, np.uint8))
    for (x, y, w, h) in protect:
        mask[max(0, y):min(H, y + h), max(0, x):min(W, x + w)] = 0
    return mask


def overlay(bgr, labels, regions, mask, drop, include):
    out = bgr.copy()
    tint = out.copy()
    tint[mask > 0] = (0, 0, 255)
    out = cv2.addWeighted(out, 0.6, tint, 0.4, 0)
    for r in regions:
        on = (r["accepted"] and r["n"] not in drop) or r["n"] in include
        if not on and r["area"] < 60:
            continue
        x, y, w, h = r["box"]
        col = (0, 200, 0) if on else (0, 220, 255)
        cv2.rectangle(out, (x, y), (x + w, y + h), col, 2)
        cv2.putText(out, str(r["n"]), (x + 3, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
        cv2.putText(out, str(r["n"]), (x + 3, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
    return out


def composite(orig, gen_m, mask):
    hard = (mask > 0).astype(np.float32)
    alpha = cv2.GaussianBlur(hard, (0, 0), FEATHER)
    alpha = np.maximum(alpha, hard)[:, :, None]
    return np.clip(orig.astype(np.float32) * (1 - alpha) + gen_m.astype(np.float32) * alpha + 0.5, 0, 255).astype(np.uint8)


def compare_sheet(orig, result, path, max_w=900):
    s = min(1.0, max_w / orig.shape[1])
    a = cv2.resize(orig, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else orig
    b = cv2.resize(result, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else result
    gap = np.full((a.shape[0], 10, 3), 80, np.uint8)
    cv2.imwrite(path, np.hstack([a, gap, b]), [cv2.IMWRITE_JPEG_QUALITY, 85])


def save_jpeg(path, bgr, src):
    cv2.imwrite(path, bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if shutil.which("exiftool"):
        subprocess.run(["exiftool", "-q", "-m", "-overwrite_original", "-TagsFromFile", src, "-all:all",
                        "-ImageHeight=", "-ImageWidth=", "-Orientation=", path], check=False)


def parse_nums(s):
    return {int(v) for v in s.split(",") if v.strip()} if s else set()


def parse_boxes(items):
    return [tuple(int(v) for v in it.split(",")) for it in (items or [])]


def collect(paths, recursive=False):
    """[(file, folder-or-None)] for images and folders; a folder's images
    carry the folder so their results are grouped under its name."""
    files = []
    for p in paths:
        p = os.path.expanduser(p.rstrip("/"))
        if os.path.isdir(p):
            pattern = os.path.join(p, "**", "*") if recursive else os.path.join(p, "*")
            for f in sorted(glob.glob(pattern, recursive=recursive)):
                if f.lower().endswith(EXT) and os.path.isfile(f):
                    files.append((f, p))
        elif os.path.isfile(p):
            files.append((p, None))
        else:
            print(f"not found: {p}", file=sys.stderr)
    return files


def restore_one(src, a, folder=None):
    root = out_root(src, a.output, folder)
    rest_dir, work = os.path.join(root, "restored"), os.path.join(root, "work")
    os.makedirs(rest_dir, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    name = os.path.splitext(os.path.basename(src))[0]
    dst = os.path.join(rest_dir, name + ".jpg")
    if os.path.exists(dst) and not (a.force or a.reuse_raw or a.raw or a.drop or a.include or a.add or a.protect):
        print(f"skip (exists): {dst}")
        return
    orig = cv2.imread(src, cv2.IMREAD_COLOR)
    if orig is None:
        print(f"cannot read {src}", file=sys.stderr)
        return
    t0 = time.time()
    if a.mode == "color":
        result = fix_color.fix(orig, None, a.levels, a.wb, a.contrast, a.sat)
        save_jpeg(dst, result, src)
        compare_sheet(orig, result, os.path.join(work, name + ".compare.jpg"))
        print(f"-> {dst} (colour only, {time.time() - t0:.0f}s)")
        return
    raw_path = os.path.join(work, name + ".raw.png")
    if a.raw:
        raw = cv2.imread(a.raw)
        if raw is None:
            sys.exit(f"--raw: cannot read {a.raw}")
        cv2.imwrite(raw_path, raw)   # so that --reuse-raw works from now on
    elif a.reuse_raw:
        raw = cv2.imread(raw_path)
        if raw is None:
            sys.exit(f"--reuse-raw: no raw at {raw_path}")
    else:
        if not comfy_client.available(a.backend):
            sys.exit(f"{a.backend} is not available in ComfyUI - start it or fix the model files (see README)")
        raw = comfy_client.edit(orig, a.prompt or PROMPT, a.backend, a.seed)
        cv2.imwrite(raw_path, raw)
    gen = match_colors(orig, align(orig, raw))
    labels, regions, _ = diff_regions(orig, gen, a.threshold, a.min_area)
    drop, inc, add = parse_nums(a.drop), parse_nums(a.include), parse_boxes(a.add)
    mask = build_mask(orig.shape[:2], labels, regions, drop, inc, add, protect=parse_boxes(a.protect))
    cv2.imwrite(os.path.join(work, name + ".mask.png"), mask)
    cv2.imwrite(os.path.join(work, name + ".regions.jpg"), overlay(orig, labels, regions, mask, drop, inc),
                [cv2.IMWRITE_JPEG_QUALITY, 85])
    if a.hires and orig.shape[0] * orig.shape[1] > HIRES_MP * 1e6 and mask.any():
        import inpaint
        gen = inpaint.inpaint(orig, mask, PROMPT, a.backend, a.seed, base=gen)
    result = composite(orig, gen, mask)
    if a.fix_color:
        result = fix_color.fix(result, None, a.levels, a.wb, a.contrast, a.sat)
    save_jpeg(dst, result, src)
    compare_sheet(orig, result, os.path.join(work, name + ".compare.jpg"))
    on = [r["n"] for r in regions if (r["accepted"] and r["n"] not in drop) or r["n"] in inc]
    print(f"-> {dst}  regions {len(on)} in / {len(regions) - len(on)} out, mask {(mask > 0).mean() * 100:.1f}% "
          f"of the image, {time.time() - t0:.0f}s")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="images and/or folders")
    ap.add_argument("--recursive", action="store_true", help="also the sub-folders of a folder")
    ap.add_argument("--no-sheets", action="store_true", help="skip the QC contact sheets after a folder run")
    ap.add_argument("--mode", choices=("full", "color"), default="full")
    ap.add_argument("--backend", choices=("klein", "qwen"), default="klein")
    ap.add_argument("--output")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--reuse-raw", action="store_true", help="reuse work/<name>.raw.png instead of calling the model")
    ap.add_argument("--raw", help="use this model output as the raw (single image)")
    ap.add_argument("--threshold", type=float, default=22.0)
    ap.add_argument("--min-area", type=int, default=300)
    ap.add_argument("--drop", default="")
    ap.add_argument("--include", default="")
    ap.add_argument("--add", action="append", help="x,y,w,h box to repaint (repeatable)")
    ap.add_argument("--protect", action="append", help="x,y,w,h box that keeps the original (repeatable)")
    ap.add_argument("--hires", action="store_true")
    ap.add_argument("--prompt", help="replace the repair prompt entirely (single image)")
    ap.add_argument("--fix-color", action="store_true", help="mode full: also run the colour fix (off by default)")
    ap.add_argument("--levels", type=float, default=1.0)
    ap.add_argument("--wb", type=float, default=0.5)
    ap.add_argument("--contrast", type=float, default=1.2)
    ap.add_argument("--sat", type=float, default=1.1)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    files = collect(a.paths, a.recursive)
    if not files:
        sys.exit("nothing to do")
    if len(files) > 1 and (a.raw or a.drop or a.include or a.add or a.protect or a.prompt):
        sys.exit("--raw/--drop/--include/--add/--protect/--prompt apply to one image at a time")
    roots = {}
    for f, folder in files:
        restore_one(f, a, folder)
        roots.setdefault(out_root(f, a.output, folder), []).append(f)
    if not a.no_sheets:
        for root, fs in roots.items():
            if len(fs) > 1:
                sheets(root)


def sheets(root, per=4, width=1000):
    """Stack work/*.compare.jpg into <root>/sheets/qc_NN.jpg for QC."""
    work, out = os.path.join(root, "work"), os.path.join(root, "sheets")
    comps = sorted(glob.glob(os.path.join(work, "*.compare.jpg")))
    if not comps:
        return
    os.makedirs(out, exist_ok=True)
    for old in glob.glob(os.path.join(out, "qc_*.jpg")):
        os.remove(old)
    for s in range(0, len(comps), per):
        tiles = []
        for k, f in enumerate(comps[s:s + per]):
            im = cv2.imread(f)
            im = cv2.resize(im, (width, round(im.shape[0] * width / im.shape[1])), interpolation=cv2.INTER_AREA)
            label = np.full((22, width, 3), 60, np.uint8)
            cv2.putText(label, f"{s + k + 1}. {os.path.basename(f)[:-12]}", (6, 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
            tiles += [label, im, np.full((4, width, 3), 60, np.uint8)]
        p = os.path.join(out, f"qc_{s // per + 1:02d}.jpg")
        cv2.imwrite(p, np.vstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 80])
        print(f"sheet -> {p}")


if __name__ == "__main__":
    main()
