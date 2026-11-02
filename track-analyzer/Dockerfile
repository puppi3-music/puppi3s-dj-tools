# syntax=docker/dockerfile:1
# Puppi3's Track Analyzer in a Linux x86_64 container (Essentia has no Linux arm64 build). See compose.yaml.
#   app:  the analyzer and its web page; music, Traktor folder and data are mounted from your computer
#   test: app + the models + a synthetic library (scripts/make_fixtures.py), for CI and development.
#         Built locally, never published: the models' licence doesn't allow passing them on.
# amd64 even when built on an arm64 machine (Apple Silicon: runs emulated)
FROM --platform=linux/amd64 ghcr.io/astral-sh/uv:0.13.4 AS uv
FROM --platform=linux/amd64 python:3.11.13-slim-bookworm AS app
COPY --from=uv /uv /bin/uv
# Debian 12's Rubber Band is 3.1 (the R3 engine the tempo view needs)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg rubberband-cli \
    && rm -rf /var/lib/apt/lists/*
# TRACK_ANALYZER_CONTAINER: Traktor writes ask whether Traktor is closed (pgrep can't see it from here)
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3.11 \
    UV_PROJECT_ENVIRONMENT=/opt/venv VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH \
    TRACK_ANALYZER_HOME=/data TRACK_ANALYZER_CONTAINER=1 HOME=/home/dj
# Opt-in extras as on a Mac, e.g. --build-arg EXTRAS="--extra stems"
ARG EXTRAS=""
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project $EXTRAS
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen $EXTRAS
# Any user id can run it (compose passes yours, so files it writes in your music and Traktor folder stay yours)
RUN mkdir -p /data /home/dj /app/logs && chmod 777 /data /home/dj /app/logs
EXPOSE 8766
CMD ["dj-track-analyzer", "web", "--host", "0.0.0.0", "--no-open"]

# The models on their own, from the model list alone: a code change doesn't download them again (a few hundred MB)
FROM --platform=linux/amd64 python:3.11.13-slim-bookworm AS models
COPY src/dj_track_analyzer/model_files.py /tmp/model_files.py
RUN python /tmp/model_files.py /models

FROM app AS test
# --chmod in the copy itself: a later layer changing their mode would hold a second copy of the models
COPY --from=models --chmod=777 /models /data/models
RUN dj-track-analyzer setup && chmod -R a+rwX /data /home/dj
COPY scripts ./scripts
RUN python scripts/make_fixtures.py --music /fixtures/music \
        --nml "/home/dj/Documents/Native Instruments/Traktor 4.0.0/collection.nml" \
    && chmod -R a+rwX /fixtures /home/dj
CMD ["python", "scripts/docker_smoke.py"]

# CI: the smoke test as the last build step (as any user id, as compose runs it), so the 2 GB image never has to be
# exported and loaded into Docker to run one container. Unchanged code = this step cached = nothing to test again.
FROM test AS smoke
USER 1000:1000
RUN python scripts/docker_smoke.py
