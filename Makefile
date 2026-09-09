PYTHON ?= .venv/bin/python
OLLAMA_URL ?= http://127.0.0.1:11434
OLLAMA_MODEL ?= qwen3.8:27b-mlx

.PHONY: test test-full test-live test-all test-coverage

test:
	$(PYTHON) scripts/test_battery.py quick

test-full:
	$(PYTHON) scripts/test_battery.py full

test-live:
	$(PYTHON) scripts/test_battery.py live --ollama-url "$(OLLAMA_URL)" --ollama-model "$(OLLAMA_MODEL)"

test-all:
	$(PYTHON) scripts/test_battery.py all --coverage --ollama-url "$(OLLAMA_URL)" --ollama-model "$(OLLAMA_MODEL)"

test-coverage:
	$(PYTHON) scripts/test_battery.py full --coverage
