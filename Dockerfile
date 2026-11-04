# presto-photos: the upload page (`web`) and the converter (`convert`), one image.
# Built and pushed by brick-cicd-config's build-image Jenkins job (a reproducible
# rootless BuildKit build on brick2000): IMAGE=whitepatrick/presto-photos,
# REF=<full commit>, TAG=<version>.
#
# The base is pinned by digest (the same as overwatch's) and dependencies come
# from poetry.lock. pillow-heif's wheel carries its own libheif and libde265, so
# no apt packages are needed. Bump the base deliberately, with the digest from:
#   docker buildx imagetools inspect python:3.11-slim-bookworm
ARG PYTHON=python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b

FROM ${PYTHON} AS deps
ARG POETRY_VERSION=2.1.2
ENV PIP_DEFAULT_TIMEOUT=120 POETRY_NO_INTERACTION=1 VIRTUAL_ENV=/venv PATH=/venv/bin:$PATH
# One package at a time: the parallel installer races on the umask (wigle-sync).
ENV POETRY_INSTALLER_PARALLEL=false
RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"
WORKDIR /code
COPY pyproject.toml poetry.lock /code/
# Poetry leaves its clone of a git dependency in /venv/src; the package is in
# site-packages, and the clone's .git/index changes every build (and the digest).
RUN python -m venv --without-pip /venv && poetry install --no-root --only main && rm -rf /venv/src

FROM ${PYTHON}
COPY --from=deps /venv /venv
# Read-only root filesystem in the cluster, so no .pyc files.
ENV VIRTUAL_ENV=/venv PATH=/venv/bin:$PATH PYTHONPATH=/code PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /code
# An explicit file list: a new module must be added here.
COPY presto_photos/__init__.py presto_photos/__main__.py presto_photos/pipeline.py presto_photos/store.py \
     presto_photos/web.py presto_photos/worker.py /code/presto_photos/
COPY presto_photos/static/index.html /code/presto_photos/static/

USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["python", "-m", "presto_photos"]
