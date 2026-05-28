import json

runs = []
with open('outputs/MedAgentBenchv1/claude3.5/medagentbench-std/runs.jsonl') as f:
    for line in f:
        runs.append(json.loads(line))

with open('data/medagentbench/test_data_v2.json') as f:
    raw = json.load(f)
cases_by_pos = {i: c for i, c in enumerate(raw)}

# Show 3 examples each from task2, task4, task6, task7
targets = {'2': 0, '4': 0, '6': 0, '7': 0}

for r in runs:
    idx = r['index']
    case = cases_by_pos.get(idx, {})
    task_id = case.get('id', '')
    cat = task_id.replace('task', '').split('_')[0]
    if cat not in targets or targets[cat] >= 3:
        continue
    if r['output']['status'] != 'completed':
        continue
    targets[cat] += 1
    print(f"=== {task_id} (task{cat}) ===")
    print(f"  sol:    {case.get('sol')}")
    print(f"  result: {r['output'].get('result')}")
    print()
