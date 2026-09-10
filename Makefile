PYTHON ?= .venv/bin/python
COVERAGE ?= 0
MCP_DOCS_IMAGE ?=
OLLAMA_URL ?= http://127.0.0.1:11434
OLLAMA_MODEL ?= qwen3.8:27b-mlx

.PHONY: test test-docker test-live test-all test-coverage

test:
	$(PYTHON) scripts/test_battery.py quick $(if $(filter 1,$(COVERAGE)),--coverage,)

test-docker:
	$(PYTHON) scripts/test_battery.py docker --mcp-docs-image "$(MCP_DOCS_IMAGE)"

test-live:
	$(PYTHON) scripts/test_battery.py live --ollama-url "$(OLLAMA_URL)" --ollama-model "$(OLLAMA_MODEL)"

test-all:
	$(PYTHON) scripts/test_battery.py all --mcp-docs-image "$(MCP_DOCS_IMAGE)" --coverage --ollama-url "$(OLLAMA_URL)" --ollama-model "$(OLLAMA_MODEL)"

test-coverage:
	$(PYTHON) scripts/test_battery.py quick --coverage

.PHONY: test-live-mcp
test-live-mcp:
	$(PYTHON) scripts/test_battery.py live --with-docker --mcp-docs-image "$(MCP_DOCS_IMAGE)" --ollama-url "$(OLLAMA_URL)" --ollama-model "$(OLLAMA_MODEL)"
