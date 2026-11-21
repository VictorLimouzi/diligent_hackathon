"""Output shapes, a small JSON Schema check, and record-ID helpers.

Every model output is checked against one of these schemas before it is used.
The schemas stay inside what structured outputs accept: objects closed with
additionalProperties false, every property required, no numeric or length
constraints.
"""
import json
import re

DECISIONS = ("DIFFERENT_ENTITY", "SAME_ENTITY", "REVIEW")
REASON_CODES = {
    "DIFFERENT_ENTITY": ("TEMPORAL_IMPOSSIBILITY", "DOB_MISMATCH", "LOCATION_MISMATCH",
                         "NAME_MISMATCH_VERIFIED", "ENTITY_TYPE_MISMATCH", "DECEASED"),
    "SAME_ENTITY": ("NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH", "NAME_LOCATION_MATCH"),
    "REVIEW": ("INSUFFICIENT_DATA", "AMBIGUOUS_CUSTOMER_RECORD"),
}
ALL_REASON_CODES = tuple(c for codes in REASON_CODES.values() for c in codes)

# The same pattern the server uses to find record IDs in text.
ID_RE = re.compile(r"\b(?:CUS|KYC|NOTE|SR|H)-[A-Z0-9]+\b")


def _obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


_STR = {"type": "string"}
_STR_OR_NULL = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_STR_LIST = {"type": "array", "items": {"type": "string"}}

INVESTIGATION = _obj({
    "blocker": _STR,
    "findings": {"type": "array", "items": _obj({"text": _STR, "cited_ids": _STR_LIST})},
    "next_action": _STR,
    "rfi_draft": _STR_OR_NULL,
})

NOTE = _obj({"body": _STR})

QUEUE = _obj({"text": _STR})

SECOND_OPINION = _obj({
    "decision": {"type": "string", "enum": list(DECISIONS)},
    "reason_code": {"type": "string", "enum": list(ALL_REASON_CODES)},
    "rationale": _STR,
})

EXTRACTION = _obj({
    "fact": _obj({
        "kind": {"type": "string", "enum": ["identity_check", "residence", "listing", "none"]},
        "name": _STR_OR_NULL,
        "date_of_birth": _STR_OR_NULL,
        "countries": _STR_LIST,
        "approved": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
        "listing_phrase": _STR_OR_NULL,
        "age": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "age_at_date": _STR_OR_NULL,
    }),
    "quote": _STR,
})


_TYPES = {
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "null": lambda v: v is None,
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


def validate(value, schema, path="$"):
    """Problems found when checking a value against a schema; an empty list means it fits.

    Covers the subset used here: type, enum, anyOf, properties, required,
    additionalProperties false, and array items.
    """
    if "anyOf" in schema:
        if any(not validate(value, s, path) for s in schema["anyOf"]):
            return []
        return [f"{path}: matches none of the allowed shapes"]
    errors = []
    kind = schema.get("type")
    if kind and not _TYPES[kind](value):
        return [f"{path}: expected {kind}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if kind == "object":
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: missing")
        for key, item in value.items():
            if key in props:
                errors.extend(validate(item, props[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key}: not allowed")
    if kind == "array" and "items" in schema:
        for i, item in enumerate(value):
            errors.extend(validate(item, schema["items"], f"{path}[{i}]"))
    return errors


def parse(text, schema):
    """(value, errors) for a model reply that should be one JSON document fitting the schema."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        return None, [f"not JSON: {exc}"]
    errors = validate(value, schema)
    return (None, errors) if errors else (value, [])


def find_ids(text):
    """Record IDs written in a text, in order, each once."""
    return list(dict.fromkeys(ID_RE.findall(text or "")))


def hit_ids(hit):
    """Every record ID the engine's result for a hit refers to.

    Read from the structured fields and the engine's own path strings only,
    never from profile notes, quotes or snippets: third-party text must not be
    able to add an ID to the set a note or finding may cite.
    """
    ids = [hit.get("hit_id")]
    ids += list(hit.get("evidence") or [])
    if hit.get("customer_record_id") not in (None, "AMBIGUOUS"):
        ids.append(hit["customer_record_id"])
    trace = hit.get("trace") or {}
    for line in trace.get("resolution") or []:
        ids += ID_RE.findall(str(line))
    facts = trace.get("hit") or {}
    ids += list(facts.get("dob_sources") or [])
    ids += [s.get("result_id") for s in facts.get("search_considered") or []]
    for cand in trace.get("candidates") or []:
        cust = cand.get("customer") or {}
        ids += list(cust.get("records") or [])
        ids += [cust.get("primary"), cust.get("verified_check"), cust.get("dob_source")]
        ids += [f.get("source") for f in (cust.get("declared_names") or []) + (cust.get("countries") or [])]
        for line in cust.get("path") or []:
            ids += ID_RE.findall(str(line))
        ids += list(cand.get("evidence") or [])
        for check in cand.get("checks") or []:
            ids += list(check.get("sources") or [])
    for cf in hit.get("counterfactuals") or []:
        for outcome in cf.get("outcomes") or []:
            ids += ID_RE.findall(str(outcome.get("if") or ""))
    return {i for i in ids if isinstance(i, str) and ID_RE.fullmatch(i)}
