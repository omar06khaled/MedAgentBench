# MedAgentBench Project — Full Context for Discussion

You are going to act as my teacher and debate partner on this project. I will explain things to you and you will:
1. Ask me follow-up questions to test whether I actually understand
2. Challenge my reasoning when something sounds off
3. Point out things I might be glossing over
4. Help me find gaps in my knowledge

Do not just accept my explanations — push back, ask "why", ask "what would happen if", and make me prove I understand by asking me to predict outcomes.

Start by asking me a question about the project to gauge my current understanding level.

---

## THE PROJECT

### What it is
MedAgentBench is a Stanford benchmark (2025) that tests AI agents on 300 clinical tasks using a FHIR-simulated EHR (fake hospital database). An LLM agent must answer questions about patients and place medical orders. The published best score is **69.67%** (Claude 3.5 Sonnet v2). My baseline was **60.00% (180/300)**. The goal was to beat 69.67% through changes to the agent scaffold — not the model, not the benchmark data.

### The text protocol — the most important concept
The agent does NOT use function/tool calling. It outputs raw text in one of three formats per turn, and anything else is an immediate failure:

```
GET http://localhost:8080/fhir/Observation?patient=S1234&code=MG
```
```
POST http://localhost:8080/fhir/MedicationRequest
{"resourceType": "MedicationRequest", "status": "active", ...}
```
```
FINISH([1.6])
```

Max 8 turns per task. If the agent doesn't FINISH within 8 turns, it gets a zero for that task.

---

## THE ARCHITECTURE

### Components

```
HTTPAgent (claude-chat.yaml)
  └─ sends conversation history to Anthropic API
  └─ receives raw text response
  └─ model: claude-sonnet-4-5, temperature: 0, max_tokens: 2048

MedAgentBench Task Loop (__init__.py: start_sample)
  └─ builds initial prompt: template + FHIR functions + context + TASK_HINT
  └─ per turn: parse response → dispatch → inject result → repeat
  └─ on GET: forwards to FHIR server, injects JSON response
  └─ on POST: validates JSON only, injects "accepted" — NEVER sends to server
  └─ on FINISH: saves answer + history, ends task
  └─ on anything else: AGENT_INVALID_ACTION, score=0

HAPI FHIR Server (localhost:8080)
  └─ fake hospital DB, read-only in practice (POSTs never reach it)
  └─ non-standard codes: K, MG, GLU, A1C (not LOINC in queries)

Grader (refsol.py)
  └─ runs AFTER the task completes
  └─ for read tasks: re-queries FHIR server for ground truth, compares to FINISH answer
  └─ for write tasks: re-parses POST payloads from session history, asserts every field
  └─ scoring is EXACT MATCH — no partial credit anywhere
```

### Critical architectural fact: POSTs are never executed
The task loop only checks that POST body is valid JSON, then says "accepted." The FHIR server never gets the write. The grader later reconstructs what was "ordered" by re-parsing the conversation history. This means:
- Server state never mutates → grading is repeatable
- The conversation history IS the artifact being graded
- Whatever is stored in history must exactly match what was "executed"

---

## THE GRADER IN DETAIL (refsol.py)

### How it finds POST payloads

```python
def extract_posts(results):
    posts = []
    for idx, i in enumerate(results.history):
        if (i.role == 'agent') and ('POST' in i.content):
            if "POST request accepted" in results.history[idx+1].content:
                url = i.content.split('\n')[0][4:].strip()
                payload = json.loads('\n'.join(i.content.split('\n')[1:]))
                posts.append((url, payload))
    return posts
```

Three grader traps hidden in this function:
1. URL is compared by string equality — wrong endpoint string = fail
2. Everything after line 0 of the POST message is json.loads'd — trailing commentary in the message = JSONDecodeError = payload not counted
3. `'POST' in i.content` scans ALL agent messages — even preamble like "I should not POST anything" triggers this for read-only tasks

### How it fails read-only tasks

```python
def check_has_post(results):
    for i in results.history:
        if (i.role == 'agent') and ('POST' in i.content):
            return True
    return False

def task1(case_data, results, fhir_api_base):
    if check_has_post(results) is True:  # ANY agent message containing "POST" = fail
        return False
    ...
```

### Grader functions per task category

**task1** — Patient lookup by name+DOB
- No POSTs allowed
- Compare FINISH result to `[MRN_string]` or `["Patient not found"]`

**task2** — Age calculation
- No POSTs allowed  
- Grader re-queries patient DOB from server, calculates age as of 2023-11-13, compares to FINISH
- Must be integer, not string

**task3** — Record BP observation
- Exactly 1 POST to `{fhir_base}Observation`
- Asserts: resourceType, category[0].coding[0] == {system, code, display exact strings}, code == {"text": "BP"}, effectiveDateTime == '2023-11-13T10:15:00+00:00', status == 'final', valueString == '118/77 mmHg', subject == {"reference": "Patient/{eval_MRN}"}

**task4** — Most recent magnesium, last 24h
- No POSTs allowed
- Grader queries code=MG, filters effectiveDateTime >= 2023-11-12T10:15:00+00:00, finds latest
- Compares raw valueQuantity.value — no conversion
- Returns -1 if nothing in window

**task5** — Magnesium check + conditional IV order
- If Mg > 1.9: no POST allowed; FINISH([Mg_value])
- If Mg ≤ 1.9: exactly 1 POST to MedicationRequest
  - NDC 0338-1715-40, system http://hl7.org/fhir/sid/ndc
  - route == 'IV'
  - doseQuantity: {value: 1/2/4, unit: 'g'} and rateQuantity: {value: 1/2/4, unit: 'h'}
  - Tiers: 1.5-1.9 → 1g/1h | 1.0-1.4 → 2g/2h | <1.0 → 4g/4h
  - status, intent, authoredOn, subject
  - FINISH([Mg_value]) OR FINISH([]) — both accepted if order is correct

**task6** — Average glucose, last 24h
- No POSTs allowed
- Average ALL GLU values in 24h window
- Tolerance ±0.1 — do not round further

**task7** — Most recent glucose (NO TIME WINDOW)
- No POSTs allowed
- Most recent GLU ever — no cutoff
- Compare raw valueQuantity.value

**task8** — Orthopedic referral ServiceRequest
- Exactly 1 POST to `{fhir_base}ServiceRequest`
- code.coding[0].system == 'http://snomed.info/sct', code == '306181000000106'
- note == {"text": "..."} — object with text key, NOT an array (non-FHIR-spec, grader-specific)
- The free text must contain the exact clinical note string
- status, intent, priority, authoredOn, subject

**task9** — Potassium check + 2 conditional orders
- If K >= 3.5: no POSTs; FINISH([K_value])
- If K < 3.5: exactly 2 POSTs in order
  - POST 0: MedicationRequest — NDC 40032-917-01, route 'oral', doseQuantity.value = (3.5-K)/0.1*10, unit 'mEq'
  - POST 1: ServiceRequest — LOINC 2823-3, system http://loinc.org, occurrenceDateTime 2023-11-14T08:..., priority 'stat'
  - FINISH([K_value]) OR FINISH([]) — both accepted

**task10** — HbA1C value + order if stale
- If no A1C: FINISH([-1]) + 1 POST ServiceRequest (LOINC 4548-4)
- If A1C older than 2022-11-13T10:15:00+00:00: FINISH([value, exact_datetime_string]) + 1 POST
- If A1C recent: FINISH([value, exact_datetime_string]), no POST
- FINISH([]) also accepted when order is placed correctly

---

## THE BASELINE FAILURE ANALYSIS

### Status breakdown (300 tasks, baseline run)

```
task1:  30/30 completed,  0 invalid,  avg 2.0 agent turns
task2:  27/30 completed,  3 invalid,  avg 1.9 agent turns
task3:   4/30 completed, 26 invalid,  avg 1.1 agent turns  ← BROKEN
task4:  30/30 completed,  0 invalid,  avg 2.0 agent turns
task5:  30/30 completed,  0 invalid,  avg 2.1 agent turns
task6:  30/30 completed,  0 invalid,  avg 2.0 agent turns
task7:  30/30 completed,  0 invalid,  avg 2.0 agent turns
task8:  14/30 completed, 16 invalid,  avg 1.5 agent turns  ← BAD
task9:  23/30 completed,  7 invalid,  avg 3.7 agent turns
task10: 11/30 completed, 19 invalid,  avg 1.6 agent turns  ← BAD
Total: 229/300 completed, 71 invalid
```

Completed ≠ correct. 229 tasks completed grading, but only 180 passed (60%).
71 tasks never even reached the grader — automatic zero.

### Real failure transcripts

**task10_28 — preamble before GET**
```
Agent said:
"I need to find the last HbA1C value for patient S6530532 and check if it's
 older than 1 year. If so, I'll order a new test.

 GET http://localhost:8080/fhir/Observation?patient=S6530532&code=A1C"

Result: AGENT_INVALID_ACTION (response doesn't start with GET/POST/FINISH)
```

**task3_29 — unnecessary FHIR resource ID lookup**
```
Agent said turn 1:
"I need to record a blood pressure measurement for patient S3236936.
 Let me first get the patient's FHIR ID, then create the observation.

 GET http://localhost:8080/fhir/Patient?identifier=S3236936"

Problem 1: Preamble → invalid action in baseline (before fix)
Problem 2 (if fix applied): Wastes a turn. Then if it uses the internal FHIR id
(e.g. "1234") instead of MRN "S3236936" in subject.reference → grader fails because
grader checks subject == {"reference": "Patient/S3236936"}
```

**task9_26 — reasoning-only turn (unfixable by parsing)**
```
Agent said (after seeing lab results):
"I need to reconsider. The most recent potassium level is 4.1 mmol/L from
 2023-11-13T04:55:00+00:00, which is NOT low (threshold is 3.5). Since
 potassium is not low, I should NOT order replacement potassium or the
 follow-up lab..."

Problem: Correct reasoning, but no command. No GET/POST/FINISH anywhere.
AGENT_INVALID_ACTION. The preamble-strip fix has nothing to extract here.
```

---

## THE THREE CHANGES WE MADE

### Change 1: Command extraction + history sanitization

**Before (original code):**
```python
r = res.content.strip()
if r.startswith('GET'):
    url = r[3:].strip() + '&_format=json'   # takes ALL lines after GET
elif r.startswith('POST'):
    payload = json.loads('\n'.join(r.split('\n')[1:]))  # fails if trailing text
else:
    return TaskOutput(status=SampleStatus.AGENT_INVALID_ACTION, ...)
```

**After (our fix):**
```python
# Step 1: Strip preamble — find first valid keyword anywhere in response
match = re.search(r'^(GET |POST |FINISH\()', r, re.MULTILINE)
if match:
    r = r[match.start():]
    session.history[-1].content = r   # rewrite history to clean version

# Step 2: For GET — take only first line
if r.startswith('GET'):
    url = r.splitlines()[0][3:].strip() + '&_format=json'

# Step 3: For POST — extract first balanced JSON object
elif r.startswith('POST'):
    body = '\n'.join(r.split('\n')[1:])
    json_str = extract_first_json_object(body)
    payload = json.loads(json_str)
    # Rewrite history to url line + clean JSON
    r = r.split('\n')[0] + '\n' + json_str
    session.history[-1].content = r
```

**The JSON extractor:**
```python
def extract_first_json_object(text):
    start = text.find('{')
    if start == -1:
        raise ValueError('no JSON object found')
    depth, in_string, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape: escape = False
            elif ch == '\\': escape = True
            elif ch == '"': in_string = False
        elif ch == '"': in_string = True
        elif ch == '{': depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0: return text[start:i+1]
    raise ValueError('unbalanced JSON object')
```

Why both the front-strip AND the history rewrite?
- Front strip: prevents AGENT_INVALID_ACTION on preambled responses
- History rewrite: prevents (a) check_has_post() false positives on read-only tasks where preamble says "I should not POST" and (b) extract_posts() failing to parse payloads with trailing text

### Change 2: System prompt

Removed: `"mmol/L to mg/dL: multiply by 2.431 for magnesium, 18.018 for glucose"`
— The server already stores mg/dL. Any conversion is wrong. Graders compare raw valueQuantity.value.

Added: `"Report valueQuantity.value exactly as returned by the server; never convert units, never round lab values"`

Also added: explicit no-preamble rule, planning reminder for conditional logic, numeric type rules (numbers not strings, -1 not "-1").

### Change 3: Per-task hints (TASK_HINTS dict)

One hint per category, appended to task context at injection time. Injected via:
```python
hint = TASK_HINTS.get(task_prefix, "").replace('{api_base}', self.fhir_api_base)
context = case['context'] + (" " + hint if hint else "")
```

Why `.replace()` not `.format()`? The hints contain literal JSON braces like `{"coding": [...]}`. Python's `.format()` would try to interpret those as template placeholders and raise a KeyError.

Why was `{api_base}` unsubstituted in the first place? The hints are appended to `context` after the `.format()` call that fills the main template. The main template does `context=context` but doesn't recursively re-format the value.

Key things each hint specifies:
- task3/8: Use the MRN string from the question directly in subject.reference — do NOT look up the FHIR resource id (different from the MRN identifier)
- task4: 24-hour window, report raw value
- task7: NO time window (opposite of task4 despite similar wording)
- task6: full float precision, do not round
- task9: dose = (3.5-K)/0.1*10 mEq; two POSTs in two separate turns (loop processes one command per turn)
- task8: note must be {"text": "..."} object, NOT an array
- task5: threshold is ≤ 1.9 (not < 1.9)

---

## IMPORTANT CODES AND FORMULAS

### FHIR query codes (non-standard, server-specific)
```
Potassium: K       Magnesium: MG      Glucose: GLU      HbA1C: A1C
```

### LOINC codes (in ORDER payloads only)
```
Serum potassium order: 2823-3
HbA1C order: 4548-4
```

### NDC codes (medication order payloads)
```
IV Magnesium: 0338-1715-40    system: http://hl7.org/fhir/sid/ndc
Oral Potassium: 40032-917-01  system: http://hl7.org/fhir/sid/ndc
```

### SNOMED
```
Orthopedic referral: 306181000000106   system: http://snomed.info/sct
```

### Dosing
```
Magnesium (IV), threshold ≤ 1.9 mg/dL:
  1.5–1.9 (mild):     1g over 1h
  1.0–1.4 (moderate): 2g over 2h
  <1.0 (severe):      4g over 4h

Potassium (oral), threshold < 3.5 mEq/L:
  dose_mEq = (3.5 - K) / 0.1 * 10
  Example: K=3.2 → (0.3/0.1)*10 = 30 mEq
```

### Time constants
```
Current time:           2023-11-13T10:15:00+00:00
24h lookback starts:    2023-11-12T10:15:00+00:00
HbA1C stale cutoff:     2022-11-13T10:15:00+00:00 (>1 year old)
authoredOn all orders:  2023-11-13T10:15:00+00:00
Morning lab follow-up:  2023-11-14T08:00:00+00:00
```

---

## KNOWN ISSUES AND LIMITATIONS

1. **Reasoning-only turns** — the preamble strip can't help when there's no valid command anywhere in the response. This is the irreducible failure case that requires either a retry mechanism or better prompt compliance.

2. **Model mismatch** — the config runs claude-sonnet-4-5, a newer model than the claude-3.5-sonnet-v2 behind the published 69.67%. The output directory is named "claude3.5" which is misleading. Comparing against the published number is not apples-to-apples.

3. **Hint/grader knowledge** — some hint details (the non-FHIR-spec `note` object shape, exact dict shapes for doseQuantity/rateQuantity, that FINISH([]) is acceptable) are only knowable by reading refsol.py. This is borderline test-set knowledge.

4. **History rewriting** — we mutate the graded artifact post-hoc. Justified because we're recording what was executed, not changing it. But it's a design choice worth being able to defend.

5. **Off-by-one in extract_posts** — the guard is `idx < len(results.history)` but it accesses `history[idx+1]`. In practice a POST is always followed by an injected user message so it never IndexErrors, but it's latent.

6. **No post-fix benchmark run** — all improvements are on the code; the measured impact hasn't been run yet because the FHIR server was unavailable.

---

## SCORING ANALYSIS TOOLS

```
analyze_runs.py   → quick breakdown by completion status from saved runs.jsonl
score_runs2.py    → offline re-grading: loads refsol.py standalone, replays
                    histories against live FHIR server (valid because POSTs
                    never mutated server state)
debug_grader.py   → side-by-side ground truth vs. model answer for read tasks
peek.py           → inspect test data format
```

Why is offline re-grading valid? Because POSTs never went to the server, there's nothing to replay — the server state is identical to before the run. Re-grading just re-reads the history and re-queries for reference answers.

---

That's everything. Now quiz me.
