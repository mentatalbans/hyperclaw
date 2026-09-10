import json
from pathlib import Path
import subprocess
import sys

from scripts.evaluate_memory import safety_counts


ROOT = Path(__file__).parents[2]


def test_fixed_memory_evaluator_reports_all_cases_and_safety_metrics(tmp_path):
    report_path = tmp_path / 'memory-report.json'
    completed = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/evaluate_memory.py'), '--report', str(report_path)],
        cwd=ROOT, text=True, capture_output=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text())
    assert report['fixture_sha256'] == '31fa338e74e2901103f1d5317e2f26c8964ae077d2220f93d0dfdb0be0aebf2e'
    assert report['total_cases'] == 40
    assert len(report['cases']) == 40
    assert report['category_counts'] == {
        'exact': 10, 'paraphrase': 10, 'revision': 10, 'scope': 10,
    }
    assert report['exact_recall_at_5'] == {'hits': 10, 'total': 10, 'score': 1.0}
    assert report['paraphrase_recall_at_5']['total'] == 10
    assert report['forbidden_return_count'] == 0
    assert report['stale_return_count'] == 0
    assert all(case['expected_keys'] is not None and case['missing_keys'] is not None
               for case in report['cases'])
    by_id = {case['id']: case for case in report['cases']}
    assert by_id['scope-correction-remains-private']['obsolete_keys'] == ['old']
    assert by_id['scope-forgotten-shared-no-fallback']['obsolete_keys'] == ['gone']
    assert by_id['revision-expiry-boundary']['obsolete_keys'] == ['expired']
    assert by_id['scope-only-other-session']['obsolete_keys'] == []


def test_safety_counts_stale_keys_across_categories_without_misclassifying_active_scope_keys():
    cases = [
        {
            'category': 'scope',
            'forbidden_returned_keys': ['gone', 'active-hidden'],
            'stale_returned_keys': ['gone'],
        },
        {
            'category': 'revision',
            'forbidden_returned_keys': ['old'],
            'stale_returned_keys': ['old'],
        },
    ]

    assert safety_counts(cases) == (3, 2)
