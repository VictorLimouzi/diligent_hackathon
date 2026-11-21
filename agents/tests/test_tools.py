"""The tools: scoped to the alert's own customer, read-only, small, and explicit about untrusted text."""
import json

import pytest

from agents.tools import TOOL_DEFS, ToolBox


def box(world, hit_id):
    alert, hit = world.case(hit_id)
    return ToolBox(world.data, alert, hit)


def test_definitions_are_tight_and_stable():
    names = [t["name"] for t in TOOL_DEFS]
    assert names == sorted(names) == ["compare_names", "get_customer", "get_engine_trace", "get_hit_profile",
                                      "get_identity_checks", "get_notes", "get_search_results"]
    for tool in TOOL_DEFS:
        schema = tool["input_schema"]
        assert tool["strict"] is True and schema["additionalProperties"] is False
        assert schema["required"] == list(schema["properties"]) and tool["description"]
        assert all(p["type"] == "string" for p in schema["properties"].values())


def test_every_tool_answers_for_the_alerts_own_records(world):
    b = box(world, "H-ABROAD")
    calls = [("get_hit_profile", {"hit_id": "H-ABROAD"}), ("get_customer", {"record_id": "CUS-1"}),
             ("get_customer", {"record_id": "CUS-2"}),            # another record of the same person
             ("get_identity_checks", {"record_id": "CUS-1"}), ("get_notes", {"record_id": "CUS-1"}),
             ("get_search_results", {"hit_id": "H-ABROAD"}), ("get_engine_trace", {"hit_id": "H-ABROAD"}),
             ("compare_names", {"name_a": "Amina Bello", "name_b": "Bello, Amina"})]
    for name, args in calls:
        result = b.call(name, args)
        assert result.ok, (name, result.content)
        assert result.summary and len(b.as_text(result)) < 6000          # small outputs
        json.loads(b.as_text(result))


@pytest.mark.parametrize("name,args", [
    ("get_customer", {"record_id": "CUS-9"}),
    ("get_identity_checks", {"record_id": "CUS-9"}),
    ("get_notes", {"record_id": "CUS-9"}),
    ("get_customer", {"record_id": "CUS-3"}),       # same name, but not the person this alert resolved to
    ("get_hit_profile", {"hit_id": "H-NAME"}),
    ("get_search_results", {"hit_id": "H-SEARCH"}),
    ("get_engine_trace", {"hit_id": "H-NAME"}),
])
def test_records_outside_the_alert_are_refused(world, name, args):
    b = box(world, "H-ABROAD")
    result = b.call(name, args)
    assert result.ok is False and result.content["error"] == "out_of_scope"
    assert result.summary.startswith("Refused")
    blob = b.as_text(result)
    assert "Zed Other" not in blob and "Kenya" not in blob and "NOTE-9" not in blob
    assert not {"CUS-9", "NOTE-9", "CUS-3"} & b.seen_ids


def test_scope_is_per_alert(world):
    assert box(world, "H-SEPT").call("get_customer", {"record_id": "CUS-3"}).ok       # both people fit alert A-2
    assert box(world, "H-NOBODY").customers == []                                     # no record was found
    assert box(world, "H-NOBODY").call("get_customer", {"record_id": "CUS-6"}).ok is False


@pytest.mark.parametrize("name,args,error", [
    ("set_decision", {"decision": "DIFFERENT_ENTITY"}, "unknown_tool"),
    ("_person", {"record_id": "CUS-1"}, "unknown_tool"),
    ("call", {"name": "x", "args": {}}, "unknown_tool"),
    ("get_customer", {"record_id": "CUS-1", "extra": 1}, "invalid_input"),
    ("get_customer", {}, "invalid_input"),
    ("get_customer", {"record_id": 5}, "invalid_input"),
    ("get_customer", "CUS-1", "invalid_input"),
    ("compare_names", {"name_a": "x"}, "invalid_input"),
])
def test_bad_calls_are_error_results_not_crashes(world, name, args, error):
    result = box(world, "H-ABROAD").call(name, args)
    assert result.ok is False and result.content["error"] == error


def test_tool_names_are_matched_without_regard_to_case(world):
    assert box(world, "H-ABROAD").call("Get_Customer", {"record_id": "CUS-1"}).ok


def test_identity_checks_are_labelled_and_only_approved_is_evidence(world):
    content = box(world, "H-ABROAD").call("get_identity_checks", {"record_id": "CUS-1"}).content
    by_id = {c["check_id"]: c for c in content["checks"]}
    assert {k: v["status"] for k, v in by_id.items()} == {"KYC-1": "approved", "KYC-1R": "rejected", "KYC-1E": "expired"}
    assert [k for k, v in by_id.items() if v["is_evidence"]] == ["KYC-1"]
    assert by_id["KYC-1"]["used_by_engine"] and not by_id["KYC-1R"]["used_by_engine"]
    assert "document_number" not in json.dumps(content)


def test_third_party_text_is_wrapped_as_untrusted(world):
    b = box(world, "H-ABROAD")
    profile = b.call("get_hit_profile", {"hit_id": "H-ABROAD"}).content
    assert profile["profile_notes"]["untrusted"] is True and "appointed" in profile["profile_notes"]["text"]
    assert profile["profile"]["position_or_allegation"] == {"untrusted": True, "text": "Former minister"}
    notes = b.call("get_notes", {"record_id": "CUS-1"}).content["notes"]
    assert notes[0]["text"]["untrusted"] is True and notes[0]["countries_mentioned"] == ["Canada"]
    assert notes[0]["countries_lived_in"] == []
    results = box(world, "H-NODOB").call("get_search_results", {"hit_id": "H-NODOB"}).content["results"]
    assert results and all(r["snippet"]["untrusted"] is True for r in results)


def test_search_results_carry_the_engines_verdict(world):
    content = box(world, "H-SEARCH").call("get_search_results", {"hit_id": "H-SEARCH"}).content
    by_id = {r["result_id"]: r for r in content["results"]}
    assert by_id["SR-HIT"]["about_hit"] is True and by_id["SR-HIT"]["used_for_birth_date"] is True
    assert by_id["SR-NAMESAKE"]["about_hit"] is False and "Norway" in by_id["SR-NAMESAKE"]["why_not"]
    assert content["results"][0]["result_id"] == "SR-HIT" and content["about_hit"] == 1


def test_compare_names_uses_the_policy_rule(world):
    b = box(world, "H-ABROAD")
    assert b.call("compare_names", {"name_a": "Amína BELLO", "name_b": "Bello, Amina"}).content["agree"] is True
    assert b.call("compare_names", {"name_a": "Amina Bello", "name_b": "Tunde Musa"}).content["agree"] is False
    assert "caveat" in b.call("compare_names", {"name_a": "N/A Bello", "name_b": "Amina Bello"}).content


def test_tools_do_not_change_the_data_or_the_hit(world):
    alert, hit = world.case("H-ABROAD")
    before_hit = json.dumps(hit, sort_keys=True)
    before_data = json.dumps([world.data.customers, world.data.hits, dict(world.data.by_id)], sort_keys=True, default=str)
    b = ToolBox(world.data, alert, hit)
    for tool in TOOL_DEFS:
        args = {k: {"hit_id": "H-ABROAD", "record_id": "CUS-1"}.get(k, "Amina Bello") for k in tool["input_schema"]["properties"]}
        assert b.call(tool["name"], args).ok
    assert json.dumps(hit, sort_keys=True) == before_hit
    assert json.dumps([world.data.customers, world.data.hits, dict(world.data.by_id)], sort_keys=True, default=str) == before_data
    assert not [name for name in dir(ToolBox) if name.startswith(("_set", "_write", "_review", "_decide", "_update"))]
