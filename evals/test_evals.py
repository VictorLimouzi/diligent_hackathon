"""Invented fixtures; run python3 -m unittest discover -s evals."""
import datetime as dt
import gzip
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
import unicodedata as ud

from evals.perturb import (COUNTRY_GROUPS, CRASHES, REPRESENTATIONS, Corpus,
                           Experiment, date_form, fold, full_date, make_experiment,
                           name_noise, rewrap, values)
from evals.run import CLEAR, REVIEW, SAME, compare, evaluate, failed, fingerprint, score, write_report
from evals.minimize import fast_check, matches


def fixture(folder):
    tables = {
        "customers": [
            {"record_id": "CUS-1", "full_name": "John O'Brien-Smith", "entity_type": "Individual",
             "dob": "1980-05-02", "phone": "+44 123", "email": "one@example.test",
             "nationality": "British", "residence_country": "GB", "preferred_name": None,
             "onboarded_at": "2020-04-03", "last_kyc_refresh": "2024-06-07"},
            {"record_id": "CUS-2", "full_name": "Émilie Dupont", "entity_type": "Individual",
             "dob": "1968-02", "nationality": "French", "residence_country": "France"}],
        "accounts": [{"account_id": "ACC-1", "customer_id": "CUS-1", "login_handle": "john1"}],
        "onboarding": [{"customer_id": "CUS-1", "kyc_applicant_id": "APP-1"}],
        "kyc_checks": [{"check_id": "KYC-1", "applicant_id": "APP-1", "result": "approved",
                        "verified_name": "John O'Brien-Smith", "verified_dob": "2 May 1980",
                        "document_country": "United Kingdom", "checked_at": "2024-02-01"}],
        "notes": [{"note_id": "NOTE-1", "requester_email": "one@example.test", "channel": "support",
                   "created_at": "2024-02-01", "text": "John O'Brien-Smith lived in Germany.\n\nHe moved in May 2020."}],
        "search_results": [{"result_id": "SR-1", "query": "john obrien smith", "title": "John O'Brien-Smith - report",
                            "snippet": "In May 2020, John O'Brien-Smith, 40, was a politically exposed person in Germany."}],
        "alerts": [{"alert_id": "A-1", "customer_name": "John O'Brien-Smith", "entity_type": "Individual",
                    "dob": "02/05/1980", "screened_at": "2026-11-06", "account_ref": "ACC-1",
                    "hit_ids": "H-1; H-2", "hit_count": "2.0"}],
        "hits": [{"hit_id": "H-1", "alert_id": "A-1", "entity_type": "Individual", "name": "John O'Brien-Smith",
                  "aliases": "Jean O'Brien-Smith; Jon O'Brien-Smith", "dobs": "1980-05-02; 1981",
                  "countries": "Germany; United Kingdom", "addresses": "Berlin, Germany; London, United Kingdom",
                  "categories": "PEP; Enforcement", "match_score": "100.0"},
                 {"hit_id": "H-2", "alert_id": "A-1", "name": "John O'Brien-Smith",
                  "entity_type": "Individual", "dobs": "1950-01-01", "countries": "Germany"}],
    }
    for table, rows in tables.items():
        (Path(folder) / (table + ".jsonl")).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    # Use actual bundled rules so optional-file removal preserves the policy.
    default = Path(__file__).resolve().parents[1] / "casebook/name_variants_default.json"
    (Path(folder) / "name_variants.json").write_bytes(default.read_bytes())


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        fixture(self.tmp.name)
        self.corpus = Corpus(self.tmp.name)

    def written(self, experiment):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.corpus.write(tmp.name, experiment)
        return Path(tmp.name)


class Dates(unittest.TestCase):
    def test_round_trip_all_forms_and_leap_day(self):
        for raw in ("1980-05-02", "29/02/2000", "8 October 1972", "February 19, 2002"):
            for form in range(4):
                with self.subTest(raw=raw, form=form):
                    self.assertEqual(full_date(date_form(raw, form)), full_date(raw))
        self.assertEqual(full_date("29/02/2000"), dt.date(2000, 2, 29))

    def test_partial_invalid_and_timestamp_unchanged(self):
        for raw in (None, "1980", "1968-02", "0000-00-00", "2023-02-29", "2024-01-01T12:03:04Z"):
            for form in range(4):
                self.assertEqual(date_form(raw, form), raw)


class Names(unittest.TestCase):
    def test_case_accents_hyphens_order_preserve_parts(self):
        def canonical(s):
            import re
            return sorted(re.findall(r"[^\W\d_]+", fold(s)))
        for mode in ("case", "accents_strip", "accents_add", "hyphens", "order"):
            for raw in ("Jérôme Kerviel", "John Smith", "Émilie Anne-Dupont"):
                with self.subTest(mode=mode, raw=raw):
                    result = name_noise(raw, mode, random.Random(0))
                    self.assertEqual(canonical(result), canonical(raw))

    def test_order_does_not_move_brackets_or_legal_punctuation(self):
        for raw in ("John Smith (aka Jack)", "Pescanova S.A.", "Smith, John"):
            self.assertEqual(name_noise(raw, "order", random.Random(0)), raw)

    def test_variant_is_explicitly_listed_and_brackets_untouched(self):
        groups = [["john", "jon", "jean"]]
        result = name_noise("John Smith (John)", "variants", random.Random(7), groups)
        self.assertIn(result.split()[0], groups[0])
        self.assertTrue(result.endswith("Smith (John)"))
        self.assertEqual(result, name_noise("John Smith (John)", "variants", random.Random(7), groups))


class Representations(FixtureTest):
    def test_replay_rejects_changed_source_record(self):
        m = self.corpus.mutation("hits", 0, "name", "Jon Smith")
        m.record_id = "H-NOT-THE-SOURCE-RECORD"
        with self.assertRaisesRegex(ValueError, "replay record differs"):
            self.written(Experiment("bad-replay", mutations=(m,)))

    def test_hit_mutation_identifies_hit_instead_of_parent_alert(self):
        exp = make_experiment(self.corpus, "shape_lists")
        self.assertTrue(all(m.record_id.startswith("H-") for m in exp.mutations if m.file == "hits.jsonl"))

    def test_minimizer_restores_row_and_matches_real_folder(self):
        from casebook.load import Data
        data = Data(self.corpus.folder)
        m = self.corpus.mutation("hits", 0, "dobs", "1950-01-01")
        original = data.hits["H-1"].copy()
        result = fast_check(data, self.corpus, m, ("A-1", "H-1"))
        actual, errors = score(self.written(Experiment("one-field", mutations=(m,))))
        self.assertEqual(errors, [])
        self.assertTrue(matches({"after": actual[("A-1", "H-1")]}, result))
        self.assertEqual(data.hits["H-1"], original)

    def test_date_fields_and_customer_countries(self):
        for name in REPRESENTATIONS[:6]:
            exp = make_experiment(self.corpus, name, 2)
            self.assertTrue(exp.mutations)
            for m in exp.mutations:
                if name.startswith("dates"):
                    self.assertEqual(full_date(m.before), full_date(m.after))
                else:
                    self.assertTrue(any(m.before in group and m.after in group for group in COUNTRY_GROUPS))

    def test_lists_and_numbers_preserve_values(self):
        for name in ("shape_lists", "shape_numbers"):
            exp = make_experiment(self.corpus, name)
            self.assertTrue(exp.mutations)
            for m in exp.mutations:
                if name == "shape_lists":
                    self.assertEqual(values(m.before), m.after)
                    self.assertIsInstance(m.after, list)
                else:
                    self.assertEqual(float(m.before), m.after)
                    self.assertIsInstance(m.after, (int, float))

    def test_missing_empty_null_are_only_optional_unknowns(self):
        for name in ("shape_empty", "shape_missing"):
            exp = make_experiment(self.corpus, name)
            self.assertTrue(exp.mutations)
            for m in exp.mutations:
                self.assertIn(m.before, (None, ""))
                self.assertEqual(m.after, None if m.delete else "")
                self.assertNotIn(m.field, ("record_id", "hit_id", "hit_ids"))
            self.assertEqual(score(self.written(exp))[1], [])

    def test_key_and_row_shuffle_preserve_all_records(self):
        for name in ("shape_keys", "shape_rows"):
            exp = make_experiment(self.corpus, name, 8)
            dest = self.written(exp)
            loaded = Corpus(dest)
            for table in self.corpus.rows:
                canonical = lambda rows: sorted(json.dumps(r, sort_keys=True) for r in rows)
                self.assertEqual(canonical(self.corpus.rows[table]), canonical(loaded.rows[table]))
            same = make_experiment(self.corpus, name, 8)
            self.assertEqual(exp, same)

    def test_short_and_long_filenames_and_optional_rules(self):
        baseline, errors = score(self.corpus.folder)
        self.assertEqual(errors, [])
        exp = make_experiment(self.corpus, "filenames_short")
        short = self.written(exp)
        self.assertTrue((short / "kyc.jsonl").is_file())
        self.assertTrue((short / "search.jsonl").is_file())
        for name in ("filenames_long", "rules_absent", "rules_present",
                     "filenames_short_rules_absent", "filenames_long_rules_absent"):
            self.corpus = Corpus(short)
            exp = make_experiment(self.corpus, name)
            self.assertIsNone(exp.skip)
            dest = self.written(exp)
            results, errors = score(dest)
            self.assertEqual(errors, [])
            self.assertEqual(baseline, results)
            if name == "rules_absent":
                self.corpus = Corpus(dest)
                added = make_experiment(self.corpus, "rules_present")
                self.assertIn("name_variants.json", added.add)
                self.assertEqual(score(self.written(added))[0], baseline)

    def test_different_fallback_rules_are_explicitly_skipped(self):
        (self.corpus.folder / "name_variants.json").write_text('{"name_variants": [["x", "y"]]}')
        self.corpus = Corpus(self.corpus.folder)
        self.assertIn("changes semantics", make_experiment(self.corpus, "rules_absent").skip)

    def test_whitespace_unicode_and_text_keep_semantic_content(self):
        for name in ("whitespace_edges", "whitespace_nbsp", "unicode_apostrophes", "unicode_nfc", "unicode_nfd", "text_rewrap"):
            exp = make_experiment(self.corpus, name)
            for m in exp.mutations:
                if name.startswith("whitespace"):
                    self.assertEqual(m.before.split(), m.after.split())
                elif name == "unicode_apostrophes":
                    self.assertEqual(m.before, m.after.replace("’", "'"))
                elif name.startswith("unicode_n"):
                    self.assertEqual(ud.normalize("NFC", m.before), ud.normalize("NFC", m.after))
                else:
                    self.assertEqual(m.before.rstrip().removesuffix(".").split(), m.after.split())
                    self.assertEqual(m.before.count("\n\n"), m.after.count("\n\n"))
                    self.assertEqual(m.before.count("May"), m.after.count("May"))
        self.assertEqual(rewrap("A May report.\n\nA second line."), "A  May  report.\n\nA  second  line")

    def test_typography_never_changes_identifier_or_url_values(self):
        self.corpus.rows["customers"][0]["email"] = "o'brien@example.test"
        self.corpus.rows["customers"][0]["login_name"] = "O'Brien"
        self.corpus.rows["hits"][0]["source_urls"] = "https://example.test/O'Brien"
        for name in ("whitespace_edges", "whitespace_nbsp", "unicode_apostrophes", "unicode_nfc", "unicode_nfd"):
            self.assertFalse(any(m.field in {"email", "requester_email", "login_name", "source_urls"}
                                 for m in make_experiment(self.corpus, name).mutations))

    def test_all_experiments_are_seeded_and_source_unchanged(self):
        hashes = fingerprint(self.corpus.folder)
        baseline, errors = score(self.corpus.folder)
        self.assertEqual(errors, [])
        for name in REPRESENTATIONS + CRASHES:
            with self.subTest(name=name):
                a, b = make_experiment(self.corpus, name, 9), make_experiment(self.corpus, name, 9)
                self.assertEqual(a, b)
                row = evaluate(self.corpus, baseline, a)
                self.assertIn("crashes", row)
                self.assertTrue(all(len(v) <= 20 for v in row["examples"].values()))
        self.assertEqual(hashes, fingerprint(self.corpus.folder))

    def test_malformed_probes_intentionally_break_one_selected_alert(self):
        for name in CRASHES:
            exp = make_experiment(self.corpus, name)
            self.assertEqual(exp.kind, "malformed")
            self.assertTrue(exp.affected_hits)
            self.assertTrue(exp.mutations)
            if name == "crash_missing_hit_ids":
                self.assertTrue(exp.mutations[0].delete)
            elif name == "crash_unknown_hit":
                self.assertEqual(exp.affected_hits, ("H-EVAL-UNKNOWN",))
            elif name == "crash_null_names":
                self.assertTrue(all(m.after is None for m in exp.mutations))
            elif name == "crash_invalid_dates":
                self.assertTrue(all(m.after == "0000-00-00" for m in exp.mutations))
            elif name == "crash_missing_account":
                self.assertEqual(exp.mutations[0].after, "ACC-EVAL-MISSING")


class Reporting(unittest.TestCase):
    def test_report_keeps_all_ids_in_deterministic_compressed_manifest(self):
        import copy
        report = {"experiments": [{"name": "tiny", "manifest": {
            "record_ids": {"hits.jsonl": [f"H-{i}" for i in range(25)]}}}]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            write_report(path, copy.deepcopy(report))
            first = path.read_bytes(), path.with_suffix(".records.json.gz").read_bytes()
            self.assertEqual(len(json.loads(first[0])["experiments"][0]["manifest"]["record_id_examples"]["hits.jsonl"]), 20)
            self.assertEqual(len(json.loads(gzip.decompress(first[1]))["tiny"]["hits.jsonl"]), 25)
            write_report(path, copy.deepcopy(report))
            self.assertEqual(first, (path.read_bytes(), path.with_suffix(".records.json.gz").read_bytes()))

    @staticmethod
    def row(decision, reason="INSUFFICIENT_DATA"):
        return {"hit_id": "H-1", "alert_id": "A-1", "decision": decision, "reason_code": reason}

    def test_transitions_omissions_and_review_requirement(self):
        exp = Experiment("invented", kind="malformed", affected_hits=("H-1",))
        for before, after, category in ((REVIEW, CLEAR, "new_clears"), (CLEAR, SAME, "new_confirms"),
                                        (CLEAR, REVIEW, "lost_clears")):
            cats = compare({("A-1", "H-1"): self.row(before)}, {("A-1", "H-1"): self.row(after)}, exp)
            self.assertEqual(len(cats[category]), 1)
            self.assertEqual(len(cats["hits_changed"]), 1)
            self.assertEqual(len(cats["review_violations"]), int(after != REVIEW))
        cats = compare({("A-1", "H-1"): self.row(SAME)}, {}, exp)
        self.assertEqual(len(cats["missing_results"]), 1)
        self.assertEqual(len(cats["review_violations"]), 1)

    def test_unknown_profile_review_is_expected_addition(self):
        exp = Experiment("unknown", affected_hits=("H-1",))
        cats = compare({}, {("A-1", "H-1"): self.row(REVIEW)}, exp)
        self.assertEqual(cats["unexpected_results"], [])
        self.assertEqual(cats["review_violations"], [])

    def test_caught_errors_are_not_silently_hidden(self):
        def engine(data, on_error):
            try:
                raise TypeError("invented failure")
            except TypeError as exc:
                on_error({"alert_id": "A-1"}, exc)
            return [self.row(REVIEW)]
        with patch("evals.run.Data"), patch("evals.run.decide_all", side_effect=engine):
            results, errors = score("unused")
        self.assertEqual(len(results), 1)
        self.assertEqual(errors[0]["kind"], "caught_alert_error")
        self.assertIn("TypeError", errors[0]["traceback"])

    def test_loader_crash_and_failure_exit_criteria(self):
        with patch("evals.run.Data", side_effect=ValueError("bad row")):
            results, errors = score("unused")
        self.assertEqual(results, {})
        self.assertEqual(errors[0]["kind"], "escaped_exception")
        self.assertTrue(failed({"crashes": 1}))
        self.assertTrue(failed({"new_clears": 1}))
        self.assertTrue(failed({"review_violations": 1}))
        self.assertFalse(failed({"new_confirms": 1, "lost_clears": 2}))


if __name__ == "__main__":
    unittest.main()
