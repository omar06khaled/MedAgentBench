import json
from collections import defaultdict

runs = []
with open('outputs/MedAgentBenchv1/claude3.5/medagentbench-std/runs.jsonl') as f:
    for line in f:
        runs.append(json.loads(line))

with open('data/medagentbench/test_data_v2.json') as f:
    raw = json.load(f)

# Build lookup by position (index 0..299) and by task id
cases_by_pos = {i: c for i, c in enumerate(raw)}

# Extract category from id like "task1_1" -> "1"
def get_category(case):
    task_id = case.get('id', '')
    # task1_1 -> task number is 1
    part = task_id.replace('task', '').split('_')[0]
    return part

total = len(runs)
completed = sum(1 for r in runs if r['output']['status'] == 'completed')
print(f'Total: {total}')
print(f'Completed: {completed}')
print(f'Failed/Invalid: {total - completed}')
print(f'Completion rate: {completed/total*100:.1f}%')
print()

by_category = defaultdict(lambda: {'total': 0, 'completed': 0})
for r in runs:
    idx = r['index']
    case = cases_by_pos.get(idx, {})
    cat = get_category(case)
    by_category[cat]['total'] += 1
    if r['output']['status'] == 'completed':
        by_category[cat]['completed'] += 1

print(f'{"Category":<10} | {"Total":<5} | {"Done":<5} | {"Invalid":<7} | Rate')
print('-' * 50)
for cat in sorted(by_category, key=lambda x: int(x) if x.isdigit() else 99):
    d = by_category[cat]
    rate = d['completed'] / d['total'] * 100
    inv = d['total'] - d['completed']
    print(f'task{cat:<6} | {d["total"]:<5} | {d["completed"]:<5} | {inv:<7} | {rate:.1f}%')
