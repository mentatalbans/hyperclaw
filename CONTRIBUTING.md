# Contributing to HyperClaw

HyperClaw is MIT-licensed and open for contributions. Here's how to get involved.

## Getting Started

```bash
git clone https://github.com/mentatalbans/hyperclaw.git
cd hyperclaw
pip install -e ".[dev]"
pytest tests/ -v  # confirm 184/184 passing
```

## Adding an Agent

1. Pick a domain: `swarm/agents/{personal,business,scientific,creative,recursive}/`
2. Inherit `BaseAgent`, define `agent_id`, `domain`, `supported_task_types`, `preferred_model`
3. Implement `async run(task, state, context) -> str`
4. Register in `swarm/registry.py` inside `build_default()`
5. Add tests in `tests/unit/`

## Writing Tests

- Use `pytest-asyncio` for async tests
- Mock asyncpg pools with `unittest.mock.AsyncMock`
- Maintain 90%+ coverage on all `core/`, `memory/`, `security/` modules
- Run: `pytest tests/ --cov=core --cov=memory --cov=security --cov=models`

## Code Style

- Python 3.11+ type hints on all functions
- Async-first — avoid blocking calls
- Pydantic v2 for all data models
- No references to other agent platforms in code or comments

## Security Rules (enforced)

- ChatJimmy outputs are **never** auto-certified — always route through Claude first
- VITALS and MEDICUS must always append medical disclaimer to output
- COUNSEL must always append legal disclaimer to output
- All new agents must work within HyperShield policies

## Submitting

1. Fork the repo
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Commit with a descriptive message
4. Open a PR — all tests must pass

## Questions?

Open a [GitHub Discussion](https://github.com/mentatalbans/hyperclaw/discussions) — that's the best place for ideas, questions, and showcasing what you've built with HyperClaw.
