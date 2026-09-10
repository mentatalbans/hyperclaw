The documentation peer serves only the admitted public Markdown/text snapshot at `/docs`, using `mcp==2.0.0` SDK dispatch. It has no installation or download step at startup.

Build explicitly from the repository root:

```sh
uv export --locked --extra mcp --no-dev --no-emit-project -o examples/mcp-docs/requirements.lock
docker build -f examples/mcp-docs/Dockerfile -t hyperclaw-mcp-docs .
docker image inspect -f '{{.Id}}' hyperclaw-mcp-docs
```

Configure `mcp_docs_path` as an absolute public-only directory and `mcp_docs_image` as the printed immutable `sha256:` ID (or a repository digest). Then use authenticated `hyperclaw mcp inspect`, `hyperclaw mcp admit --expected-sha256 HASH`, and explicitly select `--tool mcp_docs_read` / `--tool mcp_docs_search` for chat. The inspect fingerprint covers source file hashes, the closed catalog, protocol, image and sandbox policy. Changes require renewed admission. `hyperclaw mcp revoke` preserves historical evidence and snapshots.

Pass the image ID explicitly to tests with `--mcp-docs-image` or `MCP_DOCS_IMAGE`. Tests never build or download a peer implicitly. The two supported revisions are `2026-07-28` and `2025-11-25`.

MCP tools support directly submitted runs, including detached chat. Schedule creation rejects MCP names because admission and revocation binding for scheduled occurrences is not defined in M5.
