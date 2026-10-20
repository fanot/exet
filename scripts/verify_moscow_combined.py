"""Rebuild the combined 24-parameter case using saved optimizer candidates.

The complete normal/bayes search was repeated before the final dispatch fix.
This replay isolates the changed trajectory/schedule stage and preserves every
mission parameter. It does not benchmark or repeat the 60 TPE trials.
"""
import copy
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scripts.qa_moscow_params as qa
from planner.optimizer import Planner


class ReplayPlanner(Planner):
    def search(self):
        baseline = ROOT / 'out/qa_moscow_params/48_combined/plan.json.gz'
        saved = json.loads(gzip.decompress(baseline.read_bytes()))
        self.candidates = copy.deepcopy(saved['final_candidates'])
        self.progress(0.72, 'Повторное построение сохраненных кандидатов: все 24 параметра')


if __name__ == '__main__':
    qa.OUT = ROOT / 'out/qa_moscow_fixes_final'
    qa.Planner = ReplayPlanner
    case = next(case for case in qa.cases() if case['id'] == '48_combined')
    record = qa.worker(case, force=True)
    record['verification_mode'] = 'Saved candidate replay; all 24 parameters unchanged; TPE trials not repeated'
    qa.write_json(qa.OUT / case['id'] / 'result.json', record)
    print(json.dumps({key: record.get(key) for key in ('id', 'status', 'elapsed_s', 'error', 'suspicions')}, ensure_ascii=False))
