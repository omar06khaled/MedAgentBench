import json
from collections import defaultdict

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
    task_id = case.get('id', '')
    return task_id.replace('task', '').split('_')[0]

def normalize(val):
    """Normalize a value for comparison."""
    if val is None:
        return None
    s = str(val).strip().lower()
    # Remove surrounding quotes
    s = s.strip('"\'')
    return s

def check_correct(case, result_str):
    """Simple grader: parse result JSON and compare to sol field."""
    sol = case.get('sol', [])
    if result_str is None:
        return False
    try:
        result = json.loads(result_str)
    except Exception:
        result = result_str

    # result should be a list matching sol
    if not isinstance(result, list):
        result = [result]
    if not isinstance(sol, list):
        sol = [sol]

    if len(result) != len(sol):
        return False

    for r, s in zip(result, sol):
        # Numeric comparison with tolerance
        try:
            if abs(float(r) - float(s)) < 0.01:
                continue
            else:
                return False
        except (TypeError, ValueError):
            pass
        # String comparison
        if normalize(r) != normalize(s):
            return False
    return True

by_category = defaultdict(lambda: {'total': 0, 'correct': 0, 'invalid': 0, 'incorrect': 0})

for r in runs:
    idx = r['index']
    case = cases_by_pos.get(idx, {})
    cat = get_category(case)
    status = r['output']['status']
    result = r['output'].get('result')

    by_category[cat]['total'] += 1

    if status != 'completed' or result is None:
        by_category[cat]['invalid'] += 1
        continue

    if check_correct(case, result):
        by_category[cat]['correct'] += 1
    else:
        by_category[cat]['incorrect'] += 1

print(f'{"Cat":<6} | {"Total":<5} | {"Correct":<7} | {"Wrong":<7} | {"Invalid":<7} | Accuracy')
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
