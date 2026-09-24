# photo-restore

One skill, `restore-photos/` (`SKILL.md` + `scripts/`), harness-neutral: this
file is read as `CLAUDE.md` (Claude Code) and, through a symlink, as
`AGENTS.md` (Codex). `.claude/skills/` and `.agents/skills/` hold symlinks to
the skill folder. Read `restore-photos/SKILL.md` before running any script.

- `setup.sh` creates `venv/` (opencv-python-headless, numpy, pillow) and
  writes an empty `~/.config/photo-restore/comfyui.env` (COMFYUI_URL,
  COMFYUI_SERVICE). ComfyUI and its models are installed elsewhere (README
  "Local AI models"); the scripts only talk to a running server.
- Scripts import each other from their own folder (`restore.py` uses
  `comfy_client.py`, `fix_color.py`, `inpaint.py`), so they run from
  anywhere but must stay in `restore-photos/scripts/`.
- Input is an image, several images or a folder (`--recursive`). Results
  always go to `~/Downloads/photo-restore/<folder name>` (loose images: the
  root; `$PHOTO_RESTORE_OUT` or `--output` change it); no script writes
  next to its input or into a photo library. No personal path, host or library location
  belongs in this repo, not even as a default.
- The model's pixels are used only inside the damage mask (`restore.py`
  `diff_regions` → `composite`), colour-matched to the scan first, and the
  colour fix is opt-in (`--fix-color`, `--mode color`). Keep that contract
  when changing anything: a restored photo must be the original, in the
  original's colours, everywhere it was not damaged.
