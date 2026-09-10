# Trusted Linux verification runner

This runner verifies the project on Linux from a non-root account. It is a
trusted test harness: it mounts the Docker socket so the Docker-marked tests
can exercise `DockerBackend`. The socket is never mounted in an Executor tool
container.

From the repository root, build the pinned runner image for the current user:

```sh
runner_tag=hyperclaw-verification:runtime-v2
docker build \
  --build-arg VERIFY_UID="$(id -u)" \
  --build-arg VERIFY_GID="$(id -g)" \
  --tag "$runner_tag" \
  containers/verification
runner_image="$(docker image inspect --format '{{.Id}}' "$runner_tag")"
```

Create one disposable host directory and discover the Docker socket's group as
seen inside the runner:

```sh
verify_root="$(mktemp -d /tmp/hyperclaw-linux.XXXXXX)"
mkdir -p "$verify_root/home" "$verify_root/shared-tmp"
socket_gid="$(docker run --rm \
  --entrypoint stat \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock,readonly \
  "$runner_image" -c %g /var/run/docker.sock)"
```

For the Docker battery, first [build the documentation peer](../../examples/mcp-docs/README.md)
and resolve its immutable image ID. The runner never builds or downloads that peer:

```sh
mcp_docs_image="$(docker image inspect --format '{{.Id}}' hyperclaw-mcp-docs)"
```

Run both the quick and Docker batteries:

```sh
docker run --rm \
  --name hyperclaw-runtime-v2-verification \
  --user "$(id -u):$(id -g)" \
  --group-add "$socket_gid" \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --workdir "$PWD" \
  --mount type=bind,src="$PWD",dst="$PWD",readonly \
  --mount type=bind,src="$verify_root",dst="$verify_root" \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --env HOME="$verify_root/home" \
  --env TMPDIR="$verify_root/shared-tmp" \
  --env UV_PROJECT_ENVIRONMENT="$verify_root/venv" \
  --env UV_CACHE_DIR="$verify_root/uv-cache" \
  --env VERIFY_REPORT_DIR="$verify_root/reports" \
  --env VERIFY_SOURCE_DIR="$verify_root/source" \
  --env MCP_DOCS_IMAGE="$mcp_docs_image" \
  "$runner_image"
```

The repository bind is read-only. The runner copies it into the disposable
directory before installing and testing. Quick tests use a container-local
`/tmp` directory so process-lock tests run on a native Linux filesystem.
Docker tests use the host-shared `TMPDIR`; their temporary workspaces must have
the same absolute path on the host and in the runner because the Docker daemon
resolves nested bind mounts on the host.

Set `VERIFY_QUICK=0` or `VERIFY_DOCKER=0` with an additional `--env` argument
to run only one battery. The peer image is only required when the Docker battery
is selected. The runner installs locked development and MCP dependencies;
browser and model checks are separate explicit gates described in the
[testing guide](../../docs/testing.md). Results remain in `$verify_root/reports` after the
runner exits. Remove the disposable directory and runner image when the
evidence is no longer needed.
