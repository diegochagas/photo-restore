#!/usr/bin/env python3
"""Straighten a scanned print and cut away the white around it.

A print scanned at a slight angle leaves a white wedge along its edges, and
a print with a white border, a torn edge or a cut corner leaves white strips.
Those are not part of the photo: they are cut, not repainted.

  1. deskew   a print scanned crooked shows a wedge-shaped white strip
              along its edges; the strip's width is fitted along each side
              and gives the angle. Angles between 0.3 and --max-angle degrees
              are corrected (the new corners are filled white, so step 2
              cuts them).
  2. trim     from each side, lines (rows / columns) that are mostly white
              (> 50 %) are cut, up to --max-trim of that side; then a second
              narrow pass (at most 2 % more) cuts lines still > 5 % white -
              the rest of a slanted strip or a torn corner. White inside the
              photo (a wedding dress, a white wall) is never reached unless
              it fills the edge line.

    crop.py <image> <out> [--max-angle 6] [--max-trim 0.15]
"""
import argparse

import cv2
import numpy as np

WHITE_MIN, WHITE_SPREAD, WHITE_GRAIN = 246, 8, 2.0
FIRST_FRAC, SECOND_FRAC, SECOND_MAX = 0.50, 0.05, 0.02


def white_mask(bgr):
    """Scanner bed / bare paper: every channel near 255, no colour and no
    grain. A clipped wall or sky is as bright but keeps film grain and a
    little colour, so it is not taken for a border."""
    lo, hi = bgr.min(axis=2).astype(np.int16), bgr.max(axis=2).astype(np.int16)
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    grain = cv2.blur(np.abs(cv2.Laplacian(g, cv2.CV_32F)), (7, 7))
    m = ((lo >= WHITE_MIN) & (hi - lo <= WHITE_SPREAD) & (grain < WHITE_GRAIN)).astype(np.uint8)
    return cv2.medianBlur(m * 255, 5) > 0


def _edge_slopes(wm, max_frac=0.2):
    """Per side, the slope (px of strip width per px along the edge) of the
    white strip between the scan edge and the print, fitted on the lines
    where the strip exists. Returns [(slope, weight)]."""
    H, W = wm.shape
    out = []
    sides = [(wm, W), (wm[:, ::-1], W), (wm.T, H), (wm.T[:, ::-1], H)]
    for k, (m, depth) in enumerate(sides):
        lim = int(depth * max_frac)
        head = m[:, :lim]
        width = np.where(head.all(axis=1), lim, np.argmin(head, axis=1))   # leading white run
        idx = np.nonzero((width > 0) & (width < lim))[0]
        if len(idx) < 0.4 * m.shape[0]:
            continue
        slope = np.polyfit(idx, width[idx].astype(np.float64), 1)[0]
        # left/top strips widen one way, right/bottom mirrored: same tilt, opposite sign
        out.append((slope if k in (0, 3) else -slope, len(idx)))
    return out


def skew_angle(bgr):
    """Degrees to rotate (cv2 convention, counter-clockwise positive) so the
    print is level; 0 when no side shows a wedge-shaped white strip."""
    slopes = _edge_slopes(white_mask(bgr))
    if not slopes:
        return 0.0
    s = sum(v * w for v, w in slopes) / sum(w for _, w in slopes)
    return float(-np.degrees(np.arctan(s)))


def rotate(bgr, angle):
    h, w = bgr.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(bgr, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                          borderValue=(255, 255, 255))


def _trim(frac, limit, thr, start=0):
    i = start
    while i < limit and frac[i] > thr:
        i += 1
    return i


def trim_box(bgr, max_trim=0.15):
    """(x, y, w, h) of the photo inside its white strips."""
    wm = white_mask(bgr)
    H, W = wm.shape
    rows, cols = wm.mean(axis=1), wm.mean(axis=0)
    ch, cw = int(H * max_trim), int(W * max_trim)
    top = _trim(rows, ch, FIRST_FRAC)
    bot = _trim(rows[::-1], ch, FIRST_FRAC)
    left = _trim(cols, cw, FIRST_FRAC)
    right = _trim(cols[::-1], cw, FIRST_FRAC)
    # second pass on what is left: the tail of a slanted strip, a torn corner
    for _ in range(2):
        sub = wm[top:H - bot, left:W - right]
        r, c = sub.mean(axis=1), sub.mean(axis=0)
        sh, sw = int(H * SECOND_MAX), int(W * SECOND_MAX)
        top += _trim(r, sh, SECOND_FRAC)
        bot += _trim(r[::-1], sh, SECOND_FRAC)
        left += _trim(c, sw, SECOND_FRAC)
        right += _trim(c[::-1], sw, SECOND_FRAC)
    # a couple of pixels more so no light seam is left at the cut
    pad = lambda n: n + 2 if n else 0
    top, bot, left, right = pad(top), pad(bot), pad(left), pad(right)
    return left, top, W - left - right, H - top - bot


def crop(bgr, max_angle=6.0, max_trim=0.15):
    """Returns (cropped image, info dict)."""
    angle = skew_angle(bgr)
    out = bgr
    if 0.3 <= abs(angle) <= max_angle:
        out = rotate(bgr, angle)
    else:
        angle = 0.0
    x, y, w, h = trim_box(out, max_trim)
    if w < 0.5 * out.shape[1] or h < 0.5 * out.shape[0]:
        return bgr, dict(angle=0.0, box=(0, 0, bgr.shape[1], bgr.shape[0]), changed=False)
    out = out[y:y + h, x:x + w]
    changed = bool(angle) or out.shape[:2] != bgr.shape[:2]
    return out, dict(angle=round(angle, 2), box=(x, y, w, h), changed=changed)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("out")
    ap.add_argument("--max-angle", type=float, default=6.0)
    ap.add_argument("--max-trim", type=float, default=0.15)
    a = ap.parse_args()
    im = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if im is None:
        raise SystemExit(f"cannot read {a.image}")
    out, info = crop(im, a.max_angle, a.max_trim)
    cv2.imwrite(a.out, out, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"-> {a.out}  angle {info['angle']}°, box {info['box']} of {im.shape[1]}x{im.shape[0]}")


if __name__ == "__main__":
    main()
