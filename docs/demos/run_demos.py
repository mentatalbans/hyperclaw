"""
HyperClaw Demo Runner — runs 5 real end-to-end tasks and saves HyperState fixtures.

Usage:
    python docs/demos/run_demos.py

Prerequisites:
    export ANTHROPIC_API_KEY=sk-ant-...
    export DATABASE_URL=postgresql://...
    hyperclaw init
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

DEMO_TASKS = [
    {
        "name": "personal_weekly_schedule",
        "domain": "personal",
        "task": "Build me a weekly schedule optimized around 3 deep-focus hours in the morning, "
                "regular exercise, and blocking time for strategic thinking. I work in tech.",
        "agent": "ATLAS",
    },
    {
        "name": "business_competitive_analysis",
        "domain": "business",
        "task": "Analyze the competitive landscape for open-source AI agent platforms. "
                "Who are the top 5 competitors to HyperClaw? What are their strengths and gaps?",
        "agent": "STRATEGOS",
    },
    {
        "name": "scientific_arxiv_summary",
        "domain": "scientific",
        "task": "Summarize the most significant trends in AI research from the last 30 days. "
                "Focus on multi-agent systems, reasoning improvements, and practical applications. "
                "Give me 5 bullet points with the most actionable insights.",
        "agent": "SCRIBE",
    },
    {
        "name": "creative_ucb1_blogpost",
        "domain": "creative",
        "task": "Write a technical blog post (800-1000 words) explaining UCB1 multi-armed bandit routing "
                "for AI agent systems. Target audience: ML engineers. Include a Python code example. "
                "Make it genuinely useful, not just theoretical.",
        "agent": "AUTHOR",
    },
    {
        "name": "recursive_scout_sweep",
        "domain": "recursive",
        "task": "Run a research sweep and report the 3 most actionable AI capability discoveries "
                "from the last week. For each: what it is, why it matters, and how HyperClaw could integrate it.",
        "agent": "SCOUT",
    },
]

FIXTURES_DIR = Path(__file__).parent


async def run_demo(task_config: dict) -> dict:
    """Run a single demo task and return the result + HyperState snapshot."""
    from models.router import ModelRouter
    from models.claude_client import ClaudeClient
    from models.chatjimmy_client import ChatJimmyClient
    from core.hyperstate.state_manager import StateManager
    from core.hyperstate.store import HyperStateStore
    from memory.causal_graph import CausalGraph
    from swarm.registry import AgentRegistry
    from security.policy_engine import PolicyEngine

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    db_url = os.environ.get("DATABASE_URL", "")

    # Build lightweight deps
    claude = ClaudeClient(api_key=api_key)
    cj_key = os.environ.get("CHATJIMMY_API_KEY")
    cj = ChatJimmyClient() if cj_key else None
    model_router = ModelRouter(claude_client=claude, chatjimmy_client=cj)

    store = HyperStateStore(db_url) if db_url else HyperStateStore()
    state_manager = StateManager(store)

    causal_graph = None
    if db_url:
        import asyncpg
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)
        causal_graph = CausalGraph(pool)

    registry = AgentRegistry.build_default(
        model_router=model_router,
        state_manager=state_manager,
        causal_graph=causal_graph,
        hyper_shield=None,
    )

    # Create state
    from core.hyperstate.schema import HyperState, Task
    state = HyperState(
        domain=task_config["domain"],
        task=Task(goal=task_config["task"], task_type="research"),
    )

    # Run the designated agent
    agent = registry.get(task_config["agent"])
    print(f"\n🔄 Running {task_config['agent']} — {task_config['name']}...")
    t0 = __import__("time").time()

    result = await agent.run(task_config["task"], state, {"domain": task_config["domain"]})

    elapsed = __import__("time").time() - t0
    print(f"✅ Completed in {elapsed:.1f}s")
    print(f"   Output preview: {result[:200]}...")

    # Save fixture
    fixture = {
        "demo_name": task_config["name"],
        "domain": task_config["domain"],
        "agent": task_config["agent"],
        "task": task_config["task"],
        "result_preview": result[:1000],
        "elapsed_seconds": round(elapsed, 2),
        "state": json.loads(state.model_dump_json()),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    fixture_path = FIXTURES_DIR / f"{task_config['name']}.json"
    fixture_path.write_text(json.dumps(fixture, indent=2, default=str))
    print(f"   Saved: {fixture_path}")

    return fixture


async def main():
    print("⚡ HyperClaw Demo Runner")
    print("=" * 50)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY not set. Export it and retry.")
        sys.exit(1)

    results = []
    for task in DEMO_TASKS:
        try:
            result = await run_demo(task)
            results.append({"name": task["name"], "status": "success", "elapsed": result["elapsed_seconds"]})
        except Exception as e:
            print(f"❌ {task['name']} failed: {e}")
            results.append({"name": task["name"], "status": "failed", "error": str(e)})

    print("\n" + "=" * 50)
    print("Demo Run Summary:")
    for r in results:
        icon = "✅" if r["status"] == "success" else "❌"
        detail = f"{r.get('elapsed', '?')}s" if r["status"] == "success" else r.get("error", "")
        print(f"  {icon} {r['name']}: {detail}")

    # Save index
    index = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "runs": results,
    }
    (FIXTURES_DIR / "index.json").write_text(json.dumps(index, indent=2))
    print(f"\nFixtures saved to: {FIXTURES_DIR}/")


if __name__ == "__main__":
    asyncio.run(main())
