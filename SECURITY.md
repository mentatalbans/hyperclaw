# Security model

Runtime v2 targets one local operator on macOS/Linux. The default root is ~/.hyperclaw-v2. Setup refuses existing unmarked/v1 roots. Never point tests at personal data. Runtime files and the operator token belong to that operator; keep the token private (0600) and root private (0700).

M1 is text chat only. It has no tool execution, shell fallback, model-authorized policy changes, or imported skills. The configured Ollama endpoint is explicit; there is no fallback or automatic download. A loopback endpoint does not prove the model server itself is offline.

The authenticated daemon and durable run controls are implemented in the following M1 tasks. Later execution policy and Docker ownership gates must pass before enabling tools. Switching any existing service requires a separate operational action.
