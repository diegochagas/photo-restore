#!/usr/bin/env bash
# One-time setup. Safe to re-run.
#   1. creates the Python venv (<repo>/venv) with the deps of every script;
#   2. checks the optional tools: exiftool (keeps EXIF on re-saved files) and
#      a ComfyUI server with the Qwen-Image-Edit / FLUX.2 klein models (the
#      AI inpainting; installed separately, see README "Local AI models").
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "Creating venv..."
    python3 -m venv venv
    venv/bin/pip install --quiet --upgrade pip
fi
venv/bin/pip install --quiet opencv-python-headless numpy pillow
echo "venv ready: $(venv/bin/python -c 'import cv2; print("opencv", cv2.__version__)')"

command -v exiftool >/dev/null || echo "WARN: exiftool not found (apt install libimage-exiftool-perl) - EXIF will not be copied to results"

CONF="$HOME/.config/photo-restore/comfyui.env"
if [ ! -f "$CONF" ]; then
    mkdir -p "$(dirname "$CONF")"
    cat > "$CONF" <<'CFG'
# Local ComfyUI server used by restore-photos (AI inpainting). Both optional.
COMFYUI_URL=
COMFYUI_SERVICE=
CFG
    echo "Wrote $CONF - fill in COMFYUI_URL (default http://127.0.0.1:8188) and COMFYUI_SERVICE (systemd --user unit) if you have them."
fi
CCONF="$HOME/.config/photo-restore/compare.env"
if [ ! -f "$CCONF" ]; then
    cat > "$CCONF" <<'CFG'
# restore-photos review page (restore-photos/scripts/compare_server.py).
# Set before each review; --results / --originals override them.
COMPARE_RESULTS=
COMPARE_ORIGINALS=
COMPARE_PORT=8790
COMPARE_ALT=Higgsfield,Higgsfield 2
CFG
    echo "Wrote $CCONF - set COMPARE_RESULTS / COMPARE_ORIGINALS before a review."
fi
venv/bin/python restore-photos/scripts/comfy_client.py --check 2>/dev/null || true
echo "Setup complete."
