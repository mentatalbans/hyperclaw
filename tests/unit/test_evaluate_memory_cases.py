import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_alternate_cases_reports_all_categories_without_measuring_frozen_sixty(tmp_path):
    cases=[]
    for category in ('exact','paraphrase','revision','scope'):
        cases.append({'id':category,'category':category,'records':[{'key':'a','text':'Tiny signal is teal.','workspace':'tiny','session':'s'}], 'actions':[], 'query_scope':{'workspace':'tiny','session':'s'},'query':'signal', 'expected_keys':['a'],'forbidden_keys':[]})
    path=tmp_path/'cases.json'
    path.write_text(json.dumps({'observed_at':'2026-01-01T00:00:00Z','cases':cases}))
    result=subprocess.run([sys.executable,str(ROOT/'scripts/evaluate_memory.py'),'--cases',str(path),'--report',str(tmp_path/'report.json')],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    report=json.loads((tmp_path/'report.json').read_text())
    assert report['category_counts']=={'exact':1,'paraphrase':1,'revision':1,'scope':1}
    for category in ('exact','paraphrase','revision','scope'):
        assert report[category+'_recall_at_5']=={'hits':1,'total':1,'score':1.0}
