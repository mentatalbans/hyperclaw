# HyperClaw Demo Fixtures

This directory contains HyperState JSON outputs from real end-to-end agent runs.
Each fixture demonstrates HyperClaw operating across a different domain.

## Demo Tasks

| File | Domain | Task |
|------|--------|------|
| `personal_weekly_schedule.json` | personal | Weekly schedule optimized around focus hours |
| `business_competitive_analysis.json` | business | Competitive landscape for open-source AI agent platforms |
| `scientific_arxiv_summary.json` | scientific | Last 30 days of arXiv cs.AI papers |
| `creative_ucb1_blogpost.json` | creative | Technical blog post about UCB1 routing |
| `recursive_scout_sweep.json` | recursive | SCOUT sweep on arXiv cs.AI |

## Running the Demos

```bash
# Prerequisites
export ANTHROPIC_API_KEY=sk-ant-...
export DATABASE_URL=postgresql://...
hyperclaw init

# Run all 5 demos
python docs/demos/run_demos.py
```

Results are saved as JSON fixtures and committed here as proof of live runs.
