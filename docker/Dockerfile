# StoreyPath Studio: the whole tool in one container, the same for every machine (amd64
# or arm64). No GPU needed: the language model runs on the CPU. No network needed at
# run time: the model is fetched before the build and baked in, and Studio's database,
# PostgreSQL 17 with PostGIS 3, runs inside. A GPU helps through the optional GPU
# helper, a separate image (docker/gpu-helper/Dockerfile).
#
#   docker/fetch-models.sh                                  # once, before building
#   docker build -f docker/Dockerfile -t storeypath/studio .
#   docker run -d --name storeypath -p 8080:8080 -v storeypath:/data storeypath/studio
#   docker logs storeypath                                  # where it is, its certificate
#
# Open https://localhost:8080 (or the machine's address) and log in as admin, password
# admin (or set -e STOREYPATH_ADMIN_PASSWORD=… for another at the first start; change
# it in the person menu). Everything Studio keeps is in the one volume, /data: its
# database (/data/pg), its certificate (/data/tls) and caches. docker/start.sh starts
# the database (made the first time; a Unix socket only, no port), then Studio, and
# stops the database cleanly after Studio on `docker stop`. Another PostgreSQL (with
# PostGIS) instead: -e STOREYPATH_DATABASE_URL=postgresql://user:password@host/db.
# psql, pg_dump and pg_restore (17) are inside: docker exec -it storeypath psql.
#
# Studio serves HTTPS with a certificate it makes in /data/tls the first time: browsers
# warn once about it (the log prints its SHA-256 fingerprint, to compare). To reach it
# by a host name other than localhost, give that name:
# -e STOREYPATH_ALLOWED_HOSTS=gpu-server,studio.example; it goes on the certificate (and
# Studio refuses requests addressed to other names, against DNS rebinding). Your own
# certificate: mount it and end the run line with `serve --host 0.0.0.0 --port 8080
# --data /data --cert /tls/cert.pem --key /tls/key.pem`; behind a proxy that speaks
# HTTPS, `serve … --http --secure-cookies --trusted-proxy <its address>` (it must set
# X-Real-IP). Users, sharing and backups: README.md.
# Air-gapped: build (or `docker save`) on a connected machine, `docker load` on the
# isolated one:
#
#   docker save storeypath/studio | gzip > storeypath-studio.tar.gz
#   docker load -i storeypath-studio.tar.gz
#
# Nothing in it reaches out; its command-line tool runs with --network none. Vision
# models on GPU helpers (docker/gpu-helper), or any OpenAI-compatible one that takes
# images: -e STOREYPATH_VISION_URL=https://gpu1:8105/v1,https://gpu2:8105/v1 and
# -e STOREYPATH_VISION_KEY=<the helpers' key>; views of the plans and texts of the
# drawings are then sent to them.
#
# Optional, for research only: docker/fetch-symbols.sh before building bakes in
# SymPoint-V2, which types unnamed rooms by the fixtures drawn in them. It is not
# StoreyPath's and its terms do not allow publishing it (see docker/symbols/README.md);
# without it the image is built as before. The release workflow fetches it only when
# the repository variable STOREYPATH_SYMBOLS is "research".
#
# Inside: StoreyPath Studio (Apache-2.0), with pyca/cryptography (Apache-2.0 or BSD) to
# make its certificate; PostgreSQL (PostgreSQL Licence) with PostGIS (GPL-2.0-or-later)
# and the libraries Debian builds them with, as Debian packages (copyrights in
# /usr/share/doc); tini (MIT); LibreDWG's dwg2dxf for reading DWG
# (GPL-3.0-or-later, a separate program; its exact source is in
# /usr/share/storeypath/source); llama.cpp's llama-server (MIT); a Qwen3.5 model
# (Apache-2.0) for reading the text in drawings; three.js (MIT), MapLibre GL JS
# (BSD-3-Clause) and JSZip (MIT) for the viewer; Node.js (MIT), the program alone, to
# build the floors' 3D ahead of time when exporting. Licences: /usr/share/storeypath/licenses.

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
# The release tarball, as GNU publishes it (also attached to the GitHub release, for
# networks that cannot reach ftp.gnu.org).
ARG LIBREDWG_SHA256=cb6ee0b078c6d9e0f09d66f1feac33ba6342df88ae544e9f9335fab475218351
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config python3 curl ca-certificates xz-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN f="libredwg-${LIBREDWG_VERSION}.tar.gz" \
    && { curl -fsSL --connect-timeout 20 -o "$f" "https://ftp.gnu.org/gnu/libredwg/$f" \
         || curl -fsSL --connect-timeout 20 -o "$f" "https://github.com/LibreDWG/libredwg/releases/download/${LIBREDWG_VERSION}/$f"; } \
    && echo "${LIBREDWG_SHA256}  $f" | sha256sum -c - \
    && tar xf "$f"
RUN cd "libredwg-${LIBREDWG_VERSION}" \
    && ./configure --prefix=/opt/libredwg --disable-bindings --disable-static --disable-docs \
    && make -j"$(nproc)" \
    && make install \
    && mkdir -p /opt/source /opt/licenses \
    && cp "/src/libredwg-${LIBREDWG_VERSION}.tar.gz" /opt/source/ \
    && cp COPYING /opt/licenses/LICENSE-libredwg.txt

# ---- the 2D plan engine (viewer/svg), compiled from its TypeScript; and Node.js, ---------
# whose program alone goes into the image: Studio runs the viewer's baker with it
# (viewer/world/bake.mjs, in Studio's package, with three.js and JSZip beside it)
FROM node:22-slim AS plan
COPY viewer/svg /build/svg
RUN cd /build/svg && npm ci --no-audit --no-fund && npm run build

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
COPY --from=plan /build/svg/dist /build/viewer/svg/dist
RUN cd /build/studio && uv build --wheel -o /build/dist \
    && VIRTUAL_ENV=/opt/storeypath uv pip install --no-cache "$(ls /build/dist/*.whl)[vision]"

# ---- the image -------------------------------------------------------------------------
FROM debian:${DEBIAN}-slim
ARG MODEL=Qwen3.5-4B-Q4_K_M.gguf
# Studio's database: PostgreSQL and PostGIS as Debian packages (trixie has 17 and 3.5),
# with psql, pg_dump and pg_restore of the same version. The packages would make a
# cluster of their own: they are told not to (docker/start.sh makes Studio's in /data/pg).
ARG PG=17
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-common \
    && sed -ri 's/^#?[[:space:]]*create_main_cluster[[:space:]]*=.*/create_main_cluster = false/' \
           /etc/postgresql-common/createcluster.conf \
    && apt-get install -y --no-install-recommends python3 libgomp1 tini \
           "postgresql-${PG}" "postgresql-${PG}-postgis-3" \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --home-dir /home/storeypath storeypath \
    && mkdir -p /data /run/postgresql \
    && chown storeypath /data \
    && chown storeypath:storeypath /run/postgresql && chmod 0700 /run/postgresql
COPY --from=llama /opt/llama /opt/llama
COPY --from=libredwg /opt/libredwg /opt/libredwg
COPY --from=libredwg /opt/source /usr/share/storeypath/source
COPY --from=libredwg /opt/licenses /usr/share/storeypath/licenses
COPY --from=studio /opt/storeypath /opt/storeypath
COPY --from=studio /build/symbols /opt/storeypath/symbols
COPY --from=plan /usr/local/bin/node /opt/node/bin/node
COPY --from=plan /usr/local/LICENSE /usr/share/storeypath/licenses/LICENSE-node.js.txt
COPY docker/models/${MODEL} /opt/storeypath/models/${MODEL}
COPY viewer/vendor/LICENSE-maplibre-gl.txt viewer/vendor/LICENSE-jszip.markdown /usr/share/storeypath/licenses/
COPY viewer/vendor/three/LICENSE /usr/share/storeypath/licenses/LICENSE-three.js.txt
COPY docker/start.sh /usr/local/bin/storeypath-start
RUN cp /opt/llama/LICENSE /usr/share/storeypath/licenses/LICENSE-llama.cpp.txt \
    && cp /opt/storeypath/lib/python3*/site-packages/storeypath-*.dist-info/licenses/LICENSE \
          /usr/share/storeypath/licenses/LICENSE-storeypath.txt \
    && printf 'Qwen3.5 model weights: Apache License 2.0, Copyright Alibaba Cloud (Qwen team).\nhttps://huggingface.co/Qwen\n' \
          > /usr/share/storeypath/licenses/LICENSE-model.txt \
    && for p in "postgresql-${PG}" "postgresql-${PG}-postgis-3" tini; do \
           cp "/usr/share/doc/$p/copyright" "/usr/share/storeypath/licenses/COPYRIGHT-$p.txt"; \
       done \
    && if [ -f /opt/storeypath/symbols/weights/best.pth ]; then \
        printf '%s\n' 'SymPoint-V2 (https://github.com/nicehuster/SymPointV2): no licence stated; weights' \
          'trained on FloorPlanCAD (CC BY-NC 4.0). Included for research use only: this image' \
          'must not be published or used commercially.' > /usr/share/storeypath/licenses/NOTICE-SymPoint-V2.txt; \
    fi

ENV PATH="/opt/storeypath/bin:/opt/llama:/opt/libredwg/bin:/usr/lib/postgresql/${PG}/bin:${PATH}" \
    LD_LIBRARY_PATH="/opt/llama:/opt/libredwg/lib" \
    STOREYPATH_MODELS=/opt/storeypath/models \
    STOREYPATH_SYMBOLS=/opt/storeypath/symbols \
    STOREYPATH_NODE=/opt/node/bin/node \
    STOREYPATH_DATABASE_URL="postgresql:///storeypath?host=/run/postgresql" \
    PGDATA=/data/pg \
    PGHOST=/run/postgresql \
    PYTHONUNBUFFERED=1

USER storeypath
WORKDIR /data
VOLUME /data
EXPOSE 8080
# a check from inside, of Studio's own page (no login needed): over HTTPS, its own
# certificate not checked; over plain HTTP when it was started with --http
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python3 -c "import ssl, urllib.request as u; u.urlopen('https://127.0.0.1:8080/login.html', timeout=4, context=ssl._create_unverified_context())" \
     || python3 -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/login.html', timeout=4)"
LABEL org.opencontainers.image.title="StoreyPath Studio" \
      org.opencontainers.image.description="Turn DWG/DXF floor plans into portable indoor maps with stable IDs; runs offline" \
      org.opencontainers.image.source="https://github.com/StoreyPath/storeypath" \
      org.opencontainers.image.licenses="Apache-2.0 AND PostgreSQL AND GPL-2.0-or-later AND GPL-3.0-or-later AND MIT AND BSD-3-Clause"
# tini passes docker stop's SIGTERM on to the start script, which stops Studio, then
# the database; the command is Studio's (`docker run … storeypath/studio users list`)
ENTRYPOINT ["tini", "--", "storeypath-start"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080", "--data", "/data"]
