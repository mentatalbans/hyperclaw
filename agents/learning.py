#!/usr/bin/env python3
"""
Assistant Self-Improvement System — Learn from successes and failures.
Tracks patterns, updates instincts, reflects on performance.
"""

import os
import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict

# Use user's ~/.hyperclaw directory, not package location
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
LEARNING_DB = HYPERCLAW_ROOT / "memory" / "learning.db"
INSTINCTS_FILE = HYPERCLAW_ROOT / "memory" / "instincts.md"


def init_db():
    """Initialize the learning database."""
    conn = sqlite3.connect(str(LEARNING_DB))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action_type TEXT NOT NULL,
            action_detail TEXT,
            outcome TEXT NOT NULL,
            success INTEGER NOT NULL,
            feedback TEXT,
            context TEXT,
            learned TEXT
        );

        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_type TEXT NOT NULL,
            description TEXT NOT NULL,
            frequency INTEGER DEFAULT 1,
            success_rate REAL DEFAULT 0.0,
            last_seen TEXT,
            action TEXT
        );

        CREATE TABLE IF NOT EXISTS reflections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            period TEXT NOT NULL,
            summary TEXT NOT NULL,
            insights TEXT,
            improvements TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_interactions_type ON interactions(action_type);
        CREATE INDEX IF NOT EXISTS idx_interactions_outcome ON interactions(outcome);
        CREATE INDEX IF NOT EXISTS idx_patterns_type ON patterns(pattern_type);
    """)
    conn.commit()
    conn.close()


init_db()


def log_interaction(
    action_type: str,
    action_detail: str,
    outcome: str,
    success: bool,
    feedback: str = None,
    context: Dict = None
) -> str:
    """Log an interaction for learning."""
    conn = sqlite3.connect(str(LEARNING_DB))

    conn.execute("""
        INSERT INTO interactions (timestamp, action_type, action_detail, outcome, success, feedback, context)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        action_type,
        action_detail,
        outcome,
        1 if success else 0,
        feedback,
        json.dumps(context) if context else None
    ))

    conn.commit()
    conn.close()

    # Update patterns
    _update_patterns(action_type, success)

    # If failure, try to learn from it
    if not success and feedback:
        return _learn_from_failure(action_type, action_detail, outcome, feedback)

    return f"Logged: {action_type} - {'success' if success else 'failure'}"


def _update_patterns(action_type: str, success: bool):
    """Update pattern statistics."""
    conn = sqlite3.connect(str(LEARNING_DB))

    # Check if pattern exists
    existing = conn.execute(
        "SELECT id, frequency, success_rate FROM patterns WHERE pattern_type = ?",
        (action_type,)
    ).fetchone()

    if existing:
        pattern_id, freq, rate = existing
        new_freq = freq + 1
        # Running average
        new_rate = ((rate * freq) + (1 if success else 0)) / new_freq
        conn.execute("""
            UPDATE patterns SET frequency = ?, success_rate = ?, last_seen = ?
            WHERE id = ?
        """, (new_freq, new_rate, datetime.now().isoformat(), pattern_id))
    else:
        conn.execute("""
            INSERT INTO patterns (pattern_type, description, frequency, success_rate, last_seen)
            VALUES (?, ?, 1, ?, ?)
        """, (action_type, f"Pattern for {action_type}", 1.0 if success else 0.0, datetime.now().isoformat()))

    conn.commit()
    conn.close()


def _learn_from_failure(action_type: str, detail: str, outcome: str, feedback: str) -> str:
    """Extract learning from a failure."""
    # Simple learning extraction
    learning = f"When {action_type}: {detail[:50]}... resulted in {outcome}. User feedback: {feedback}"

    # Update the interaction with the learning
    conn = sqlite3.connect(str(LEARNING_DB))
    conn.execute("""
        UPDATE interactions SET learned = ?
        WHERE action_type = ? AND action_detail = ?
        ORDER BY timestamp DESC LIMIT 1
    """, (learning, action_type, detail))
    conn.commit()
    conn.close()

    # Check if this should become an instinct
    _maybe_create_instinct(action_type, feedback)

    return f"Learned: {learning[:100]}..."


def _maybe_create_instinct(action_type: str, feedback: str):
    """Check if a pattern should become an instinct."""
    conn = sqlite3.connect(str(LEARNING_DB))

    # Count recent failures of this type
    recent_failures = conn.execute("""
        SELECT COUNT(*) FROM interactions
        WHERE action_type = ? AND success = 0
        AND timestamp > datetime('now', '-7 days')
    """, (action_type,)).fetchone()[0]

    conn.close()

    # If 3+ failures in a week, create an instinct
    if recent_failures >= 3:
        _add_instinct(f"CAUTION: {action_type}", feedback)


def _add_instinct(title: str, description: str):
    """Add a new instinct to instincts.md."""
    if not INSTINCTS_FILE.exists():
        INSTINCTS_FILE.write_text("# Assistant Instincts\n\nLearned behaviors and reflexes.\n\n")

    content = INSTINCTS_FILE.read_text()

    # Check if already exists
    if title.lower() in content.lower():
        return

    # Add new instinct
    new_instinct = f"\n## {title}\n\n{description}\n\n*Learned: {datetime.now().strftime('%Y-%m-%d')}*\n"
    INSTINCTS_FILE.write_text(content + new_instinct)


def get_stats(days: int = 7) -> Dict:
    """Get learning statistics."""
    conn = sqlite3.connect(str(LEARNING_DB))

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    total = conn.execute(
        "SELECT COUNT(*) FROM interactions WHERE timestamp > ?", (cutoff,)
    ).fetchone()[0]

    successes = conn.execute(
        "SELECT COUNT(*) FROM interactions WHERE timestamp > ? AND success = 1", (cutoff,)
    ).fetchone()[0]

    by_type = conn.execute("""
        SELECT action_type, COUNT(*), SUM(success)
        FROM interactions WHERE timestamp > ?
        GROUP BY action_type
        ORDER BY COUNT(*) DESC LIMIT 10
    """, (cutoff,)).fetchall()

    recent_failures = conn.execute("""
        SELECT action_type, action_detail, feedback
        FROM interactions WHERE timestamp > ? AND success = 0
        ORDER BY timestamp DESC LIMIT 5
    """, (cutoff,)).fetchall()

    conn.close()

    return {
        "period_days": days,
        "total_interactions": total,
        "successes": successes,
        "success_rate": round(successes / total * 100, 1) if total > 0 else 0,
        "by_type": [
            {"type": t, "count": c, "successes": s, "rate": round(s/c*100, 1) if c > 0 else 0}
            for t, c, s in by_type
        ],
        "recent_failures": [
            {"type": t, "detail": d[:50], "feedback": f}
            for t, d, f in recent_failures
        ]
    }


def get_patterns() -> List[Dict]:
    """Get learned patterns."""
    conn = sqlite3.connect(str(LEARNING_DB))

    patterns = conn.execute("""
        SELECT pattern_type, description, frequency, success_rate, last_seen, action
        FROM patterns ORDER BY frequency DESC LIMIT 20
    """).fetchall()

    conn.close()

    return [
        {
            "type": p[0],
            "description": p[1],
            "frequency": p[2],
            "success_rate": round(p[3] * 100, 1),
            "last_seen": p[4],
            "action": p[5]
        }
        for p in patterns
    ]


def reflect(period: str = "day") -> Dict:
    """Generate a reflection on recent performance."""
    if period == "day":
        days = 1
    elif period == "week":
        days = 7
    elif period == "month":
        days = 30
    else:
        days = 7

    stats = get_stats(days)

    # Generate insights
    insights = []

    if stats["success_rate"] < 80:
        insights.append(f"Success rate ({stats['success_rate']}%) is below target (80%)")

    # Find problematic areas
    for item in stats["by_type"]:
        if item["rate"] < 70 and item["count"] >= 3:
            insights.append(f"{item['type']}: Only {item['rate']}% success rate over {item['count']} attempts")

    # Suggest improvements
    improvements = []
    for failure in stats["recent_failures"]:
        if failure["feedback"]:
            improvements.append(f"For {failure['type']}: {failure['feedback']}")

    reflection = {
        "period": period,
        "stats": stats,
        "insights": insights,
        "improvements": improvements,
        "generated_at": datetime.now().isoformat()
    }

    # Save reflection
    conn = sqlite3.connect(str(LEARNING_DB))
    conn.execute("""
        INSERT INTO reflections (timestamp, period, summary, insights, improvements)
        VALUES (?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        period,
        json.dumps(stats),
        json.dumps(insights),
        json.dumps(improvements)
    ))
    conn.commit()
    conn.close()

    return reflection


def get_advice(action_type: str) -> str:
    """Get advice based on past learnings for an action type."""
    conn = sqlite3.connect(str(LEARNING_DB))

    # Get pattern stats
    pattern = conn.execute(
        "SELECT success_rate, frequency FROM patterns WHERE pattern_type = ?",
        (action_type,)
    ).fetchone()

    # Get recent learnings
    learnings = conn.execute("""
        SELECT learned FROM interactions
        WHERE action_type = ? AND learned IS NOT NULL
        ORDER BY timestamp DESC LIMIT 3
    """, (action_type,)).fetchall()

    conn.close()

    advice_parts = []

    if pattern:
        rate, freq = pattern
        if rate < 0.7:
            advice_parts.append(f"Caution: {action_type} has {rate*100:.0f}% success rate over {freq} attempts.")
        elif rate > 0.9:
            advice_parts.append(f"Good track record: {action_type} has {rate*100:.0f}% success rate.")

    if learnings:
        advice_parts.append("Past learnings:")
        for (learning,) in learnings:
            advice_parts.append(f"  - {learning}")

    return "\n".join(advice_parts) if advice_parts else f"No prior data for {action_type}"


def log_success(action_type: str, detail: str, context: Dict = None) -> str:
    """Shorthand for logging a success."""
    return log_interaction(action_type, detail, "completed successfully", True, context=context)


def log_failure(action_type: str, detail: str, error: str, feedback: str = None) -> str:
    """Shorthand for logging a failure."""
    return log_interaction(action_type, detail, error, False, feedback=feedback)


def log_feedback(feedback: str, action_type: str = None) -> str:
    """Log user feedback on recent actions."""
    conn = sqlite3.connect(str(LEARNING_DB))

    # Update most recent interaction
    if action_type:
        conn.execute("""
            UPDATE interactions SET feedback = ?
            WHERE action_type = ?
            ORDER BY timestamp DESC LIMIT 1
        """, (feedback, action_type))
    else:
        conn.execute("""
            UPDATE interactions SET feedback = ?
            ORDER BY timestamp DESC LIMIT 1
        """, (feedback,))

    conn.commit()
    conn.close()

    return f"Feedback recorded: {feedback[:50]}..."


# CLI functions for TUI integration
def learning_log(action: str, detail: str, success: bool, feedback: str = None) -> str:
    """Log an action for learning."""
    outcome = "success" if success else "failure"
    return log_interaction(action, detail, outcome, success, feedback=feedback)


def learning_stats(days: int = 7) -> Dict:
    """Get learning statistics."""
    return get_stats(days)


def learning_patterns() -> List[Dict]:
    """Get learned patterns."""
    return get_patterns()


def learning_reflect(period: str = "week") -> Dict:
    """Generate a reflection."""
    return reflect(period)


def learning_advice(action_type: str) -> str:
    """Get advice for an action type."""
    return get_advice(action_type)


def learning_feedback(feedback: str) -> str:
    """Record feedback on recent action."""
    return log_feedback(feedback)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: learning.py <log|stats|patterns|reflect|advice> [args]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "log":
        action = sys.argv[2] if len(sys.argv) > 2 else "test"
        detail = sys.argv[3] if len(sys.argv) > 3 else "test action"
        success = sys.argv[4].lower() == "true" if len(sys.argv) > 4 else True
        print(learning_log(action, detail, success))

    elif cmd == "stats":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        print(json.dumps(learning_stats(days), indent=2))

    elif cmd == "patterns":
        print(json.dumps(learning_patterns(), indent=2))

    elif cmd == "reflect":
        period = sys.argv[2] if len(sys.argv) > 2 else "week"
        print(json.dumps(learning_reflect(period), indent=2))

    elif cmd == "advice":
        action = sys.argv[2] if len(sys.argv) > 2 else ""
        print(learning_advice(action))

    else:
        print(f"Unknown command: {cmd}")
