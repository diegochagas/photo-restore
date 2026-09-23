---
name: restore-photos
description: Restore scanned photo prints - water/emulsion damage, stains, scratches, cut corners repainted by a local image-edit model (FLUX.2 klein or Qwen-Image-Edit in ComfyUI, free, offline) but ONLY inside the damaged areas, every undamaged pixel and every face kept from the original; then automatic colour/contrast fix for faded or colour-cast prints; re-save of broken JPEGs. Works from a restoration CSV (tiers Severe/Moderate/Light/Digital) or on any image/folder. Results in ~/Downloads/photo-restore, sources never touched, the agent QCs every result with Diego. Use when Diego asks to "restore these photos/prints/scans", "fix the damaged photos from the report", "restaurar as fotos", "remove the white blotches / water damage", "fix the colours of the old photos", or points at the photos_needing_restoration.csv.
---

# restore-photos — repair scanned prints, keep what is real

You are the orchestrator and the visual QC reviewer. The model repaints, the
scripts make sure it only repaints damage, you judge every result before it
counts as done. No LLM API usage; the only AI is local ComfyUI.

Scripts in `restore-photos/scripts/`, run with the repo venv
(`<repo>/venv/bin/python`, created by `<repo>/setup.sh`). `<repo>` is the
photo-restore checkout. All paths below are relative to it.

| Script | Does |
| --- | --- |
| `select_photos.py <csv> [--tier ...]` | copies the CSV's photos into `<out>/<tier>/originals/`, writes `<out>/manifest.csv` |
| `restore.py <image-or-folder>... [--mode full\|color]` | the restoration (model pass → damage mask → composite, original colours kept; `--fix-color` adds the colour fix; `--mode color` is the colour fix alone) |
| `contact_sheet.py <originals> <restored>` | before/after sheets, 6 pairs each, for batch QC |
| `fix_broken.py <jpg>... [--crop-strip]` | re-saves JPEGs with data-stream errors / truncated tails, EXIF kept |
| `inpaint.py <image> <mask> <out>` | native-resolution repaint of masked regions (big scans; `restore.py --hires` calls it) |
| `fix_color.py <image> <out>` | the colour fix alone, with all its knobs |
| `comfy_client.py --check` | is ComfyUI up, which backend has its models |

## Where results go

`~/Downloads/photo-restore/` (`$PHOTO_RESTORE_OUT` replaces it, `--output`
names another root). With the CSV layout: `<out>/<tier>/originals/` (copies),
`<out>/<tier>/restored/<name>.jpg` (results, q95, EXIF copied from the
original), `<out>/<tier>/work/` (`<name>.raw.png` model output,
`<name>.mask.png`, `<name>.regions.jpg` numbered regions, `<name>.compare.jpg`
original | result). For a loose image: `~/Downloads/photo-restore/restored/`
+ `work/`. Sources (the Immich library, any folder) are never written to.
Existing results are skipped, so a folder run resumes; `--force` redoes.

## Arguments Diego gives → what you run

| Diego says | Run |
| --- | --- |
| "restore the photos from the report / the CSV" | `select_photos.py <csv>` then, tier by tier, `restore.py <out>/Severe/originals` … (see Workflow) |
| "only the severe ones", "the moderate and severe" | `select_photos.py <csv> --tier Severe,Moderate` |
| "fix the colours", "the faded ones", the **Light** tier | `restore.py <folder> --mode color` (no model, ~1 s/photo) |
| "restore this photo/folder" (no CSV) | `restore.py <path>` (mode full) |
| "the sepia one should stay sepia", "don't neutralise the tone" | add `--wb 0` |
| "too strong / too contrasty" | `--contrast 0.6` (0 = off), `--strength` is on `fix_color.py` only |
| "repair and also fix the colours", a damaged print that is also faded | `--fix-color` (off by default: a repair keeps the original colours) |
| "use Qwen", "try the other model" | `--backend qwen` (~100 s/photo, changes faces more - klein is the default for a reason) |
| "try another version" | `--seed <other>` (and `--force`) |
| "it's a big scan / keep it sharp" | `--hires` (only matters above ~1.3 MP) |
| the **Digital** tier (broken files) | `fix_broken.py <files>`; `--crop-strip` for the truncated one with the grey strip |

## Workflow

**Before the first model run of a session:** `comfy_client.py --check`. If
klein is unavailable: ComfyUI must be running (`COMFYUI_SERVICE` in
`~/.config/photo-restore/comfyui.env` lets the scripts start the systemd
--user unit; `COMFYUI_URL` for another host) — tell Diego what is missing,
never fall back to a paid service.

1. `select_photos.py` the CSV (all tiers, or the tiers Diego named). Report:
   copied / already there / missing.
2. **Light tier, colour only:** `restore.py <out>/Light/originals --mode color`,
   then `contact_sheet.py <out>/Light/originals <out>/Light/restored` and look
   at every sheet. Typical fixes: `--wb 0` for a print whose tone is on purpose
   (sepia, sunset), `--contrast 0.6` for a print that went harsh. Re-run those
   single files with `--force`.
3. **Severe and Moderate, one photo at a time in the beginning** (repair only,
   original colours; add `--fix-color` only when Diego asks): run the first
   photo alone, open its `work/<name>.compare.jpg` and `work/<name>.regions.jpg`,
   and check (a) every damaged area is red in the regions image, (b) nothing
   real is red — a face, a hand, a pattern the model "improved" — and (c) the
   repaired areas look like the rest of the photo. Then run the rest of the
   folder (15–30 s per photo with klein on the 0.3–1.2 MP scans of the
   report; above ~10 photos run it in the background) and QC each compare sheet the same way.
4. **Fixing a mask** (free, no model call — `--reuse-raw`):
   - damage left untouched: it is under the threshold (white damage on white
     clothes is the usual case) → `--threshold 12` (measured: catches flakes on
     a white robe, but then a face or two show up as regions → `--drop` them;
     8 floods the whole photo, and a heavily damaged print already masks
     60 % at the default 22, so never lower it there), or `--include N` if it
     has a yellow number, or `--add x,y,w,h` (pixel box on the original) if it
     has none;
   - something real was repainted (green region on a face/hand/pattern) →
     `--drop N`; when it is part of one big region (the model shifted the
     whole scene a little, so the region wraps around the person) →
     `--protect x,y,w,h` with a box around the person, which keeps the
     original there;
   - the model's fill is wrong (a hallucinated object, a duplicated person) →
     `--seed <other> --force` for a new raw, then QC again; or `--backend qwen`.
   - a blotch that covered people (Severe prints) comes back with invented
     people, and a cut print (heart, arch) comes back as a full rectangle with
     invented surroundings: that is the best any tool can do, and Diego must
     be told which photos had content invented, not just repaired.
5. **Report per tier:** approved / fixed / flagged (photos you could not get
   right; say what is wrong), and where the results are. Show Diego the
   compare sheets of the photos you changed the most. Ask before touching
   another tier only if he asked for one tier.

## What "only the damage changes" means

The model repaints the whole photo (and, left alone, would smooth faces and
move details). `restore.py` aligns the output to the original, matches its
colours to the original, and takes the model's pixels only where the two
differ strongly (the damage it repaired) — feathered 4 px. Faces, hands,
clothes outside the mask are the scan's own pixels, in the scan's own
colours - the model's pixels are colour-matched to the scan before pasting,
so a repair does not shift the tone of the photo. The colour fix (`--mode
color`, or `--fix-color` on a repair) is a plain auto-levels + half
grey-world + light CLAHE, no AI, and runs only when asked. So a restored photo
is trustworthy as a record: what is new is exactly the red area in
`regions.jpg`, and you looked at it.

Do not deliver a photo whose face was inside the mask without telling Diego
that this face was redrawn.

## Digital tier

`fix_broken.py` re-encodes; the three panoramas only need that. The
truncated file: `--crop-strip` removes the flat grey rows; otherwise they stay.
EXIF comes over through exiftool (setup.sh warns if it is missing).
