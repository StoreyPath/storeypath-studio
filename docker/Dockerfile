# StoreyPath Studio: the whole tool in one container. CPU only; no network needed
# at run time.
#
#   docker/fetch-models.sh                                  # once, before building
#   docker build -f docker/Dockerfile -t storeypath/studio .
#   docker run -p 8080:8080 -v storeypath:/data storeypath/studio
#
# then open http://localhost:8080. Air-gapped: build (or `docker save`) on a
# connected machine, `docker load` on the isolated one; run with --network none
# if you like — nothing in it reaches out.
#
# Optional, for research only: docker/fetch-symbols.sh before building bakes in
# SymPoint-V2, which types unnamed rooms by the fixtures drawn in them. It is not
# StoreyPath's and its terms do not allow publishing it (see docker/symbols/README.md);
# without it the image is built as before.
#
# Inside: StoreyPath Studio (Apache-2.0); LibreDWG's dwg2dxf for reading DWG
# (GPL-3.0-or-later, a separate program; its exact source is in
# /usr/share/storeypath/source); llama.cpp's llama-server (MIT); a Qwen3.5 model
# (Apache-2.0) for reading the text in drawings; three.js (MIT), MapLibre GL JS
# (BSD-3-Clause) and JSZip (MIT) for the viewer. Licences: /usr/share/storeypath/licenses.

ARG DEBIAN=trixie

# ---- llama.cpp: the local language model server ------------------------------
FROM debian:${DEBIAN}-slim AS llama
ARG LLAMA_CPP_VERSION=v0.5.0
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${LLAMA_CPP_VERSION}" https://github.com/ggml-org/llama.cpp /src
# CPU backends for every instruction-set level are built and the fastest one the
# machine supports is picked at start, so one image runs on any x86-64 or arm64 CPU.
RUN cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release \
        -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON \
        -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON \
    && cmake --build /build --target llama-server -j"$(nproc)" \
    && mkdir -p /opt/llama \
    && cp /build/bin/llama-server /opt/llama/ \
    && find /build -name '*.so*' -exec cp -P {} /opt/llama/ \; \
    && cp /src/LICENSE /opt/llama/LICENSE

# ---- LibreDWG: reading DWG ---------------------------------------------------------
FROM debian:${DEBIAN}-slim AS libredwg
ARG LIBREDWG_VERSION=0.14
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config python3 curl ca-certificates xz-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSLO "https://ftp.gnu.org/gnu/libredwg/libredwg-${LIBREDWG_VERSION}.tar.xz" \
    && tar xf "libredwg-${LIBREDWG_VERSION}.tar.xz"
RUN cd "libredwg-${LIBREDWG_VERSION}" \
    && ./configure --prefix=/opt/libredwg --disable-bindings --disable-static --disable-docs \
    && make -j"$(nproc)" \
    && make install \
    && mkdir -p /opt/source /opt/licenses \
    && cp "/src/libredwg-${LIBREDWG_VERSION}.tar.xz" /opt/source/ \
    && cp COPYING /opt/licenses/LICENSE-libredwg.txt

# ---- StoreyPath Studio ---------------------------------------------------------------
FROM debian:${DEBIAN}-slim AS studio
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
RUN uv venv --python /usr/bin/python3 /opt/storeypath
# SymPoint-V2, when docker/fetch-symbols.sh put it there: PyTorch (CPU only) and what
# its code needs, before Studio so a change to Studio does not fetch them again.
# Otherwise nothing of it goes in.
ARG TORCH_VERSION=2.14.1
COPY docker/symbols /build/symbols
RUN if [ -f /build/symbols/weights/best.pth ]; then \
        VIRTUAL_ENV=/opt/storeypath uv pip install --no-cache \
            --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}" \
        && VIRTUAL_ENV=/opt/storeypath uv pip install --no-cache "scipy>=1.13" "munch>=4"; \
    else \
        rm -rf /build/symbols && mkdir /build/symbols; \
    fi
COPY studio /build/studio
COPY spec /build/spec
COPY viewer /build/viewer
RUN cd /build/studio && uv build --wheel -o /build/dist \
    && VIRTUAL_ENV=/opt/storeypath uv pip install --no-cache /build/dist/*.whl

# ---- the image -------------------------------------------------------------------------
FROM debian:${DEBIAN}-slim
ARG MODEL=Qwen3.5-4B-Q4_K_M.gguf
RUN apt-get update && apt-get install -y --no-install-recommends python3 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --home-dir /home/storeypath storeypath \
    && mkdir -p /data && chown storeypath /data
COPY --from=llama /opt/llama /opt/llama
COPY --from=libredwg /opt/libredwg /opt/libredwg
COPY --from=libredwg /opt/source /usr/share/storeypath/source
COPY --from=libredwg /opt/licenses /usr/share/storeypath/licenses
COPY --from=studio /opt/storeypath /opt/storeypath
COPY --from=studio /build/symbols /opt/storeypath/symbols
COPY docker/models/${MODEL} /opt/storeypath/models/${MODEL}
COPY viewer/vendor/LICENSE-maplibre-gl.txt viewer/vendor/LICENSE-jszip.markdown /usr/share/storeypath/licenses/
COPY viewer/vendor/three/LICENSE /usr/share/storeypath/licenses/LICENSE-three.js.txt
RUN cp /opt/llama/LICENSE /usr/share/storeypath/licenses/LICENSE-llama.cpp.txt \
    && cp /opt/storeypath/lib/python3*/site-packages/storeypath-*.dist-info/licenses/LICENSE \
          /usr/share/storeypath/licenses/LICENSE-storeypath.txt \
    && printf 'Qwen3.5 model weights: Apache License 2.0, Copyright Alibaba Cloud (Qwen team).\nhttps://huggingface.co/Qwen\n' \
          > /usr/share/storeypath/licenses/LICENSE-model.txt \
    && if [ -f /opt/storeypath/symbols/weights/best.pth ]; then \
        printf '%s\n' 'SymPoint-V2 (https://github.com/nicehuster/SymPointV2): no licence stated; weights' \
          'trained on FloorPlanCAD (CC BY-NC 4.0). Included for research use only: this image' \
          'must not be published or used commercially.' > /usr/share/storeypath/licenses/NOTICE-SymPoint-V2.txt; \
    fi

ENV PATH="/opt/storeypath/bin:/opt/llama:/opt/libredwg/bin:${PATH}" \
    LD_LIBRARY_PATH="/opt/llama:/opt/libredwg/lib" \
    STOREYPATH_MODELS=/opt/storeypath/models \
    STOREYPATH_SYMBOLS=/opt/storeypath/symbols \
    PYTHONUNBUFFERED=1

USER storeypath
WORKDIR /data
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=4)"
LABEL org.opencontainers.image.title="StoreyPath Studio" \
      org.opencontainers.image.description="Turn DWG/DXF floor plans into portable indoor maps with stable IDs; runs offline" \
      org.opencontainers.image.source="https://github.com/StoreyPath/storeypath" \
      org.opencontainers.image.licenses="Apache-2.0 AND GPL-3.0-or-later AND MIT AND BSD-3-Clause"
ENTRYPOINT ["storeypath"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080", "--data", "/data"]
