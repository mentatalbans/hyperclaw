# Trusted test harness only. It mounts the Docker socket to exercise DockerBackend;
# this image is never used as an Executor tool container.
FROM ghcr.io/astral-sh/uv:0.8.3-python3.13-bookworm-slim@sha256:0e781e30ba62d0c9feb3f91da614a58d3486e23c0d0038c748e3218cc2951dec

USER 0:0
RUN apt-get update \
    && apt-get install --yes --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/*

ARG VERIFY_UID=1000
ARG VERIFY_GID=1000
RUN (getent group "$VERIFY_GID" >/dev/null || groupadd --gid "$VERIFY_GID" verifier) \
    && (getent passwd "$VERIFY_UID" >/dev/null \
        || useradd --uid "$VERIFY_UID" --gid "$VERIFY_GID" --home-dir /tmp/hyperclaw-verifier --create-home verifier)

COPY run.sh /usr/local/bin/hyperclaw-linux-verify
RUN chmod 0755 /usr/local/bin/hyperclaw-linux-verify

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/hyperclaw-linux-verify"]
