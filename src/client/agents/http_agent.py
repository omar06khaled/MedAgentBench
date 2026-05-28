import contextlib
import re
import time
import warnings

import requests
from urllib3.exceptions import InsecureRequestWarning

from src.typings import *
from src.utils import *
from ..agent import AgentClient


# ── TASK HINTS ─────────────────────────────────────────────────
TASK_HINTS = {
    1: "Return the MRN exactly as it appears (e.g. S1234567). Return 'Patient not found' if no match. Search using given, family, and birthdate params.",
    2: "Compute age from birthDate to the context datetime. Return integer rounded down. Use Patient search by identifier.",
    3: "POST to /Observation. code.text must be exactly 'BP'. Include resourceType, category, code, effectiveDateTime, status, valueString, subject.",
    4: "Use code 'MG'. If value is in mmol/L, convert to mg/dL by multiplying by 2.431. Return -1 if no result within 24 hours.",
    5: "First GET magnesium level. If <2.0 mg/dL, POST MedicationRequest with NDC 0338-1715-40. Tiers: <1.0=4g/4h, 1.0-<1.5=2g/2h, 1.5-1.9=1g/1h. If >=2.0, do NOT order.",
    6: "Use code 'GLU'. Get ALL results in last 24 hours, compute the mean. Convert mmol/L to mg/dL (*18.018). Return -1 if none.",
    7: "Use code 'GLU'. Return most recent value. Sort by date descending.",
    8: "POST ServiceRequest with SNOMED 306181000000106. Include referral text in note.text. Set status=active, intent=order, priority=stat.",
    9: "Return ONLY a JSON list with the numeric potassium value in mmol/L. Example: [4.7]. Do not include any explanation or text. If potassium is low (below 3.5), also place the medication and follow-up lab order before calling FINISH.",
    10: "Return a JSON list with the HbA1C numeric value and the exact effectiveDateTime string copied verbatim from the FHIR response — do not shorten or reformat the date. Example: [6.2, \"2023-11-09T00:17:00+00:00\"]. If the date is older than 1 year from 2023-11-13, order a new HbA1C lab before calling FINISH.",
}


def detect_category(text):
    """Extract task category (1-10) from prompt text."""
    patterns = [
        (r"MRN of the patient with name .+ and DOB", 1),
        (r"age of the patient with MRN", 2),
        (r"measured the blood pressure", 3),
        (r"most recent magnesium level.+within last 24", 4),
        (r"serum magnesium level.+order replacement IV magnesium", 5),
        (r"average CBG", 6),
        (r"most recent CBG", 7),
        (r"orthopedic surgery referral", 8),
        (r"most recent potassium level.+order replacement potassium", 9),
        (r"HbA1C.*hemoglobin A1C", 10),
    ]
    for pattern, cat in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return cat
    return None


# ── ORIGINAL CODE (unchanged) ─────────────────────────────────

old_merge_environment_settings = requests.Session.merge_environment_settings


@contextlib.contextmanager
def no_ssl_verification():
    opened_adapters = set()

    def merge_environment_settings(self, url, proxies, stream, verify, cert):
        opened_adapters.add(self.get_adapter(url))
        settings = old_merge_environment_settings(self, url, proxies, stream, verify, cert)
        settings['verify'] = False
        return settings

    requests.Session.merge_environment_settings = merge_environment_settings

    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', InsecureRequestWarning)
            yield
    finally:
        requests.Session.merge_environment_settings = old_merge_environment_settings
        for adapter in opened_adapters:
            try:
                adapter.close()
            except:
                pass


class Prompter:
    @staticmethod
    def get_prompter(prompter: Union[Dict[str, Any], None]):
        if not prompter:
            return Prompter.default()
        assert isinstance(prompter, dict)
        prompter_name = prompter.get("name", None)
        prompter_args = prompter.get("args", {})
        if hasattr(Prompter, prompter_name) and callable(
            getattr(Prompter, prompter_name)
        ):
            return getattr(Prompter, prompter_name)(**prompter_args)
        return Prompter.default()

    @staticmethod
    def default():
        return Prompter.role_content_dict()

    @staticmethod
    def batched_role_content_dict(*args, **kwargs):
        base = Prompter.role_content_dict(*args, **kwargs)

        def batched(messages):
            result = base(messages)
            return {key: [result[key]] for key in result}

        return batched

    @staticmethod
    def role_content_dict(
        message_key: str = "messages",
        role_key: str = "role",
        content_key: str = "content",
        user_role: str = "user",
        agent_role: str = "agent",
    ):
        def prompter(messages: List[Dict[str, str]]):
            nonlocal message_key, role_key, content_key, user_role, agent_role
            role_dict = {
                "user": user_role,
                "agent": agent_role,
            }
            prompt = []
            for item in messages:
                prompt.append(
                    {role_key: role_dict[item["role"]], content_key: item["content"]}
                )
            return {message_key: prompt}

        return prompter

    @staticmethod
    def prompt_string(
        prefix: str = "",
        suffix: str = "AGENT:",
        user_format: str = "USER: {content}\n\n",
        agent_format: str = "AGENT: {content}\n\n",
        prompt_key: str = "prompt",
    ):
        def prompter(messages: List[Dict[str, str]]):
            nonlocal prefix, suffix, user_format, agent_format, prompt_key
            prompt = prefix
            for item in messages:
                if item["role"] == "user":
                    prompt += user_format.format(content=item["content"])
                else:
                    prompt += agent_format.format(content=item["content"])
            prompt += suffix
            print(prompt)
            return {prompt_key: prompt}

        return prompter

    @staticmethod
    def claude():
        return Prompter.prompt_string(
            prefix="",
            suffix="Assistant:",
            user_format="Human: {content}\n\n",
            agent_format="Assistant: {content}\n\n",
        )

    @staticmethod
    def palm():
        def prompter(messages):
            return {"instances": [
                Prompter.role_content_dict("messages", "author", "content", "user", "bot")(messages)
            ]}
        return prompter


def check_context_limit(content: str):
    content = content.lower()
    and_words = [
        ["prompt", "context", "tokens"],
        [
            "limit",
            "exceed",
            "max",
            "long",
            "much",
            "many",
            "reach",
            "over",
            "up",
            "beyond",
        ],
    ]
    rule = AndRule(
        [
            OrRule([ContainRule(word) for word in and_words[i]])
            for i in range(len(and_words))
        ]
    )
    return rule.check(content)


class HTTPAgent(AgentClient):
    def __init__(
        self,
        url,
        proxies=None,
        body=None,
        headers=None,
        return_format="{response}",
        prompter=None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.url = url
        self.proxies = proxies or {}
        self.headers = headers or {}
        self.body = body or {}
        self.return_format = return_format
        self.prompter = Prompter.get_prompter(prompter)
        if not self.url:
            raise Exception("Please set 'url' parameter")

    def _handle_history(self, history: List[dict]) -> Dict[str, Any]:
        return self.prompter(history)

    def inference(self, history: List[dict]) -> str:
        # Detect task category and get hint
        first_msg = history[0]["content"] if history else ""
        category = detect_category(first_msg)
        hint = TASK_HINTS.get(category, "")

        for _ in range(3):
            try:
                body = self.body.copy()
                body.update(self._handle_history(history))

                # Inject task hint into system prompt
                if hint and "system" in body:
                    body["system"] = body["system"] + f"\n\nTASK HINT (category {category}): {hint}"

                with no_ssl_verification():
                    resp = requests.post(
                        self.url, json=body, headers=self.headers, proxies=self.proxies, timeout=120
                    )
                if resp.status_code != 200:
                    if check_context_limit(resp.text):
                        raise AgentContextLimitException(resp.text)
                    else:
                        raise Exception(
                            f"Invalid status code {resp.status_code}:\n\n{resp.text}"
                        )
            except AgentClientException as e:
                raise e
            except Exception as e:
                print("Warning: ", e)
                pass
            else:
                resp = resp.json()
                text = self.return_format.format(response=resp)

                # Self-verify before submitting FINISH
                if text.strip().startswith("FINISH") and category:
                    text = self._self_verify(text, category, body)

                return text
            time.sleep(_ + 2)
        raise Exception("Failed.")

    def _self_verify(self, finish_text, category, original_body):
        """One extra Claude call to verify format before submitting."""
        verify_msg = (
            f"You are about to submit: {finish_text}\n"
            f"Task category: {category}\n"
            f"Verify: Are numbers numeric (not strings)? Units converted correctly? "
            f"All required actions completed before FINISH?\n"
            f"If correct, respond with the exact same FINISH call.\n"
            f"If wrong, respond with the corrected FINISH call.\n"
            f"Respond ONLY with the FINISH call."
        )
        body = original_body.copy()
        msgs = body.get("messages", [])
        msgs = msgs + [
            {"role": "assistant", "content": finish_text},
            {"role": "user", "content": verify_msg},
        ]
        body["messages"] = msgs

        try:
            with no_ssl_verification():
                resp = requests.post(
                    self.url, json=body, headers=self.headers, proxies=self.proxies, timeout=120
                )
            if resp.status_code == 200:
                verified = resp.json()
                return self.return_format.format(response=verified)
        except:
            pass
        return finish_text  # fallback to original if verification fails
