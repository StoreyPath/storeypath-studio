#!/bin/sh
# StoreyPath Studio with its vision model on the GPU: start the model server, wait
# until the model is loaded, then serve Studio. Arguments are passed to
# `storeypath serve`.
#
#   STOREYPATH_VISION=off        no vision model (rules and the language model only)
#   STOREYPATH_VISION_URL=...    use a vision model served elsewhere instead
#   STOREYPATH_VISION_PARALLEL   rooms looked at at once (default 2): the model server's
#                                slots, each of STOREYPATH_VISION_CONTEXT tokens
#   STOREYPATH_VISION_CONTEXT    tokens of context per question (default 8192)
#
# The model is STOREYPATH_VISION_GGUF with its image encoder STOREYPATH_VISION_MMPROJ
# (Dockerfile.gpu sets both), served on 127.0.0.1:8105 inside the container only. If
# it stops while loading (no GPU given: run with --gpus all), Studio runs without it.
#
# The model server's log: /tmp/vision.log (docker exec storeypath cat /tmp/vision.log).
set -eu
port=8105
log=/tmp/vision.log

if [ "${STOREYPATH_VISION:-on}" != off ] && [ -z "${STOREYPATH_VISION_URL:-}" ] && [ -s "${STOREYPATH_VISION_GGUF:-}" ]; then
    slots="${STOREYPATH_VISION_PARALLEL:-2}"
    echo "starting the vision model $(basename "$STOREYPATH_VISION_GGUF") on the GPU (log: $log)"
    llama-server -m "$STOREYPATH_VISION_GGUF" --mmproj "$STOREYPATH_VISION_MMPROJ" \
        --alias "$(basename "$STOREYPATH_VISION_GGUF" .gguf)" \
        --host 127.0.0.1 --port "$port" -ngl 99 \
        -c "$(( ${STOREYPATH_VISION_CONTEXT:-8192} * slots ))" -np "$slots" -b 4096 -ub 4096 \
        --image-min-tokens 1024 --jinja --no-webui > "$log" 2>&1 &
    server=$!
    STOREYPATH_VISION_URL="http://127.0.0.1:$port/v1"
    waited=0
    until python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$port/health', timeout=2)" 2>/dev/null; do
        if ! kill -0 "$server" 2>/dev/null; then
            echo "the vision model stopped:"
            tail -n 15 "$log"
            echo "Studio runs without vision (is there a GPU? run with --gpus all)"
            STOREYPATH_VISION_URL=""
            break
        fi
        waited=$((waited + 2))
        if [ "$waited" -ge 900 ]; then
            echo "the vision model is still loading; Studio uses it once it answers"
            break
        fi
        sleep 2
    done
    if [ -n "$STOREYPATH_VISION_URL" ]; then
        export STOREYPATH_VISION_URL
        grep -m1 -o "using device CUDA[0-9]* ([^)]*)" "$log" || true
        echo "vision model ready"
    else
        unset STOREYPATH_VISION_URL
    fi
fi

exec storeypath serve --host 0.0.0.0 --port 8080 --data /data "$@"
