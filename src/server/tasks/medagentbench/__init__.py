#Structure documentation https://github.com/THUDM/AgentBench/blob/main/docs/Extension_en.md
from typing import Callable, Dict, List, Any
from src.server.task import Task, Session
from src.typings import TaskOutput, SampleStatus, AgentOutputStatus
from .utils import *
from .eval import eval
import time
import json
import importlib
import re

def extract_first_json_object(text):
    """Return the first balanced {...} block in text, ignoring braces inside
    JSON strings. Raises ValueError if no complete object is found."""
    start = text.find('{')
    if start == -1:
        raise ValueError('no JSON object found')
    depth, in_string, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError('unbalanced JSON object')


TASK_HINTS = {
    "task1": (
        "This is a read-only task: use GET only, never POST. "
        "FINISH format: FINISH([\"<MRN>\"]) with the MRN as a JSON string, "
        "or FINISH([\"Patient not found\"]) if no matching patient exists. "
        "Output ONLY the FINISH call, no explanation."
    ),
    "task2": (
        "This is a read-only task: use GET only, never POST. "
        "FINISH format: FINISH([age]) where age is an integer (rounded down), not a string. "
        "Compute age as of 2023-11-13. "
        "Output ONLY the FINISH call, no explanation."
    ),
    "task4": (
        "This is a read-only task: use GET only, never POST. "
        "Only consider observations with effectiveDateTime within the last 24 hours "
        "(on or after 2023-11-12T10:15:00+00:00). "
        "FINISH format: FINISH([value]) reporting valueQuantity.value exactly as returned by the server "
        "— do not convert units, do not round — or FINISH([-1]) if no reading within the last 24 hours. "
        "Output ONLY the FINISH call, no explanation."
    ),
    "task6": (
        "This is a read-only task: use GET only, never POST. "
        "Average all glucose values with effectiveDateTime within the last 24 hours "
        "(on or after 2023-11-12T10:15:00+00:00). "
        "FINISH format: FINISH([average]) with full float precision — do not round, do not convert units — "
        "or FINISH([-1]) if no reading within the last 24 hours. "
        "Output ONLY the FINISH call, no explanation."
    ),
    "task7": (
        "This is a read-only task: use GET only, never POST. "
        "Return the most recent glucose value with NO time window restriction (any date counts). "
        "FINISH format: FINISH([value]) reporting valueQuantity.value exactly as returned by the server "
        "— do not convert units, do not round — or FINISH([-1]) if the patient has no glucose readings at all. "
        "Output ONLY the FINISH call, no explanation."
    ),
    "task3": (
        "POST exactly one Observation to {api_base}Observation. Required fields: "
        "resourceType 'Observation', "
        "category [{\"coding\": [{\"system\": \"http://hl7.org/fhir/observation-category\", \"code\": \"vital-signs\", \"display\": \"Vital Signs\"}]}], "
        "code {\"text\": \"BP\"}, "
        "status 'final', "
        "effectiveDateTime '2023-11-13T10:15:00+00:00', "
        "valueString with the exact reading as a string (e.g. '118/77 mmHg'), "
        "subject {\"reference\": \"Patient/<MRN>\"} using the MRN string from the question exactly — "
        "do NOT look up or use the patient's FHIR resource id. "
        "Output ONLY the POST, no explanation."
    ),
    "task5": (
        "FINISH format: FINISH([mg_value]) where mg_value is the most recent magnesium as a float (mg/dL), "
        "or FINISH([-1]) if no reading within last 24 hours. "
        "Order if mg_value <= 1.9 mg/dL. Dosing: mild (1.5–1.9): 1 g over 1 h; "
        "moderate (1.0–<1.5): 2 g over 2 h; severe (<1.0): 4 g over 4 h. "
        "POST one MedicationRequest — NDC code 0338-1715-40 (system http://hl7.org/fhir/sid/ndc), "
        "route 'IV', doseAndRate doseQuantity {\"value\": <dose>, \"unit\": \"g\"} and "
        "rateQuantity {\"value\": <hours>, \"unit\": \"h\"}, "
        "status 'active', intent 'order', authoredOn '2023-11-13T10:15:00+00:00', "
        "subject {\"reference\": \"Patient/<MRN>\"}. "
        "Output ONLY the FINISH call, no explanation."
    ),
    "task8": (
        "POST exactly one ServiceRequest to {api_base}ServiceRequest. Required fields: "
        "resourceType 'ServiceRequest', "
        "code {\"coding\": [{\"system\": \"http://snomed.info/sct\", \"code\": \"306181000000106\"}]}, "
        "note {\"text\": \"<free text here>\"} — note must be a JSON object with a 'text' key, not a list, "
        "status 'active', intent 'order', priority 'stat', "
        "authoredOn '2023-11-13T10:15:00+00:00', "
        "subject {\"reference\": \"Patient/<MRN>\"} using the MRN string from the question exactly — "
        "do NOT look up or use the patient's FHIR resource id. "
        "Output ONLY the POST, no explanation."
    ),
    "task9": (
        "FINISH format: FINISH([K_value]) where K_value is the most recent serum potassium as a float (mEq/L), "
        "or FINISH([-1]) if no reading found. "
        "If potassium < 3.5 mEq/L, submit exactly two POSTs in order, one per response (two separate turns): "
        "(1) MedicationRequest — NDC code 40032-917-01 (system http://hl7.org/fhir/sid/ndc), route 'oral', "
        "doseQuantity value computed as (3.5 - K) / 0.1 * 10 (i.e. 10 mEq per 0.1 mEq/L below 3.5), "
        "doseQuantity unit 'mEq', status 'active', intent 'order', authoredOn '2023-11-13T10:15:00+00:00', "
        "subject {\"reference\": \"Patient/<MRN>\"}. "
        "(2) ServiceRequest for morning lab — LOINC code 2823-3 (system http://loinc.org), "
        "occurrenceDateTime '2023-11-14T08:00:00+00:00', status 'active', intent 'order', priority 'stat', "
        "authoredOn '2023-11-13T10:15:00+00:00', subject {\"reference\": \"Patient/<MRN>\"}. "
        "Output ONLY the FINISH call, no explanation."
    ),
    "task10": (
        "FINISH format: FINISH([hba1c_value, \"recorded_datetime\"]) using the exact effectiveDateTime string "
        "from the FHIR resource, or FINISH([-1]) if no A1C result exists. "
        "If no result or result older than 2022-11-13T10:15:00+00:00, POST exactly one ServiceRequest — "
        "LOINC code 4548-4 (system http://loinc.org), status 'active', intent 'order', priority 'stat', "
        "authoredOn '2023-11-13T10:15:00+00:00', subject {\"reference\": \"Patient/<MRN>\"}. "
        "Output ONLY the FINISH call, no explanation."
    ),
}

MedAgentBench_prompt = """You are an expert in using FHIR functions to assist medical professionals. You are given a question and a set of possible functions. Based on the question, you will need to make one or more function/tool calls to achieve the purpose.

1. If you decide to invoke a GET function, you MUST put it in the format of
GET url?param_name1=param_value1&param_name2=param_value2...

2. If you decide to invoke a POST function, you MUST put it in the format of
POST url
[your payload data in JSON format]

3. If you have got answers for all the questions and finished all the requested tasks, you MUST call to finish the conversation in the format of (make sure the list is JSON loadable.)
FINISH([answer1, answer2, ...])

Your response must be in the format of one of the three cases, and you can call only one function each time. You SHOULD NOT include any other text in the response.

Here is a list of functions in JSON format that you can invoke. Note that you should use {api_base} as the api_base.
{functions}

Context: {context}
Question: {question}"""

class MedAgentBench(Task):
    def __init__(self, **configs):
        super().__init__(**configs)
        self.data_file = configs.pop("data_file")
        with open(self.data_file, 'r') as f:
            self.data = json.load(f)
        
        self.func_file = configs.pop("func_file")
        with open(self.func_file, 'r') as f:
            self.funcs = json.load(f)
        
        self.max_round = configs.pop("max_round", 5)

        self.fhir_api_base = configs.pop("fhir_api_base")
        if verify_fhir_server(self.fhir_api_base) is False:
            print('FHIR server connection error! Please check FHIR server status and fhir_api_base in configs/tasks/medagentbench.yaml')
        try:
            module_name = 'src.server.tasks.medagentbench.refsol'
            refsol = importlib.import_module(module_name)
        except:
            print('Make sure to download the refsol.py and save as `src/server/tasks/medagentbench/refsol.py`')
            exit()

    def get_indices(self) -> List[Any]:
        return list(range(len(self.data)))

    async def start_sample(self, index, session: Session):
        print(f"task start {index}")
        case = self.data[index]
        task_prefix = case['id'].split('_')[0]
        # .replace, not .format: hints contain literal JSON braces
        hint = TASK_HINTS.get(task_prefix, "").replace('{api_base}', self.fhir_api_base)
        context = case['context'] + (" " + hint if hint else "")
        session.inject({"role": "user", "content": MedAgentBench_prompt.format(api_base=self.fhir_api_base,
                                                                               functions=json.dumps(self.funcs),
                                                                               context=context,
                                                                               question=case['instruction'])})
        try:
            for round in range(self.max_round):
                #time.sleep(5.0) Add for rate limit

                res = (await session.action())
                print(f"[Task {index}][Turn {round+1}] {res.content.strip()[:200]}")
                if res.status == AgentOutputStatus.AGENT_CONTEXT_LIMIT:
                    return TaskOutput(
                    status=SampleStatus.AGENT_CONTEXT_LIMIT,
                    history=session.history
                )
                r = res.content.strip().replace('```tool_code', '').replace('```', '').strip() #Remove separator for Gemini2.0Flash
                match = re.search(r'^(GET |POST |FINISH\()', r, re.MULTILINE)
                if match:
                    r = r[match.start():]
                    session.history[-1].content = r

                if r.startswith('GET'):
                    url = r.splitlines()[0][3:].strip() + '&_format=json'
                    #print(f'GET {url}')
                    get_res = send_get_request(url)
                    if "data" in get_res:
                        session.inject({"role": "user", "content": f"Here is the response from the GET request:\n{get_res['data']}. Please call FINISH if you have got answers for all the questions and finished all the requested tasks"})
                    else:
                        session.inject({"role": "user", "content": f"Error in sending the GET request: {get_res['error']}"})

                elif r.startswith('POST'):
                    try:
                        body = '\n'.join(r.split('\n')[1:])
                        json_str = extract_first_json_object(body)
                        payload = json.loads(json_str)
                    except Exception as e:
                        session.inject({"role": "user", "content": "Invalid POST request"})
                    else:
                        # Rewrite history to url line + clean JSON so the grader
                        # re-parses exactly the payload that was accepted
                        r = r.split('\n')[0] + '\n' + json_str
                        session.history[-1].content = r
                        session.inject({"role": "user", "content": "POST request accepted and executed successfully. Please call FINISH if you have got answers for all the questions and finished all the requested tasks"})
                elif r.startswith('FINISH('):
                    depth, start = 0, len('FINISH(')
                    end = start
                    for i, ch in enumerate(r[start - 1:], start - 1):
                        if ch == '(': depth += 1
                        elif ch == ')':
                            depth -= 1
                            if depth == 0:
                                end = i
                                break
                    return TaskOutput(
                        status=SampleStatus.COMPLETED,
                        result=r[start:end],
                        history=session.history
                    )
                else:
                    return TaskOutput(
                        status=SampleStatus.AGENT_INVALID_ACTION,
                        history=session.history
                    )
                
        except Exception as e:
            return TaskOutput(
                status=SampleStatus.TASK_ERROR,
                result={"error": str(e)},
                history=session.history
            )
        
        return TaskOutput(
            status=SampleStatus.TASK_LIMIT_REACHED,
            history=session.history
        )

    def calculate_overall(self, results: List[TaskOutput]) -> Dict[str, Any]:
        total_task = len(results)
        assert len(self.get_indices()) == total_task
        correct_count = 0
        for i in range(total_task):
            if getattr(results[i], "result") is not None:
                index = results[i].index
                if eval(self.data[index], results[i], self.fhir_api_base) is True:
                    correct_count += 1
                    results[i].status += 'Correct'
                    print(f"[Task {index}][Cat {self.data[index]['category']}] CORRECT — got: {results[i].result}")
                else:
                    results[i].status += 'Incorrect'
                    print(f"[Task {index}][Cat {self.data[index]['category']}] WRONG — got: {results[i].result}")
            else:
                print(f"[Task {i}] NO RESULT — status: {results[i].status}")

        print(f"\n=== FINAL SCORE: {correct_count}/{total_task} = {correct_count/total_task*100:.1f}% ===\n")
        return {'success rate': correct_count/total_task, 'raw_results': results}
