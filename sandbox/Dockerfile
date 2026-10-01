# r2r sandbox: runs generated research code with no network, resource limits, no secrets.
# Built by `r2r sandbox build`; the tag is a hash of this folder, so edits trigger a rebuild.
FROM python:3.12-slim

ARG TECTONIC_VERSION=0.15.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# tectonic (LaTeX). Its package bundle is cached at build time because containers have no network.
RUN set -eux; \
    case "$(uname -m)" in \
      aarch64) target=aarch64-unknown-linux-musl ;; \
      x86_64) target=x86_64-unknown-linux-musl ;; \
      *) echo "unsupported arch $(uname -m)"; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-${target}.tar.gz" \
      | tar -xz -C /usr/local/bin tectonic; \
    tectonic --version

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

ENV TECTONIC_CACHE_DIR=/opt/tectonic-cache
COPY warmup.tex /opt/warmup/warmup.tex
RUN mkdir -p /opt/tectonic-cache /tmp/warm \
 && tectonic -o /tmp/warm /opt/warmup/warmup.tex \
 && rm -rf /tmp/warm \
 && chmod -R a+rX /opt/tectonic-cache

RUN useradd --create-home --uid 1000 sandbox
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    NUMBA_CACHE_DIR=/tmp/numba \
    MPLCONFIGDIR=/tmp/mpl
USER sandbox
WORKDIR /workspace
