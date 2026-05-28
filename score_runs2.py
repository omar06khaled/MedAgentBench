import json
import sys
import importlib
import importlib.util
import types
from collections import defaultdict

# Patch relative imports for refsol
spec = importlib.util.spec_from_file_location(
    "utils", "src/server/tasks/medagentbench/utils.py"
)
utils_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utils_mod)

pkg = types.ModuleType("medagentbench")
pkg.utils = utils_mod
sys.modules["medagentbench"] = pkg
sys.modules["medagentbench.utils"] = utils_mod

spec2 = importlib.util.spec_from_file_location(
    "medagentbench.refsol",
    "src/server/tasks/medagentbench/refsol.py",
    submodule_search_locations=[]
)
refsol = importlib.util.module_from_spec(spec2)
refsol.__package__ = "medagentbench"
sys.modules["medagentbench.refsol"] = refsol
spec2.loader.exec_module(refsol)
print("refsol loaded OK")

# Wrapper classes to match what refsol expects
class Message:
    def __init__(self, role, content):
        self.role = role
        self.content = content

class Results:
    def __init__(self, history, result):
        self.history = [Message(m['role'], m['content']) for m in history]
        self.result = result

# Load runs
runs = []
with open('outputs/MedAgentBenchv1/claude3.5/medagentbench-std/runs.jsonl') as f:
    for line in f:
        runs.append(json.loads(line))

# Load test cases
with open('data/medagentbench/test_data_v2.json') as f:
    raw = json.load(f)
cases_by_pos = {i: c for i, c in enumerate(raw)}

def get_category(case):
    return case.get('id', '').replace('task', '').split('_')[0]

FHIR_BASE = "http://localhost:8080/fhir/"

by_category = defaultdict(lambda: {'total': 0, 'correct': 0, 'incorrect': 0, 'invalid': 0})

for r in runs:
    idx = r['index']
    case = cases_by_pos.get(idx, {})
    cat = get_category(case)
    status = r['output']['status']
    result = r['output'].get('result')
    history = r['output'].get('history', [])

    by_category[cat]['total'] += 1

    if status != 'completed' or result is None:
        by_category[cat]['invalid'] += 1
        continue

    task_id = case.get('id', '').split('_')[0]
    grader = getattr(refsol, task_id, None)
    if grader is None:
        print(f"No grader for {task_id}")
        by_category[cat]['incorrect'] += 1
        continue

    try:
        results_obj = Results(history, result)
        correct = grader(case, results_obj, FHIR_BASE)
        if correct:
            by_category[cat]['correct'] += 1
        else:
            by_category[cat]['incorrect'] += 1
    except Exception as e:
        print(f"Error grading {task_id} idx {idx}: {e}")
        by_category[cat]['incorrect'] += 1

print(f'\n{"Cat":<6} | {"Total":<5} | {"Correct":<7} | {"Wrong":<7} | {"Invalid":<7} | Accuracy')
print('-' * 65)
total_correct = 0
total_tasks = 0
for cat in sorted(by_category, key=lambda x: int(x) if x.isdigit() else 99):
    d = by_category[cat]
    acc = d['correct'] / d['total'] * 100
    total_correct += d['correct']
    total_tasks += d['total']
    print(f'task{cat:<2} | {d["total"]:<5} | {d["correct"]:<7} | {d["incorrect"]:<7} | {d["invalid"]:<7} | {acc:.1f}%')

print('-' * 65)
overall = total_correct / total_tasks * 100
print(f'OVERALL: {total_correct}/{total_tasks} = {overall:.2f}%')
print(f'Paper best: 69.67%  |  Gap: {overall - 69.67:+.2f}%')
