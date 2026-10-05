#!/bin/sh
# Download SymPoint-V2, a model that spots the symbols drawn in floor plans (doors,
# toilets, stoves, stairs…), for typing the rooms of a plan that carry no name.
#
#   docker/fetch-symbols.sh          then build the image as usual: it is baked in
#
# FOR RESEARCH USE ONLY. SymPoint-V2 (https://github.com/nicehuster/SymPointV2) is
# not part of StoreyPath: its repository states no licence, and its weights were
# trained on FloorPlanCAD (CC BY-NC 4.0, non-commercial). Use it only where that is
# allowed, and do not publish images built with it. Images built without it (the
# default) work as before.
#
# Files land in docker/symbols/ (git-ignored): its code, pinned to one commit, and
# the trained weights, checked against the checksum below. Building the image
# needs them there; running it needs no network at all.
set -eu
cd "$(dirname "$0")"
COMMIT=24594875d89a36c1a91da748c999588c8599fe9b
WEIGHTS_ID=1ZeWtgZJKD_yWmFNWwBOMN9_4-x-ZXUuS  # the authors' Google Drive file, linked from their README
WEIGHTS_SHA256=6889f3b73a283aed002cde1c99ddb543fafa9bf2cabe65c9ced2ea3daf00c364
mkdir -p symbols
cd symbols

sha256() {
  if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

if [ -f SymPointV2/svgnet/model/svgnet.py ]; then
  echo "have SymPoint-V2 $COMMIT"
else
  echo "fetching SymPoint-V2 $COMMIT"
  curl -fL --retry 3 -o code.tar.gz "https://codeload.github.com/nicehuster/SymPointV2/tar.gz/$COMMIT"
  rm -rf SymPointV2 && mkdir SymPointV2
  # the network's code only: not the sample data and logs in the repository
  tar xzf code.tar.gz -C SymPointV2 --strip-components=1 \
    "SymPointV2-$COMMIT/svgnet" "SymPointV2-$COMMIT/modules" "SymPointV2-$COMMIT/README.md"
  rm code.tar.gz
fi

if [ -f weights/best.pth ] && [ -f weights/svg_pointT.yaml ]; then
  echo "have its weights"
else
  if [ ! -s weights.zip ]; then
    echo "fetching its weights (130 MB)"
    curl -fL --retry 3 -o weights.zip.part \
      "https://drive.usercontent.google.com/download?id=$WEIGHTS_ID&export=download&confirm=t"
    mv weights.zip.part weights.zip
  fi
  got="$(sha256 weights.zip)"
  if [ "$got" != "$WEIGHTS_SHA256" ]; then
    echo "checksum mismatch for the weights: $got (expected $WEIGHTS_SHA256)" >&2
    exit 1
  fi
  echo "checksum ok: weights"
  python3 - <<'PY'
import os, shutil, zipfile
os.makedirs("weights", exist_ok=True)
with zipfile.ZipFile("weights.zip") as z:
    for name in ("best.pth", "svg_pointT.yaml"):
        with z.open(f"spv2-rep/{name}") as src, open(f"weights/{name}", "wb") as dst:
            shutil.copyfileobj(src, dst)
PY
  rm weights.zip
fi
cat <<'NOTE'

SymPoint-V2 is in docker/symbols/. It is for RESEARCH USE ONLY: no licence is stated
for its code, and its weights come from non-commercial data (FloorPlanCAD, CC BY-NC).
Do not publish or sell images built with it.
NOTE
