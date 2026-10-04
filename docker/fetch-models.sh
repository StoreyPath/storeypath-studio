#!/bin/sh
# Download the language model baked into the StoreyPath Studio image.
#
#   docker/fetch-models.sh          Qwen3.5-4B (the default: 97% on room names in eval/)
#   docker/fetch-models.sh 2B       a smaller one (86%), for a smaller image:
#                                   build with --build-arg MODEL=Qwen3.5-2B-Q4_K_M.gguf
#
# Files land in docker/models/ (git-ignored). Building the image needs them there;
# running it needs no network at all. Downloaded files are kept, and checked
# against the checksums below when they are known.
set -eu
cd "$(dirname "$0")"
mkdir -p models
cd models
QUANT="${QUANT:-Q4_K_M}"

known_sha256() {
  case "$1" in
    Qwen3.5-4B-Q4_K_M.gguf) echo 00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4 ;;
  esac
}

sha256() {
  if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

for size in ${*:-4B}; do
  file="Qwen3.5-$size-$QUANT.gguf"
  if [ -s "$file" ]; then
    echo "have $file"
  else
    echo "fetching $file"
    curl -fL --retry 3 -o "$file.part" "https://huggingface.co/unsloth/Qwen3.5-$size-GGUF/resolve/main/$file"
    mv "$file.part" "$file"
  fi
  want="$(known_sha256 "$file")"
  if [ -n "$want" ]; then
    got="$(sha256 "$file")"
    if [ "$got" != "$want" ]; then
      echo "checksum mismatch for $file: $got (expected $want)" >&2
      exit 1
    fi
    echo "checksum ok: $file"
  fi
done
ls -lh ./*.gguf
