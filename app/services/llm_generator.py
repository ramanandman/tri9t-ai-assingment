"""
LLM-powered test case generation.

Design decisions (see APPROACH.md for the full write-up):

1. Prompt design: we reconstruct the selected sections' heading + body
   text (in numbering order) and ask for STRICT JSON matching our
   schema. We explicitly tell the model the exact JSON shape and to
   return NOTHING else (no markdown fences, no commentary) - this is
   the single biggest lever for reducing malformed output.

2. Structured-output validation: the LLM's raw text response is parsed
   as JSON and validated against a Pydantic model (GeneratedTestCases).
   We NEVER trust the raw string - if it isn't valid JSON, or doesn't
   match the schema (wrong types, missing fields), that's treated as a
   failure, not "close enough."

3. Retry policy: if validation fails, we retry EXACTLY ONCE with a
   corrective follow-up message that includes the actual error and
   asks the model to fix it. If the second attempt also fails, we
   return a controlled error (a clear Python exception with a message
   describing what went wrong) - we do NOT save partial/malformed
   output, and we do NOT silently retry forever ("it usually works" is
   explicitly called out in the assignment as not a real design).

4. Duplicate-selection policy: generating test cases for the same
   selection twice creates a SEPARATE new generation record each time
   (consistent with the Selection API's own duplicate policy) - we
   don't try to detect "you already generated this." A user re-running
   generation might want fresh/different test case ideas, or the LLM
   provider may have changed; silently blocking a second generation
   would be a surprising, undocumented restriction.
"""

import os
import json
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ValidationError
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

_client = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Create a .env file with GROQ_API_KEY=your_key."
            )
        _client = Groq(api_key=api_key)
    return _client


class TestCase(BaseModel):
    title: str
    preconditions: list[str]
    steps: list[str]
    expected_result: str


class GeneratedTestCases(BaseModel):
    test_cases: list[TestCase]


class LLMGenerationError(Exception):
    """Raised when the LLM's output could not be validated, even after one retry."""
    pass


SYSTEM_PROMPT = """You are a QA engineer writing test cases for a medical device \
(a home blood pressure monitor). You will be given one or more sections of the \
device's technical manual. Generate 3 to 5 concrete, executable QA test case ideas \
based ONLY on the text provided - do not invent behavior that isn't stated or \
reasonably implied by the text.

You MUST respond with ONLY valid JSON matching exactly this shape, and nothing else \
(no markdown code fences, no explanation before or after):

{
  "test_cases": [
    {
      "title": "short descriptive title",
      "preconditions": ["precondition 1", "precondition 2"],
      "steps": ["step 1", "step 2", "step 3"],
      "expected_result": "concrete, checkable expected outcome"
    }
  ]
}
"""


def _build_user_prompt(sections: list[dict]) -> str:
    """
    sections: list of {"numbering": ..., "heading": ..., "body_text": ...}
    """
    parts = ["Generate QA test cases based on the following manual section(s):\n"]
    for s in sections:
        parts.append(f"### Section {s['numbering']} - {s['heading']}\n{s['body_text']}\n")
    return "\n".join(parts)


def _call_llm(messages: list[dict]) -> str:
    client = _get_client()
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        temperature=0.3,
    )
    return response.choices[0].message.content


def _try_parse(raw_text: str) -> GeneratedTestCases:
    """
    Strip common LLM formatting mistakes (markdown fences) before
    parsing, then validate against the strict schema. Raises on
    failure - caller decides whether to retry.
    """
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        # strip a leading ```json / ``` and trailing ```
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

    data = json.loads(cleaned)  # raises json.JSONDecodeError on invalid JSON
    return GeneratedTestCases(**data)  # raises pydantic.ValidationError on schema mismatch


def generate_test_cases(sections: list[dict]) -> GeneratedTestCases:
    """
    Main entry point. sections is the reconstructed text of a
    selection's pinned nodes. Returns validated GeneratedTestCases.
    Raises LLMGenerationError if the LLM's output is unusable after
    one retry.
    """
    user_prompt = _build_user_prompt(sections)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    raw = _call_llm(messages)

    try:
        return _try_parse(raw)
    except (json.JSONDecodeError, ValidationError) as first_error:
        # Retry exactly once, with a corrective message showing the model
        # what went wrong.
        messages.append({"role": "assistant", "content": raw})
        messages.append({
            "role": "user",
            "content": (
                "Your previous response was not valid JSON matching the required "
                f"schema. Error: {first_error}. Respond again with ONLY the corrected "
                "JSON object, matching the exact schema described earlier, and nothing else."
            ),
        })
        raw_retry = _call_llm(messages)
        try:
            return _try_parse(raw_retry)
        except (json.JSONDecodeError, ValidationError) as second_error:
            raise LLMGenerationError(
                f"LLM output could not be validated after 1 retry. "
                f"First error: {first_error}. Second error: {second_error}. "
                f"Last raw response: {raw_retry[:500]}"
            )


# --- Simple JSON-file store for generated output ---
#
# We use a single JSON file as a lightweight, well-justified NoSQL-style
# store (per the assignment's "or a well-justified JSON store" option),
# rather than standing up a real MongoDB instance. Justification: the
# generated data has no need for relational querying or joins - it's
# fetched by selection_id or node_id, which a flat JSON file indexed by
# ID handles perfectly well at this scale, and it avoids adding an
# external database dependency for a take-home assignment.

STORE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "generated_test_cases.json")
STORE_PATH = os.path.abspath(STORE_PATH)


def _load_store() -> dict:
    if not os.path.exists(STORE_PATH):
        return {}
    with open(STORE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_store(store: dict):
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)


def save_generation(selection_id: str, source_nodes: list[dict], test_cases: GeneratedTestCases) -> dict:
    """
    Persist a generation record, linked to the selection AND to the
    exact node IDs + content hashes it was generated from (this is
    what staleness detection later checks against).

    source_nodes: list of {"node_id", "version_id", "numbering", "content_hash"}
    """
    store = _load_store()

    generation_id = str(uuid.uuid4())
    record = {
        "id": generation_id,
        "selection_id": selection_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_nodes": source_nodes,
        "test_cases": [tc.model_dump() for tc in test_cases.test_cases],
    }
    store[generation_id] = record
    _save_store(store)
    return record


def get_generation(generation_id: str) -> dict | None:
    store = _load_store()
    return store.get(generation_id)


def get_generations_for_selection(selection_id: str) -> list[dict]:
    store = _load_store()
    return [rec for rec in store.values() if rec["selection_id"] == selection_id]


def get_generations_for_node(node_id: str) -> list[dict]:
    store = _load_store()
    result = []
    for rec in store.values():
        if any(n["node_id"] == node_id for n in rec["source_nodes"]):
            result.append(rec)
    return result
