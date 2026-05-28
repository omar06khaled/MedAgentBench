import json

with open('data/medagentbench/test_data_v2.json') as f:
    data = json.load(f)

print(type(data))
if isinstance(data, list):
    print(f'Length: {len(data)}')
    print('First item keys:', list(data[0].keys()))
    print('First item:', json.dumps(data[0], indent=2)[:500])
elif isinstance(data, dict):
    print('Top-level keys:', list(data.keys()))
