---
name: restore-photos
description: Restore scanned photo prints - water/emulsion damage, stains, scratches, cut corners repainted by a local image-edit model (FLUX.2 klein or Qwen-Image-Edit in ComfyUI, free, offline) but ONLY inside the damaged areas, every undamaged pixel and every face kept from the original; then automatic colour/contrast fix for faded or colour-cast prints; re-save of broken JPEGs. Takes an image, several images or a folder of scans (optionally recursive). Results in ~/Downloads/photo-restore/<folder name>/, with QC contact sheets; sources never touched; the agent QCs every result with Diego. Use when Diego asks to "restore this photo / these photos / this folder of scans", "fix the damaged prints", "restaurar as fotos", "remove the white blotches / water damage", "fix the colours of the old photos", or "this file won't open properly".
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
| `restore.py <image-or-folder>... [--mode full\|color] [--recursive]` | the restoration (model pass → damage mask → composite, original colours kept; `--fix-color` adds the colour fix; `--mode color` is the colour fix alone). A folder run ends with QC sheets in `<out>/sheets/` |
| `contact_sheet.py <originals> <restored>` | before/after sheets from two folders (when the results were moved around) |
| `fix_broken.py <jpg>... [--crop-strip]` | re-saves JPEGs with data-stream errors / truncated tails, EXIF kept |
| `inpaint.py <image> <mask> <out>` | native-resolution repaint of masked regions (big scans; `restore.py --hires` calls it) |
| `fix_color.py <image> <out>` | the colour fix alone, with all its knobs |
| `comfy_client.py --check` | is ComfyUI up, which backend has its models |

## Arguments

`/restore-photos <image-or-folder>... [what to do]`

- `image-or-folder`: one image, several images, or a folder of scans
  (`--recursive` for its sub-folders). If nothing is given, ask.
- what to do (optional): nothing = repair (mode full); "fix the colours" /
  "faded" = `--mode color`; "repair and fix the colours" = `--fix-color`.

## Where results go

Root `~/Downloads/photo-restore/` (`$PHOTO_RESTORE_OUT` replaces it,
`--output` names any directory):

| Input | Results |
| --- | --- |
| a folder `<name>/` | `<root>/<name>/restored/`, `<root>/<name>/work/`, `<root>/<name>/sheets/qc_NN.jpg` |
| loose images | `<root>/restored/`, `<root>/work/` |

`restored/<name>.jpg` is the result (q95, EXIF copied from the original);
`work/` holds `<name>.raw.png` (model output), `<name>.mask.png`,
`<name>.regions.jpg` (numbered regions on the original) and
`<name>.compare.jpg` (original | result); `sheets/` stacks the compare
images 4 per sheet after a folder run (`--no-sheets` skips it). Sources are
never written to. Existing results are
skipped, so a folder run resumes; `--force` redoes.

## Arguments Diego gives → what you run

| Diego says | Run |
| --- | --- |
| "restore this photo / these photos / this folder" | `restore.py <path>...` (mode full; `--recursive` when he says "including sub-folders") |
| "fix the colours", "the faded ones" | `restore.py <path> --mode color` (no model, ~1 s/photo) |
| "the sepia one should stay sepia", "don't neutralise the tone" | add `--wb 0` |
| "too strong / too contrasty" | `--contrast 0.6` (0 = off), `--strength` is on `fix_color.py` only |
| "repair and also fix the colours", a damaged print that is also faded | `--fix-color` (off by default: a repair keeps the original colours) |
| "use Qwen", "try the other model" | `--backend qwen` (~100 s/photo, changes faces more - klein is the default for a reason) |
| "try another version" | `--seed <other>` (and `--force`) |
| a light leak / burn (orange band, pale wash) the run left alone | run it in mode full (not `--mode color`); a strong orange band gets repainted, a pale wash over the scene does not — the models read it as light, and a prompt naming it (`--prompt`) did not help; say so |
| "it's a big scan / keep it sharp" | `--hires` (only matters above ~1.3 MP) |
| "this file won't open / is corrupted / has a grey strip at the bottom" | `fix_broken.py <files>`; `--crop-strip` for a truncated file with a flat grey strip |

## Workflow

**Before the first model run of a session:** `comfy_client.py --check`. If
klein is unavailable: ComfyUI must be running (`COMFYUI_SERVICE` in
`~/.config/photo-restore/comfyui.env` lets the scripts start the systemd
--user unit; `COMFYUI_URL` for another host) — tell Diego what is missing,
never fall back to a paid service.

**A photo or a folder (the usual case):**

1. Run the FIRST photo alone (`restore.py <that image> --output
   <root>/<folder name>` so it lands with the rest), open its
   `work/<name>.compare.jpg` and `work/<name>.regions.jpg`, and check
   (a) every damaged area is red in the regions image, (b) nothing real is
   red — a face, a hand, a pattern the model "improved" — and (c) the
   repaired areas look like the rest of the photo. Fix the mask if needed
   (step 3). This is where you learn whether the folder wants `--fix-color`
   or a different `--wb`.
2. Run the folder (15–30 s per photo with klein on 0.3–1.2 MP scans; above
   ~10 photos run it in the background). Open every `sheets/qc_NN.jpg` and
   judge each pair the same way. For `--mode color` the sheets are all there
   is to check: watch for a print whose tone was on purpose (sepia, sunset)
   and for cream/orange surfaces pushed to cyan.
3. **Fixing one result** (free, no model call — `--reuse-raw`):
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
   - a blotch that covered people comes back with invented
     people, and a cut print (heart, arch) comes back as a full rectangle with
     invented surroundings: that is the best any tool can do, and Diego must
     be told which photos had content invented, not just repaired.
   - colour fix too far (`--mode color` / `--fix-color`): `--wb 0.15–0.3` on
     prints with a cream or orange dominant surface, `--wb 0` for sepia,
     `--wb 1` for a real magenta/green cast, `--contrast 0` for harsh grain.
   A single-image re-run rewrites only that photo's files; look at its new
   `compare.jpg` rather than rebuilding the sheets.
4. **Report:** approved / fixed / flagged (photos you could not get right;
   say what is wrong and which ones had content invented), and where the
   results are. Show Diego the compare sheets of the photos you changed the
   most.

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

## Broken files

A JPEG with a data-stream error still opens but some viewers complain, and
a truncated file shows a flat grey strip where the bytes are missing.
`fix_broken.py` re-encodes them into `<root>/restored/`; `--crop-strip`
cuts the grey rows off instead of keeping them. EXIF comes over through
exiftool (setup.sh warns if it is missing).
