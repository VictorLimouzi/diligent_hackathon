"""The two batch commands: second opinion and extraction fallback."""
import json

import pytest

from agents import client, extract_fallback, live_check, schemas, second_opinion
from casebook.engine import decide_all
from casebook.load import Data

from .conftest import POLICY, go_offline, message, text


def opinion(decision, code, rationale="Because of KYC-1."):
    return message(text(json.dumps({"decision": decision, "reason_code": code, "rationale": rationale})))


def quiet(*args):
    pass


# ----------------------------------------------------------- second opinion

def test_facts_leave_out_everything_the_engine_concluded(world):
    for hit_id in world.cases:
        alert, hit = world.case(hit_id)
        payload = second_opinion.facts(alert, hit)
        blob = json.dumps(payload)
        for key in ('"decision"', '"reason_code"', '"reason_codes"', '"checks"', '"summary"', '"blocker"',
                    '"counterfactuals"', '"evidence"', '"about_hit"', '"why_not"', '"outcome"'):
            assert key not in blob, (hit_id, key)
        assert hit["summary"] not in blob and hit["reason_code"] not in blob
        assert payload["hit"]["names"] and payload["alert"]["screened_at"] == "2026-11-01"
    alert, hit = world.case("H-SEPT")
    assert [c["records"] for c in second_opinion.facts(alert, hit)["customers"]] == [["CUS-1", "CUS-2"], ["CUS-3"]]


def test_request_has_the_policy_as_a_cached_prefix_and_a_strict_output_schema(world):
    alert, hit = world.case("H-ABROAD")
    request = second_opinion.build_request(POLICY, alert, hit)
    assert request["model"] == "claude-haiku-5-5"
    system = request["system"]
    assert len(system) == 1 and POLICY.strip() in system[0]["text"] and system[0]["cache_control"] == {"type": "ephemeral"}
    assert request["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": schemas.SECOND_OPINION}}
    other = second_opinion.build_request(POLICY, *world.case("H-NAME"))
    assert other["system"] == system                               # byte-identical prefix across hits
    assert set(schemas.SECOND_OPINION["properties"]["decision"]["enum"]) == {"DIFFERENT_ENTITY", "SAME_ENTITY", "REVIEW"}
    assert "tools" not in request and "tool_choice" not in request


def test_offline_batch_writes_marked_stand_ins_and_resumes(world, tmp_path):
    out = str(tmp_path / "llm" / "second_opinion.json")
    lines = []
    first = second_opinion.run(world.folder, out, limit=3, log=lines.append)
    assert first["written"] == 3 and first["compared"] == 3 and first["stubs"] == 3
    assert any("stand-in" in l for l in lines) and any("engine \\ second opinion" in l for l in lines)
    saved = json.load(open(out, encoding="utf-8"))
    assert list(saved) == list(world.cases)[:3]
    for hit_id, entry in saved.items():
        assert set(entry) == {"decision", "reason_code", "rationale", "agrees", "model", "stub"}
        assert entry["model"] == "mock" and entry["stub"] is True and entry["rationale"].startswith("Offline stand-in")
        assert entry["decision"] in schemas.DECISIONS and entry["reason_code"] in schemas.REASON_CODES[entry["decision"]]
        assert entry["agrees"] == (entry["decision"] == world.cases[hit_id][1]["decision"])

    second = second_opinion.run(world.folder, out, log=quiet)       # resumes: only the missing five
    assert second["written"] == len(world.cases) - 3 and second["compared"] == len(world.cases)
    assert sum(second["matrix"].values()) == len(world.cases)
    assert second_opinion.run(world.folder, out, log=quiet)["written"] == 0
    assert second_opinion.run(world.folder, out, redo=True, limit=1, log=quiet)["compared"] == 1


def test_live_batch_records_agreement_and_never_touches_the_engine(world, tmp_path, fake_model, monkeypatch):
    out = str(tmp_path / "second_opinion.json")
    second_opinion.run(world.folder, out, limit=2, log=quiet)       # two stand-ins from an earlier offline run
    before = decide_all(Data(world.folder))

    def reply(request):
        case = request["messages"][0]["content"]
        if '"hit_id": "H-SAME"' in case:
            return opinion("SAME_ENTITY", "NAME_DOB_MATCH")
        if '"hit_id": "H-ABROAD"' in case:
            return opinion("SAME_ENTITY", "LOCATION_MISMATCH")      # disagrees, with a code from another decision
        if '"hit_id": "H-DEAD"' in case:
            return message(text("not json"))
        return opinion("REVIEW", "INSUFFICIENT_DATA")

    model = fake_model([reply], repeat_last=True)
    summary = second_opinion.run(world.folder, out, concurrency=3, log=quiet)
    saved = json.load(open(out, encoding="utf-8"))
    assert saved["H-SAME"] == {"decision": "SAME_ENTITY", "reason_code": "NAME_DOB_MATCH", "rationale": "Because of KYC-1.",
                               "agrees": True, "model": "claude-haiku-5-5"}     # the stand-in was replaced
    assert saved["H-ABROAD"]["agrees"] is False and saved["H-ABROAD"]["reason_code"] is None
    assert "H-DEAD" not in saved and [e[0] for e in summary["errors"]] == ["H-DEAD"]
    assert not any(e.get("stub") for e in saved.values()) and len(model.requests) == len(world.cases)
    assert summary["matrix"]["REVIEW|SAME_ENTITY"] == 1 and summary["matrix"]["SAME_ENTITY|SAME_ENTITY"] == 1
    assert all(r["model"] == "claude-haiku-5-5" and POLICY.strip() in r["system"][0]["text"] for r in model.requests)
    assert decide_all(Data(world.folder)) == before                 # same decisions, byte for byte

    go_offline(monkeypatch)                                         # replay from the cache into a fresh file
    replay = second_opinion.run(world.folder, str(tmp_path / "again.json"), log=quiet)
    again = json.load(open(str(tmp_path / "again.json"), encoding="utf-8"))
    assert again["H-SAME"] == saved["H-SAME"] and again["H-DEAD"]["stub"] is True and replay["stubs"] == 1


def test_command_line(world, tmp_path, capsys):
    out = str(tmp_path / "so.json")
    assert second_opinion.main([world.folder, "--out", out, "--limit", "2"]) == 0
    printed = capsys.readouterr().out
    assert "Agreement with the engine: " in printed and "DIFFERENT_ENTITY" in printed
    assert len(json.load(open(out, encoding="utf-8"))) == 2


def test_live_run_needs_the_policy(world, tmp_path, monkeypatch):
    bare = tmp_path / "bare"
    bare.mkdir()
    for name in ("alerts", "hits", "customers"):
        (bare / f"{name}.jsonl").write_text(open(f"{world.folder}/{name}.jsonl", encoding="utf-8").read(), encoding="utf-8")
    assert second_opinion.run(str(bare), str(tmp_path / "o.json"), limit=1, log=quiet)["stubs"] == 1   # offline is fine
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(FileNotFoundError):
        second_opinion.run(str(bare), str(tmp_path / "o.json"), limit=1, log=quiet)
    assert second_opinion.main([str(bare), "--out", str(tmp_path / "o.json")]) == 2


# ------------------------------------------------------- extraction fallback

NOTE_TEXT = "Customer called about a blocked card after a trip to Canada."


def routing(tmp_path):
    path = tmp_path / "routing.json"
    path.write_text(json.dumps({
        "notes": [{"id": "NOTE-1", "text": "stale copy of the note", "predicted": "residence", "reason": "country"},
                  {"id": "NOTE-X", "text": "He has lived in Ghana since 2010.", "predicted": "residence", "reason": "x"}],
        "snippets": [{"id": "SR-HIT", "text": "ignored: the record is used", "predicted": "listing", "reason": "x"}],
    }), encoding="utf-8")
    return str(path)


def test_quote_must_occur_in_the_source():
    assert extract_fallback.verified("a trip to  Canada", NOTE_TEXT) is True      # whitespace aside
    assert extract_fallback.verified("a trip to canada", NOTE_TEXT) is False      # not word for word
    assert extract_fallback.verified("lived in Canada", NOTE_TEXT) is False
    assert extract_fallback.verified("", NOTE_TEXT) is False


def test_offline_extraction_uses_the_regex_extractors(world, tmp_path):
    out = str(tmp_path / "extractions.json")
    summary = extract_fallback.run(world.folder, routing(tmp_path), out, log=quiet)
    saved = json.load(open(out, encoding="utf-8"))
    assert set(saved) == {"NOTE-1", "NOTE-X", "SR-HIT"} and summary["total"] == 3
    for entry in saved.values():
        assert set(entry) == {"fact", "quote", "verified", "stub"} and not schemas.validate(
            {"fact": entry["fact"], "quote": entry["quote"]}, schemas.EXTRACTION)
    assert saved["NOTE-1"]["fact"]["kind"] == "none" and saved["NOTE-1"]["verified"] is False
    assert saved["NOTE-X"]["fact"] == {**saved["NOTE-X"]["fact"], "kind": "residence", "countries": ["Ghana"]}
    assert saved["NOTE-X"]["verified"] is True
    assert saved["SR-HIT"]["fact"]["kind"] == "listing" and saved["SR-HIT"]["fact"]["age"] == 30


def test_live_extraction_verifies_the_quote_against_the_record(world, tmp_path, fake_model):
    def reply(request):
        sent = json.loads(request["messages"][0]["content"].split("\n\n", 1)[1])
        fact = {"kind": "residence", "name": None, "date_of_birth": None, "countries": ["Canada"], "approved": None,
                "listing_phrase": None, "age": None, "age_at_date": None}
        quote = "a trip to Canada" if sent["text"] == NOTE_TEXT else "has lived in Canada for years"
        return message(text(json.dumps({"fact": fact, "quote": quote})))

    model = fake_model([reply], repeat_last=True)
    out = str(tmp_path / "extractions.json")
    extract_fallback.run(world.folder, routing(tmp_path), out, log=quiet)
    saved = json.load(open(out, encoding="utf-8"))
    assert saved["NOTE-1"] == {"fact": saved["NOTE-1"]["fact"], "quote": "a trip to Canada", "verified": True}
    assert saved["NOTE-X"]["verified"] is False and saved["SR-HIT"]["verified"] is False   # quotes not in those texts
    assert all(r["model"] == "claude-haiku-5-5" for r in model.requests) and len(model.requests) == 3
    assert extract_fallback.run(world.folder, routing(tmp_path), out, log=quiet)["read"] == 0   # resumable


def test_extraction_command_reports_a_missing_routing_file(world, tmp_path):
    assert extract_fallback.main([world.folder, "--routing", str(tmp_path / "none.json"), "--out", str(tmp_path / "o.json")]) == 2
    assert extract_fallback.main([world.folder, "--routing", routing(tmp_path), "--out", str(tmp_path / "o.json")]) == 0


# ---------------------------------------------------------------- live check

def test_live_check_runs_offline_and_says_so(world, capsys):
    verdicts = live_check.run(world.folder, "H-ABROAD", log=quiet)
    assert verdicts == {k: "STUB" for k in ("note", "queue", "investigate", "chat", "second_opinion", "extract")}
    assert live_check.main([world.folder]) == 0
    assert "offline run" in capsys.readouterr().out
    assert live_check.main([world.folder, "--hit", "H-MISSING"]) == 2


def test_live_check_reports_a_feature_that_fell_back(world, fake_model, capsys):
    good_note = message(text(json.dumps({"body": "The verified name agrees (KYC-1)."})))
    fake_model([good_note, client.LLMError("API error 500")], repeat_last=True)
    assert live_check.main([world.folder, "--hit", "H-ABROAD", "--use-cache"]) == 1
    printed = capsys.readouterr().out
    assert "note=live" in printed and "investigate=STUB" in printed and "chat=STUB" in printed
    assert "second_opinion=FAILED (API error 500)" in printed and "Fell back or failed" in printed
