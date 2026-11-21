"""Tests for the policy rules, including the paths the practice labels do not cover.

Run: python3 -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from casebook.countries import countries_in_text, country, country_of_place  # noqa: E402
from casebook.dates import PDate, dob_agrees, dob_contradicts, max_age_at, parse_date  # noqa: E402
from casebook.engine import decide_alert  # noqa: E402
from casebook.extract import (age_at_date, dated_reports, parse_kyc_note,  # noqa: E402
                              parse_snippet, profile_birth_dates)
from casebook.load import Data  # noqa: E402
from casebook.names import NameRules, name_parts, names_agree  # noqa: E402

RULES = NameRules([["muhammad", "mohammed", "mohamed"], ["mark", "marc", "marco"],
                   ["markus", "marcus", "marcos"], ["john", "jon", "jack", "juan"]])


def agree(a, b, company=False):
    return names_agree(name_parts(a, company, RULES), name_parts(b, company, RULES), RULES)


class Names(unittest.TestCase):
    def test_variants_accents_order(self):
        self.assertTrue(agree("Mohamed El Idrissi", "IDRISSI, Muhammad el"))
        self.assertTrue(agree("Jérôme Kerviel", "KERVIEL jerome"))

    def test_together_or_apart(self):
        self.assertTrue(agree("Abdulsalam", "Abdul Salam"))
        self.assertFalse(agree("Abdulsalam Musa", "Abdul Salam Ibrahim"))

    def test_edit_allowance_by_shorter_part(self):
        self.assertTrue(agree("Elvira Velasco Mooillo", "Elvira Velasco Morillo"))
        self.assertFalse(agree("Gareth Wyn Llewellyn", "Gareth Wynn"))   # wyn has 3 letters
        self.assertFalse(agree("Markus Gerhard Mueller", "Mark Mueller"))

    def test_two_parts_or_all_of_shorter(self):
        self.assertFalse(agree("Chinedu Azeez", "Ridwan, Azeez"))
        self.assertTrue(agree("Abdul Rahman Yasin", "Abdul Rahman"))

    def test_placeholders_initials_brackets(self):
        self.assertEqual(name_parts("N/A TEST John 123 X. Smith (aka Bob)"), ("john", "smith"))
        self.assertEqual(name_parts("ABUBAKAR Abubakar"), ("abubakar",))

    def test_company_legal_forms(self):
        self.assertEqual(name_parts("Pescanova, S.A.", True), ("pescanova",))
        self.assertTrue(agree("Pescanova SA", "Pescanova, Sociedad Anónima", True))


class Dates(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_date("19/05/2004"), PDate(2004, 5, 19))
        self.assertEqual(parse_date("February 19, 2002"), PDate(2002, 2, 19))
        self.assertEqual(parse_date("8 October 1972"), PDate(1972, 10, 8))
        self.assertEqual(parse_date("1968-02"), PDate(1968, 2))
        self.assertEqual(parse_date("1980"), PDate(1980))
        self.assertIsNone(parse_date("unknown"))

    def test_contradiction(self):
        self.assertTrue(dob_contradicts(PDate(1979, 11, 8), PDate(1977)))
        self.assertFalse(dob_contradicts(PDate(1979, 11, 8), PDate(1978)))
        self.assertTrue(dob_contradicts(PDate(1968, 3, 5), PDate(1968, 2)))
        self.assertTrue(dob_contradicts(PDate(1968, 3, 5), PDate(1968, 3, 6)))
        self.assertFalse(dob_contradicts(PDate(1968, 3, 5), PDate(1969, 7, 1)))

    def test_agreement(self):
        self.assertTrue(dob_agrees(PDate(1968, 3, 5), PDate(1968)))
        self.assertTrue(dob_agrees(PDate(1968, 3, 5), PDate(1968, 3)))
        self.assertFalse(dob_agrees(PDate(1968, 3, 5), PDate(1969)))

    def test_age(self):
        self.assertEqual(max_age_at(PDate(2007), PDate(2020, 1, 8)), 13)
        self.assertEqual(max_age_at(PDate(2004, 6, 1), PDate(2020, 5, 31)), 15)
        self.assertEqual(max_age_at(PDate(2004, 6, 1), PDate(2020, 6, 1)), 16)


class Countries(unittest.TestCase):
    def test_codes_nationalities_territories(self):
        self.assertEqual(country("GB"), "United Kingdom")
        self.assertEqual(country("Nigerian"), "Nigeria")
        self.assertEqual(country("Cote D'Ivoire"), "Côte d'Ivoire")
        self.assertEqual(country("Northern Mariana Islands"), "Northern Mariana Islands")
        self.assertIsNone(country("International"))
        self.assertIsNone(country("Europe"))

    def test_places_and_text(self):
        self.assertEqual(country_of_place("Atlanta, Georgia, United States [Residency]"), "United States")
        self.assertEqual(countries_in_text("via his Bangladeshi national ID"), ["Bangladesh"])
        self.assertEqual(countries_in_text("JORDAN SMITH lived in Nigeria"), ["Nigeria"])


    def test_notes_read_more_place_words_than_search_results(self):
        from casebook.extract import note_countries, parse_snippet
        # In a customer's note a missed place could clear a true match, so more words are read.
        self.assertEqual(note_countries("The client lived and worked in Britain from 2010 to 2015."),
                         (["United Kingdom"], []))
        self.assertEqual(note_countries("Customer asked about a transfer to Bank of America."),
                         ([], ["United States"]))      # only mentioned: blocks a clear, confirms nothing
        self.assertEqual(countries_in_text("worked in the U.S. and the U.K. until 2019"),
                         ["United States", "United Kingdom"])
        # In a search result an extra country could make a namesake look like the hit.
        snippet = "Jo Bloggs, 45, formerly of Bank of America, was named in adverse media in Brazil in May 2019."
        self.assertEqual(parse_snippet("Jo Bloggs - news", snippet)["countries"], ["Brazil"])
        for name in ("Slovak Republic", "Kyrgyz Republic", "Lao PDR", "United Republic of Tanzania", "BVI"):
            self.assertIsNotNone(country(name), name)


class Extraction(unittest.TestCase):
    def test_kyc_note(self):
        r = parse_kyc_note("Persona verified RUBEL SOHEL MONDAL (DOB 8 October 1972) on 2024-07-24 via his "
                           "Bangladeshi national ID.", "2024-07-24")
        self.assertEqual(r["name"], "RUBEL SOHEL MONDAL")
        self.assertEqual(r["dob"], PDate(1972, 10, 8))
        self.assertEqual(r["countries"], ["Bangladesh"])
        self.assertTrue(r["approved"])

    def test_kyc_note_year_only_and_rejected(self):
        r = parse_kyc_note("Identity check via Onfido on 2024-10-03 for ASAD KHAN, born 1966, using a "
                           "United Kingdom driving licence.", "2024-10-03")
        self.assertEqual(r["dob"], PDate(1966))
        r = parse_kyc_note("Veriff rejected the passport of JOHN DOE, DOB 1990-01-01, on 2024-01-02.", "2024-01-02")
        self.assertFalse(r["approved"])

    def test_kyc_note_birth_date_before_the_keyword(self):
        # The check date follows the word DOB here; the birth date comes before it.
        r = parse_kyc_note("Sumsub confirms the driving licence from Italy for SOFIA ROSSI, who lists 06/01/1998 "
                           "as her DOB, is valid as of 2024-08-15.", "2024-08-15")
        self.assertEqual(r["dob"], PDate(1998, 1, 6))
        # A lone date that is really the check date is never taken as a birth date.
        r = parse_kyc_note("ANNA BELL passed the Veriff check; DOB confirmed on 2024-08-15.", "2024-08-14")
        self.assertIsNone(r["dob"])

    def test_kyc_note_not_approved_wordings(self):
        for wording in ("could not be verified", "did not pass", "was unsuccessful", "is pending review",
                        "document appears tampered", "check was cancelled"):
            r = parse_kyc_note(f"Onfido check for JOHN DOE, DOB 1990-01-01, {wording} on 2024-01-02.", "2024-01-02")
            self.assertFalse(r["approved"], wording)

    def test_implausible_ages_are_not_read(self):
        self.assertIsNone(age_at_date("Kofi Mensah, 7, was named in a March 2019 notice."))
        self.assertEqual(age_at_date("Kofi Mensah, 47, was named in a March 2019 notice.")[0], 47)

    def test_wordings_from_the_stress_test(self):
        # Failed checks worded without the usual words must not read as approved.
        for text in ("Heads up, NILS DROZD's ID check bounced on 2023-05-21. Birth date they gave was 6 January 1961.",
                     "We were not able to complete the identity check for MARCO GREEN on 2022-02-03. Stated DOB: 1978-11-14.",
                     "ID check didn't pass for AMINA BELLO — born 1980-05-02 — on 2024-01-02.",
                     "2024-01-02 — AMINA BELLO, DOB 1980-05-02: identity not established.",
                     "IDV result negative. 2024-01-02. AMINA BELLO. 1980-05-02."):
            self.assertFalse(parse_kyc_note(text, None)["approved"], text)
        # ...and ordinary approvals still do, including "issued in" and "no issues".
        for text in ("Sumsub completed identity verification for NILS MARTIN, born December 13, 1977, using a "
                     "national ID issued in Germany on 2023-02-19.",
                     "Veriff verified AMINA BELLO (DOB 1980-05-02) on 2024-01-02 with no issues found; Nigerian passport."):
            self.assertTrue(parse_kyc_note(text, None)["approved"], text)
        # Names: fields split by slashes, possessives, sentence-final names.
        self.assertEqual(parse_kyc_note("IDV/Sumsub/approved/2023-01-21/MARCO LAWAL/1977-04-14/Italy", "2023-01-21")["name"],
                         "MARCO LAWAL")
        self.assertEqual(parse_kyc_note("Good to go — AMINA BELLO's passport (Nigeria) passed Veriff's check on "
                                        "2024-01-02. Born 1980-05-02.", "2024-01-02")["name"], "AMINA BELLO")
        self.assertEqual(parse_kyc_note("Approved on 2024-01-02: AMINA BELLO. Born 1980-05-02. Holds a Nigerian passport.",
                                        "2024-01-02")["name"], "AMINA BELLO")
        # Birth sentences with the date in unusual places.
        self.assertEqual(profile_birth_dates("1980 is the recorded birth date of Rajesh Lawal.")[0], [PDate(1980)])
        self.assertEqual(profile_birth_dates("Files show that Elena Romano came into the world on 5 August 1998.")[0],
                         [PDate(1998, 8, 5)])
        self.assertEqual(profile_birth_dates("Kofi Mensah (b. 1968)")[0], [PDate(1968)])
        # A lone date beside a birth word is not taken as the birth date when the sentence dates something else.
        self.assertEqual(profile_birth_dates("Born in Lagos; sanctioned on 12 March 2019.")[0], [])
        # A provider's numeric date is read in full only when it can be read one way.
        self.assertEqual(profile_birth_dates("Name: Doe, Robin\nDOB: 6/20/1955\nHeight: 5' 10\"")[0], [PDate(1955, 6, 20)])
        self.assertEqual(profile_birth_dates("Name: Doe, Robin\nDOB: 6/7/1955\nHeight: 5' 10\"")[0], [PDate(1955)])
        self.assertEqual(parse_date("20.6.1955", day_first=None), PDate(1955, 6, 20))
        self.assertEqual(parse_date("31/02/1990", day_first=None), PDate(1990))
        self.assertIsNone(parse_date("1990-02-31"))
        # Ages with the month and year written apart, or the year alone.
        self.assertEqual(profile_birth_dates("Back in 2019, around March, Kofi Mensah got picked up by police; "
                                             "Kofi Mensah was 45 then.")[0], [PDate(1973), PDate(1974)])
        self.assertEqual(profile_birth_dates("1984-November: Elena Lawal, 43, taken into custody.")[0],
                         [PDate(1940), PDate(1941)])
        r = parse_snippet("Sofia Kowalski - news", "A 1977 news profile focused on 21-year-old Sofia Kowalski, a "
                          "state-owned enterprise official in Pakistan during October.", "kowalski sofia")
        self.assertEqual((r["age"], r["date"]), (21, PDate(1977, 10)))

    def test_wordings_from_the_harder_stress_rounds(self):
        from casebook.extract import note_countries, note_is_approved
        # Failed checks worded without any of the usual words.
        for bad in ("Having been submitted on 2025-03-16, the identity evidence of JO BLOGGS, born 27 July 2003, "
                    "was found wanting by Persona.",
                    "ID check result: ✗ (JO BLOGGS, 16/10/1945, 2025-04-15)",
                    "Unfortunately the attempt by JO BLOGGS on 2022-10-05 to verify identity, with date of birth "
                    "May 9, 1986, was turned away.",
                    "Heads up - JO BLOGGS got knocked back by Onfido on 2023-03-13. Birthday on form: January 11, 1961."):
            self.assertFalse(note_is_approved(bad), bad)
        # Names inside key=value, quoted, bracketed and labelled records.
        for text_, name in (
                ('event=identity_check result=approved subject="JO BLOGGS" dob=21 December 1996 country=Germany '
                 'ts=2022-09-24', "JO BLOGGS"),
                ("[2024-06-02] [Onfido] [APPROVED] JO BLOGGS / 08/09/1984 / Kenya", "JO BLOGGS"),
                ("Result=PASS; name=JO BLOGGS; birth=23 July 1995; nationality=French; verified_on=2023-05-28", "JO BLOGGS"),
                ("status:approved|who:JO BLOGGS|born:1978-04-22|where:Bangladesh|when:2023-07-11|by:Persona", "JO BLOGGS"),
                ("Hi all, FYI JO BLOGGS is through ID verification as of 2023-07-15. They were born 13/12/1964; "
                 "French passport.", "JO BLOGGS")):
            self.assertEqual(parse_kyc_note(text_, "2023-01-01")["name"], name, text_)
        # "b. unknown" followed by an age at a date: the date is not the birth date.
        self.assertEqual(profile_birth_dates("Jo Bloggs (b. unknown) was, as of August 2005, 46 years old and "
                                             "under formal investigation.")[0], [PDate(1958), PDate(1959)])
        self.assertEqual(profile_birth_dates("Jo Bloggs (b. unknown) was 47 on July 2014 release.")[0],
                         [PDate(1966), PDate(1967)])
        # A country in a negated clause is not a place lived.
        self.assertEqual(note_countries("For the record, no UK ties during 2013–2021; customer was domiciled "
                                        "and working in Brazil."), (["Brazil"], ["United Kingdom"]))

    def test_profile_birth(self):
        self.assertEqual(profile_birth_dates("Christian Zaum was born on 30 June 1969.")[0], [PDate(1969, 6, 30)])
        self.assertEqual(profile_birth_dates("The age of Tomasz Drozd was 38 when he was questioned in "
                                             "January 2025.")[0], [PDate(1986), PDate(1987)])
        # A dated report is not read for ages.
        self.assertEqual(profile_birth_dates("According to x.com; May 05, 2020: a man, 30, was held.")[0], [])

    def test_dated_reports_exact_form(self):
        notes = ("According to the nation.com.pk; February 03, 2024: On that day...\n\n"
                 "According to the U.S. Courts; On April 23, 2013, he was sentenced.\n\n"
                 "According to FINRA; June 2016: fined.")
        self.assertEqual([d for d, _ in dated_reports(notes)], [PDate(2024, 2, 3)])

    def test_snippet(self):
        r = parse_snippet("Jordan Chad - news", "Jordan Chad, aged 26 and the subject of enforcement action "
                          "in Ireland, appeared in a January 2024 regulator's notice.", "chad jordan")
        self.assertEqual(r["countries"], ["Ireland"])
        self.assertEqual((r["age"], r["date"]), (26, PDate(2024, 1)))
        self.assertEqual(r["phrases"], ["subject of enforcement action"])
        # two different years in one sentence: no single date, so no age is read
        self.assertIsNone(age_at_date("A 1977 profile, reprinted in 1990, focused on a man of 21 in Pakistan."))


def write(folder, name, rows):
    with open(os.path.join(folder, name + ".jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def cust(cid, name, dob, country_, phone, email=None, etype="Individual"):
    return {"record_id": cid, "entity_type": etype, "full_name": name, "dob": dob, "nationality": country_,
            "residence_country": country_, "city": "Lagos", "address": "1 Road, Lagos",
            "email": email or f"{cid.lower()}@x.example", "phone": phone}


class Policy(unittest.TestCase):
    """End to end on a small invented data folder."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        customers = [
            cust("CUS-1", "Amina Bello", "1980-05-02", "Nigeria", "+234 1"),
            cust("CUS-2", "Amina Bello", "02/05/1980", "NG", "+234 1"),            # same person as CUS-1
            cust("CUS-3", "Amina Bello", "1980-09-09", "Nigeria", "+234 1"),       # family member, same phone
            cust("CUS-4", "Acme Trading Ltd", "2015-03-01", "Germany", "+49 2", etype="Company"),
            cust("CUS-5", "Tunde Musa", "1975-01-01", "Nigeria", "+234 5"),
            cust("CUS-6", "Kofi Mensah", "1990-04-04", "Ghana", "+233 6"),
            cust("CUS-7", "Lena Ostrom", "1985-02-02", "Freedonia", "+999 7"),   # a country no list knows
        ]
        accounts = [{"account_id": "ACC-1", "customer_id": "CUS-1", "login_handle": "aminab1"},
                    {"account_id": "ACC-4", "customer_id": "CUS-4", "login_handle": "acme4"},
                    {"account_id": "ACC-5", "customer_id": "CUS-5", "login_handle": "tundem5"},
                    {"account_id": "ACC-6", "customer_id": "CUS-6", "login_handle": "kofim6"},
                    {"account_id": "ACC-7", "customer_id": "CUS-7", "login_handle": "lenao7"}]
        onboarding = [{"account_id": "ACC-1", "customer_id": "CUS-1", "kyc_applicant_id": "APP-1"},
                      {"account_id": "ACC-5", "customer_id": "CUS-5", "kyc_applicant_id": "APP-5"}]
        kyc = [
            {"check_id": "KYC-1", "applicant_id": "APP-1", "verified_name": "Amina Bello",
             "verified_dob": "1980-05-02", "document_country": "Nigeria", "result": "approved",
             "checked_at": "2024-01-01", "document_number": "A1"},
            # A later rejected check is not evidence.
            {"check_id": "KYC-1R", "applicant_id": "APP-1", "verified_name": "Someone Else",
             "verified_dob": "1950-01-01", "document_country": "France", "result": "rejected",
             "checked_at": "2025-01-01", "document_number": "A9"},
            {"check_id": "KYC-5", "applicant_id": "APP-5", "verified_name": "Ibrahim Lawal Musa",
             "verified_dob": "1975-01-01", "document_country": "Nigeria", "result": "approved",
             "checked_at": "2024-01-01", "document_number": "B5"},
        ]
        hits = [
            {"hit_id": "H-SAME", "entity_type": "Individual", "name": "Bello, Amina", "dobs": "1980; 1981",
             "countries": "Nigeria", "categories": "PEP"},
            {"hit_id": "H-ABROAD", "entity_type": "Individual", "name": "Bello, Amina", "dobs": "1980-05-02",
             "countries": "Canada", "categories": "PEP"},
            {"hit_id": "H-DEAD", "entity_type": "Individual", "name": "Bello, Amina", "countries": "Nigeria",
             "deceased_date": "2021-08-02", "categories": "PEP"},
            {"hit_id": "H-COMPANY", "entity_type": "Company", "name": "Amina Bello Ltd", "countries": "Nigeria",
             "categories": "Enforcement"},
            {"hit_id": "H-SEPT", "entity_type": "Individual", "name": "Bello, Amina", "dobs": "1980-09",
             "countries": "Nigeria", "categories": "PEP"},
            {"hit_id": "H-NAME", "entity_type": "Individual", "name": "Musa, Tunde", "countries": "Nigeria",
             "categories": "PEP"},
            {"hit_id": "H-YOUNG", "entity_type": "Individual", "name": "Mensah, Kofi", "countries": "Ghana",
             "categories": "Enforcement", "primary_category": "Enforcement",
             "profile_notes": "According to gbc.example; March 10, 2004: Kofi Mensah was arrested."},
            {"hit_id": "H-SEARCH", "entity_type": "Individual", "name": "Mensah, Kofi", "countries": "Ghana",
             "categories": "Enforcement", "primary_category": "Enforcement"},
            {"hit_id": "H-ACME", "entity_type": "Company", "name": "ACME Trading GmbH", "countries": "Germany",
             "categories": "Sanctions"},
            {"hit_id": "H-NODATA", "entity_type": "Individual", "name": "Mensah, Kofi", "countries": "Ghana",
             "categories": "PEP"},
            {"hit_id": "H-NORECORD", "entity_type": "Individual", "name": "Nobody Known", "countries": ["Peru"],
             "categories": ["PEP"]},
            {"hit_id": "H-OSTROM", "entity_type": "Individual", "name": "Ostrom, Lena", "countries": ["Sweden"],
             "categories": ["PEP"]},
            # the dobs field holds something that is not a date; the hit is in another country
            {"hit_id": "H-BADDOB", "entity_type": "Individual", "name": "Bello, Amina", "dobs": ["0000-00-00"],
             "countries": ["Canada"], "categories": ["PEP"]},
            # no alert lists this hit, but it names its alert itself
            {"hit_id": "H-UNLISTED", "alert_id": "A-5", "entity_type": "Individual", "name": "Musa, Tunde",
             "countries": ["Nigeria"], "categories": ["PEP"]},
            {"hit_id": "H-PLACE", "entity_type": "Individual", "name": "Mensah, Kofi",
             "addresses": ["12 Harbour Road, Portville"], "categories": ["PEP"]},
        ]
        search = [
            {"result_id": "SR-NAMESAKE", "query": "kofi mensah", "title": "Kofi Mensah - news",
             "snippet": "Kofi Mensah, 61, the subject of enforcement action in Norway, appeared in a May 2020 notice."},
            {"result_id": "SR-OTHERJOB", "query": "kofi mensah", "title": "Kofi Mensah - news",
             "snippet": "Kofi Mensah, 70, a taxi driver in Ghana, won a community award in May 2020."},
            {"result_id": "SR-HIT", "query": "kofi mensah", "title": "Kofi Mensah - news",
             "snippet": "Kofi Mensah, 30, the subject of enforcement action in Ghana, appeared in a May 2020 notice."},
        ]
        alerts = [
            {"alert_id": "A-1", "screened_at": "2026-11-01", "customer_name": "Amina Bello",
             "entity_type": "Individual", "account_ref": "ACC-1",
             "hit_ids": "H-SAME; H-ABROAD; H-DEAD; H-COMPANY; H-BADDOB"},
            {"alert_id": "A-2", "screened_at": "2026-11-01", "customer_name": "Amina Bello",
             "entity_type": "Individual", "dob": "1980", "country": "Nigeria", "hit_ids": "H-SEPT; H-ABROAD"},
            {"alert_id": "A-5", "screened_at": "2026-11-01", "customer_name": "Tunde Musa",
             "entity_type": "Individual", "login_handle": "tundem5", "hit_ids": "H-NAME"},
            {"alert_id": "A-6", "screened_at": "2026-11-01", "customer_name": "Kofi Mensah",
             "entity_type": "Individual", "email": "cus-6@x.example", "hit_ids": "H-YOUNG; H-SEARCH; H-PLACE"},
            {"alert_id": "A-7", "screened_at": "2026-11-01", "customer_name": "Lena Ostrom",
             "entity_type": "Individual", "login_handle": "lenao7", "hit_ids": ["H-OSTROM"]},
            # the shape the data room uses: customer nested, hit_ids a list, nulls for missing links
            {"alert_id": "A-4", "screened_at": "2026-11-01",
             "customer": {"name": "Acme Trading", "entity_type": "Company", "dob": None, "country": None, "city": None},
             "account_ref": None, "login_handle": "acme4", "email": None, "hit_ids": ["H-ACME"]},
            {"alert_id": "A-0", "screened_at": "2026-11-01", "customer_name": "Nobody Known",
             "entity_type": "Individual", "dob": "1990-01-01", "country": "Ghana",
             "hit_ids": "H-NODATA; H-NORECORD"},
        ]
        write(d, "alerts", alerts)
        write(d, "hits", hits)
        write(d, "customers", customers)
        write(d, "accounts", accounts)
        write(d, "onboarding", onboarding)
        write(d, "kyc_checks", kyc)
        write(d, "notes", [])
        write(d, "search_results", search)
        cls.data = Data(d)
        cls.results = {}
        for a in cls.data.alerts:
            for r in decide_alert(cls.data, a):
                cls.results[(a["alert_id"], r["hit_id"])] = r

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def got(self, alert, hit):
        r = self.results[(alert, hit)]
        return r["decision"], r["reason_code"]

    def test_one_person_one_picture(self):
        self.assertEqual(self.data.person("CUS-1"), ("CUS-1", "CUS-2"))
        self.assertEqual(self.data.person("CUS-3"), ("CUS-3",))   # shared phone alone does not merge

    def test_confirm_on_name_dob_location(self):
        self.assertEqual(self.got("A-1", "H-SAME"), ("SAME_ENTITY", "NAME_DOB_LOCATION_MATCH"))
        self.assertEqual(self.results[("A-1", "H-SAME")]["evidence"], ["KYC-1", "CUS-1", "CUS-2"])

    def test_rejected_check_is_not_evidence(self):
        pic = self.results[("A-1", "H-SAME")]["trace"]["candidates"][0]["customer"]
        self.assertEqual(pic["verified_check"], "KYC-1")

    def test_country_difference_does_not_clear_when_name_and_dob_agree(self):
        self.assertEqual(self.got("A-1", "H-ABROAD"), ("REVIEW", "INSUFFICIENT_DATA"))

    def test_deceased(self):
        self.assertEqual(self.got("A-1", "H-DEAD"), ("DIFFERENT_ENTITY", "DECEASED"))

    def test_entity_type(self):
        self.assertEqual(self.got("A-1", "H-COMPANY"), ("DIFFERENT_ENTITY", "ENTITY_TYPE_MISMATCH"))

    def test_ambiguous_customer(self):
        # Two people fit A-2: born in May and in September 1980.
        r = self.results[("A-2", "H-SEPT")]
        self.assertEqual((r["decision"], r["reason_code"]), ("REVIEW", "AMBIGUOUS_CUSTOMER_RECORD"))
        self.assertEqual(r["customer_record_id"], "AMBIGUOUS")

    def test_ambiguous_but_same_decision(self):
        r = self.results[("A-2", "H-ABROAD")]
        # CUS-1/2 would be REVIEW (name and DOB agree, country differs); CUS-3 clears on DOB.
        self.assertEqual(r["customer_record_id"], "AMBIGUOUS")
        self.assertEqual(r["decision"], "REVIEW")

    def test_verified_name_clears(self):
        self.assertEqual(self.got("A-5", "H-NAME"), ("DIFFERENT_ENTITY", "NAME_MISMATCH_VERIFIED"))
        self.assertEqual(self.results[("A-5", "H-NAME")]["evidence"], ["KYC-5"])

    def test_too_young_at_earliest_report(self):
        self.assertEqual(self.got("A-6", "H-YOUNG"), ("DIFFERENT_ENTITY", "TEMPORAL_IMPOSSIBILITY"))

    def test_search_results_exclude_namesakes(self):
        r = self.results[("A-6", "H-SEARCH")]
        self.assertEqual(r["trace"]["hit"]["dob_sources"], ["SR-HIT"])
        self.assertEqual((r["decision"], r["reason_code"]), ("SAME_ENTITY", "NAME_DOB_LOCATION_MATCH"))
        self.assertIn("SR-HIT", r["evidence"])

    def test_company_confirmed_on_name_and_country(self):
        self.assertEqual(self.got("A-4", "H-ACME"), ("SAME_ENTITY", "NAME_LOCATION_MATCH"))

    def test_location_never_clears_without_a_customer_record(self):
        # Screened as Ghana, hit in Peru: with no record the customer's countries are not fully known.
        r = self.results[("A-0", "H-NORECORD")]
        self.assertIsNone(r["customer_record_id"])
        self.assertEqual(r["decision"], "REVIEW")

    def test_unrecognised_country_never_clears(self):
        # "Freedonia" is not a known country, so it may or may not be Sweden: no location clear.
        self.assertEqual(self.got("A-7", "H-OSTROM")[0], "REVIEW")
        # A hit address that ends in a town, not a country, leaves the hit's country unknown.
        self.assertEqual(self.got("A-6", "H-PLACE")[0], "REVIEW")

    def test_odd_spaces_and_unreadable_dates(self):
        from casebook.extract import listing_phrase
        from casebook.load import clean
        self.assertEqual(clean("Adverse\u00a0Media"), "Adverse Media")
        self.assertEqual(listing_phrase(clean("Adverse\u00a0Media")), "named in adverse media")
        self.assertEqual(listing_phrase("adverse-media"), "named in adverse media")
        self.assertEqual(listing_phrase("Registration"), "named on a watchlist")
        notes = clean("According\u00a0to gbc.example;\u00a0March 10, 2004: Kofi Mensah was arrested.")
        self.assertEqual([d for d, _ in dated_reports(notes)], [PDate(2004, 3, 10)])

    def test_unreadable_hit_date_blocks_a_country_clear(self):
        # The name agrees and the date might: a country difference alone must not clear.
        self.assertEqual(self.got("A-1", "H-BADDOB"), ("REVIEW", "INSUFFICIENT_DATA"))

    def test_hit_that_only_names_its_alert_is_still_decided(self):
        self.assertEqual(self.got("A-5", "H-UNLISTED"), ("DIFFERENT_ENTITY", "NAME_MISMATCH_VERIFIED"))

    def test_explain_prints_the_trail_for_one_hit(self):
        import subprocess
        explain = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "system", "explain")
        run = subprocess.run([sys.executable, explain, self.tmp.name, "H-SEARCH"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        for expected in ("SAME_ENTITY / NAME_DOB_LOCATION_MATCH", "How the customer was found", "SR-NAMESAKE  set aside",
                         "SR-HIT  about the hit", "Date of birth          AGREES"):
            self.assertIn(expected, run.stdout)
        # a review also says what would settle it; an unknown hit is one line, not a traceback
        run = subprocess.run([sys.executable, explain, self.tmp.name, "H-PLACE"], capture_output=True, text=True)
        self.assertIn("What would settle it", run.stdout)
        run = subprocess.run([sys.executable, explain, self.tmp.name, "H-NOPE"], capture_output=True, text=True)
        self.assertEqual((run.returncode, run.stdout), (1, ""))
        self.assertNotIn("Traceback", run.stderr)

    def test_no_record_found(self):
        r = self.results[("A-0", "H-NODATA")]
        self.assertIsNone(r["customer_record_id"])
        self.assertEqual(r["decision"], "REVIEW")


class EdgeCases(unittest.TestCase):
    """Situations the policy covers that the development set barely shows."""

    @classmethod
    def setUpClass(cls):
        def person(cid, name, dob, country_, phone, etype="Individual"):
            return {"record_id": cid, "entity_type": etype, "full_name": name, "dob": dob,
                    "nationality": country_, "residence_country": country_, "city": "Town",
                    "address": "1 Road, Town", "email": f"{cid.lower()}@x.example", "phone": phone}

        def check(kid, app, name, dob, country_, result="approved", at="2023-01-01", doc=None):
            return {"check_id": kid, "applicant_id": app, "verified_name": name, "verified_dob": dob,
                    "document_country": country_, "result": result, "checked_at": at,
                    "document_number": doc or kid}

        def hit(hid, name, etype="Individual", **more):
            row = {"hit_id": hid, "entity_type": etype, "name": name, "aliases": [], "dobs": [],
                   "countries": [], "categories": ["PEP"], "primary_category": "PEP"}
            row.update(more)
            return row

        def alert(aid, name, hit_ids, etype="Individual", dob=None, **link):
            row = {"alert_id": aid, "screened_at": "2026-11-01",
                   "customer": {"name": name, "entity_type": etype, "dob": dob, "country": None, "city": None},
                   "account_ref": None, "login_handle": None, "email": None, "hit_ids": hit_ids}
            row.update(link)
            return row

        customers = [
            # One person over three records: D1-D2 share a document number, D2-D3 a phone and birth date.
            person("CUS-D1", "Sara Lindqvist", "1982-03-04", "Sweden", "+46 1"),
            person("CUS-D2", "S. Lindqvist", "1982-03-04", "Norway", "+47 2"),
            person("CUS-D3", "Sara Lindqvist", "1982-03-04", "Denmark", "+47 2"),
            person("CUS-E1", "Marta Kowalska", "1990-07-07", "Poland", "+48 1"),
            person("CUS-F1", "Borealis Shipping AS", "2018-06-01", "Norway", "+47 9", etype="Company"),
            person("CUS-G1", "Daniel Okafor", "1980", "Nigeria", "+234 7"),          # no identity check
            person("CUS-J1", "Peter Novak", "1970-01-01", "Czechia", "+420 1"),      # joint holders
            person("CUS-J2", "Petra Novak", "1972-02-02", "Czechia", "+420 2"),
            person("CUS-K1", "Yusuf Demir", "1988-08-08", "Turkey", "+90 1"),
            # The record's birth date is wrong; the identity-check note has the verified one.
            person("CUS-N1", "Lars Holm", "1970-01-01", "Norway", "+47 31"),
            person("CUS-N2", "Erik Dahl", "1970-01-01", "Norway", "+47 32"),
            person("CUS-N3", "Ola Berg", "1980-02-02", "Norway", "+47 33"),
            person("CUS-N4", "Nora Lund", "1990-03-03", "Norway", "+47 34"),
            person("CUS-T1", "Timo Aalto", "2004-12-20", "Finland", "+358 1"),
        ]
        notes = [
            {"note_id": "NOTE-N1", "requester_email": "cus-n1@x.example", "created_at": "2023-02-19", "channel": "kyc",
             "text": "Sumsub completed identity verification for LARS HOLM, born March 5, 1985, using a "
                     "passport issued in Norway on 2023-02-19."},
            # Approved, but the birth date is not in the text.
            {"note_id": "NOTE-N2", "requester_email": "cus-n2@x.example", "created_at": "2023-02-19", "channel": "kyc",
             "text": "Sumsub completed identity verification for ERIK DAHL (date of birth as on the attached "
                     "scan) using a passport issued in Norway on 2023-02-19."},
            # A failed check is not evidence, but its document names a country.
            {"note_id": "NOTE-N3", "requester_email": "cus-n3@x.example", "created_at": "2023-02-19", "channel": "kyc",
             "text": "Veriff rejected the identity check for OLA BERG, born 2 February 1980, who presented a "
                     "passport issued in Chile on 2023-02-19."},
            # The customer's name is in ordinary case; the only words in capitals are a heading.
            {"note_id": "NOTE-N4", "requester_email": "cus-n4@x.example", "created_at": "2023-02-19", "channel": "kyc",
             "text": "ONFIDO REPORT: identity of Nora Lund confirmed, born 3 March 1990, passport issued in "
                     "Norway on 2023-02-19."},
        ]
        accounts = [{"account_id": "ACC-" + c["record_id"][4:], "customer_id": c["record_id"],
                     "login_handle": c["record_id"].lower().replace("-", "")}
                    for c in customers if not c["record_id"].startswith("CUS-J")]
        accounts += [{"account_id": "ACC-J", "customer_id": "CUS-J1", "login_handle": "novak1"},
                     {"account_id": "ACC-J", "customer_id": "CUS-J2", "login_handle": "novak2"}]
        onboarding = [{"account_id": a["account_id"], "customer_id": a["customer_id"],
                       "kyc_applicant_id": "APP-" + a["customer_id"][4:]} for a in accounts]
        checks = [
            check("KYC-D1", "APP-D1", "Sara Lindqvist", "1982-03-04", "Sweden", doc="DOC77"),
            check("KYC-D2", "APP-D2", "Sara Lindqvist", "1982-03-04", "Norway", doc="DOC77"),
            # The approved check gives no birth date; a newer expired check is not evidence.
            check("KYC-E1", "APP-E1", "Marta Anna Kowalska", None, "Poland", at="2022-01-01"),
            check("KYC-E1X", "APP-E1", "Other Person", "1960-01-01", "France", result="expired", at="2024-01-01"),
            check("KYC-F1", "APP-F1", "Borealis Shipping AS", "2018-06-01", "Norway"),
            check("KYC-J1", "APP-J1", "Peter Novak", "1970-01-01", "Czechia"),
            check("KYC-J2", "APP-J2", "Petra Novak", "1972-02-02", "Czechia"),
            check("KYC-K1", "APP-K1", "Yusuf Demir", "1988-08-08", "Turkey"),
        ]
        hits = [
            hit("H-D-DK", "Lindqvist, Sara", countries=["Denmark"]),
            hit("H-D-SAME", "Lindqvist, Sara", countries=["Denmark"], dobs=["1982-03-04"]),
            hit("H-E-SAME", "Kowalska, Marta", countries=["Poland"], dobs=["1990-07-07"]),
            hit("H-E-FAR", "Kowalska, Marta", countries=["Poland"], dobs=["1985"]),
            hit("H-F-TIME", "Borealis Shipping Ltd", "Company", countries=["Norway"],
                profile_notes="According to x.example; March 10, 2010: Borealis Shipping was fined."),
            hit("H-F-DOB", "Borealis Shipping", "Company", countries=["Norway"], dobs=["2001-01-01"]),
            hit("H-F-NOCOUNTRY", "Borealis Shipping Limited", "Company"),
            hit("H-F-FORM", "BOREALIS SHIPPING GMBH", "Company", countries=["Norway"]),
            hit("H-F-ABROAD", "Borealis Shipping", "Company", countries=["Panama"]),
            hit("H-F-PERSON", "Borealis, Shipping", "Individual", countries=["Norway"]),
            hit("H-F-NAME", "Borealis Trading", "Company", countries=["Norway"]),
            hit("H-G-FULL", "Okafor, Daniel", countries=["Nigeria"], dobs=["1980-05-02"]),
            hit("H-G-NEXT", "Okafor, Daniel", countries=["Nigeria"], dobs=["1981"]),
            hit("H-G-FAR", "Okafor, Daniel", countries=["Nigeria"], dobs=["1982"]),
            hit("H-G-NAME", "Okafor, Samuel", countries=["Nigeria"]),
            hit("H-J", "Novak, Peter", countries=["Czechia"], dobs=["1970-01-01"]),
            hit("H-J-ABROAD", "Novak, Peter", countries=["Brazil"]),
            hit("H-K-NOTYPE", "Demir, Yusuf", None, countries=["Turkey"], dobs=["1988-08-08"]),
            hit("H-K-VESSEL", "Yusuf Demir", "Vessel", countries=["Turkey"]),
            hit("H-K-EMPTY", "", countries=["Turkey"]),
            hit("H-K-NONE", None, countries=["Turkey"], dobs=["1988-08-08"]),
            hit("H-K-CYR", "Юсуф Демир", countries=["Turkey"]),
            hit("H-K-CYR-DOB", "Юсуф Демир", countries=["Turkey"], dobs=["1988-08-08"]),
            hit("H-K-MIXED", "Юсуф Демир", countries=["Turkey"], dobs=["1988-08-08"],
                aliases=["Yusuf Demir (AKA)"]),
            hit("H-K-INITIALS", "Y. D.", countries=["Turkey"]),
            hit("H-K-PLACEHOLDER", "Unknown N/A", countries=["Turkey"]),
            hit("H-K-FUTURE", "Demir, Yusuf", countries=["Turkey"], deceased_date="2030-01-01"),
            hit("H-K-DEADYEAR", "Demir, Yusuf", countries=["Turkey"], deceased_date="2026"),
            hit("H-K-NUMERIC", "Demir, Yusuf", countries=["Turkey"],
                profile_notes="Name: Demir, Yusuf\nDOB: 3/25/1988\nSex: M"),
            hit("H-K-NUMERIC-AMBIGUOUS", "Demir, Yusuf", countries=["Turkey"],
                profile_notes="Name: Demir, Yusuf\nDOB: 3/5/1988\nSex: M"),
            hit("H-N1", "Holm, Lars", countries=["Norway"], dobs=["1985-03-05"]),
            hit("H-N2", "Dahl, Erik", countries=["Norway"], dobs=["1985-03-05"]),
            hit("H-N3", "Berg, Ola", countries=["Chile"]),
            hit("H-N3-PERU", "Berg, Ola", countries=["Peru"]),
            hit("H-N4", "Lund, Nora", countries=["Norway"], dobs=["1990-03-03"]),
            # Reports dated when the customer, born in December 2004, was 15: once in the year he
            # turned 16, once the year before.
            hit("H-T-16", "Aalto, Timo", countries=["Finland"],
                profile_notes="According to x.example; March 1, 2020: Timo Aalto was fined."),
            hit("H-T-15", "Aalto, Timo", countries=["Finland"],
                profile_notes="According to x.example; March 1, 2019: Timo Aalto was fined."),
        ]
        alerts = [
            alert("A-D", "Sara Lindqvist", ["H-D-DK", "H-D-SAME"], account_ref="ACC-D1"),
            alert("A-E", "Marta Kowalska", ["H-E-SAME", "H-E-FAR"], login_handle="cuse1"),
            alert("A-F", "Borealis Shipping", [h["hit_id"] for h in hits if h["hit_id"].startswith("H-F")],
                  etype="Company", login_handle="cusf1"),
            alert("A-G", "Daniel Okafor", ["H-G-FULL", "H-G-NEXT", "H-G-FAR", "H-G-NAME"], login_handle="cusg1"),
            alert("A-J-NEITHER", "Pavel Dvorak", ["H-J", "H-J-ABROAD"], dob="1999-09-09", account_ref="ACC-J"),
            alert("A-J-BOTH", "P Novak", ["H-J"], account_ref="ACC-J"),
            alert("A-J-ONE", "Petra Novak", ["H-J"], dob="1972-02-02", account_ref="ACC-J"),
            alert("A-K", "Yusuf Demir", [h["hit_id"] for h in hits if h["hit_id"].startswith("H-K")],
                  login_handle="cusk1"),
            alert("A-BADLINK", "Yusuf Demir", ["H-K-CYR"], account_ref="ACC-NOPE"),
            alert("A-N1", "Lars Holm", ["H-N1"], login_handle="cusn1"),
            alert("A-N2", "Erik Dahl", ["H-N2"], login_handle="cusn2"),
            alert("A-N3", "Ola Berg", ["H-N3", "H-N3-PERU"], login_handle="cusn3"),
            alert("A-N4", "Nora Lund", ["H-N4"], login_handle="cusn4"),
            alert("A-T", "Timo Aalto", ["H-T-16", "H-T-15"], login_handle="cust1"),
            alert("A-NOHITS", "Yusuf Demir", [], login_handle="cusk1"),
            alert("A-GHOST", "Yusuf Demir", ["H-DOES-NOT-EXIST"], login_handle="cusk1"),
        ]
        cls.tmp = tempfile.TemporaryDirectory()
        for name, rows in [("customers", customers), ("accounts", accounts), ("onboarding", onboarding),
                           ("kyc_checks", checks), ("hits", hits), ("alerts", alerts), ("notes", notes),
                           ("search_results", [])]:
            write(cls.tmp.name, name, rows)
        cls.data = Data(cls.tmp.name)
        cls.results = {}
        for a in cls.data.alerts:
            for r in decide_alert(cls.data, a):
                cls.results[(a["alert_id"], r["hit_id"])] = r

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def got(self, alert, hit):
        r = self.results[(alert, hit)]
        return r["decision"], r["reason_code"]

    def test_records_chained_by_document_number_and_phone_are_one_person(self):
        self.assertEqual(self.data.person("CUS-D1"), ("CUS-D1", "CUS-D2", "CUS-D3"))
        # Denmark is only on the third record: without the chain this would clear on location.
        self.assertEqual(self.got("A-D", "H-D-DK"), ("REVIEW", "INSUFFICIENT_DATA"))
        r = self.results[("A-D", "H-D-SAME")]
        self.assertEqual((r["decision"], r["customer_record_id"]), ("SAME_ENTITY", "CUS-D1"))
        self.assertIn("CUS-D3", r["evidence"])

    def test_approved_check_without_a_birth_date_and_a_newer_expired_check(self):
        pic = self.results[("A-E", "H-E-SAME")]["trace"]["candidates"][0]["customer"]
        self.assertEqual((pic["verified_check"], pic["verified_name"]), ("KYC-E1", "Marta Anna Kowalska"))
        self.assertEqual(pic["dob"], "1990-07-07")      # from the record, since the check gives none
        self.assertEqual(self.got("A-E", "H-E-SAME")[0], "SAME_ENTITY")
        self.assertEqual(self.got("A-E", "H-E-FAR"), ("DIFFERENT_ENTITY", "DOB_MISMATCH"))

    def test_company(self):
        self.assertEqual(self.got("A-F", "H-F-TIME"), ("DIFFERENT_ENTITY", "TEMPORAL_IMPOSSIBILITY"))
        self.assertEqual(self.got("A-F", "H-F-DOB"), ("DIFFERENT_ENTITY", "DOB_MISMATCH"))
        self.assertEqual(self.got("A-F", "H-F-NOCOUNTRY"), ("REVIEW", "INSUFFICIENT_DATA"))
        self.assertEqual(self.got("A-F", "H-F-FORM"), ("SAME_ENTITY", "NAME_LOCATION_MATCH"))
        self.assertEqual(self.got("A-F", "H-F-ABROAD"), ("DIFFERENT_ENTITY", "LOCATION_MISMATCH"))
        self.assertEqual(self.got("A-F", "H-F-NAME"), ("DIFFERENT_ENTITY", "NAME_MISMATCH_VERIFIED"))
        r = self.results[("A-F", "H-F-PERSON")]
        self.assertEqual((r["decision"], r["reason_code"]), ("DIFFERENT_ENTITY", "ENTITY_TYPE_MISMATCH"))
        self.assertEqual(r["evidence"], ["CUS-F1"])

    def test_partial_dates_and_declared_names(self):
        self.assertEqual(self.got("A-G", "H-G-FULL")[0], "SAME_ENTITY")           # a year agrees with a full date
        self.assertEqual(self.got("A-G", "H-G-NEXT"), ("REVIEW", "INSUFFICIENT_DATA"))   # one year apart
        self.assertEqual(self.got("A-G", "H-G-FAR"), ("DIFFERENT_ENTITY", "DOB_MISMATCH"))
        self.assertEqual(self.got("A-G", "H-G-NAME"), ("REVIEW", "INSUFFICIENT_DATA"))   # declared name never clears

    def test_joint_holders(self):
        # The screened details fit neither or both: each is decided, and they disagree.
        for a in ("A-J-NEITHER", "A-J-BOTH"):
            r = self.results[(a, "H-J")]
            self.assertEqual((r["decision"], r["reason_code"], r["customer_record_id"]),
                             ("REVIEW", "AMBIGUOUS_CUSTOMER_RECORD", "AMBIGUOUS"))
        # Both holders clear the hit abroad, so the shared decision stands.
        r = self.results[("A-J-NEITHER", "H-J-ABROAD")]
        self.assertEqual((r["decision"], r["customer_record_id"]), ("DIFFERENT_ENTITY", "AMBIGUOUS"))
        r = self.results[("A-J-ONE", "H-J")]
        self.assertEqual((r["decision"], r["customer_record_id"]), ("DIFFERENT_ENTITY", "CUS-J2"))

    def test_names_that_cannot_be_compared_never_clear(self):
        for h in ("H-K-EMPTY", "H-K-NONE", "H-K-INITIALS", "H-K-PLACEHOLDER", "H-K-CYR", "H-K-CYR-DOB"):
            self.assertEqual(self.got("A-K", h), ("REVIEW", "INSUFFICIENT_DATA"), h)
        # A Latin alias beside the Cyrillic name can be compared.
        self.assertEqual(self.got("A-K", "H-K-MIXED")[0], "SAME_ENTITY")
        self.assertEqual(self.got("A-BADLINK", "H-K-CYR")[0], "REVIEW")   # a dead link falls back to search

    def test_entity_type_that_is_neither_person_nor_company(self):
        self.assertEqual(self.got("A-K", "H-K-NOTYPE")[0], "SAME_ENTITY")
        self.assertEqual(self.got("A-K", "H-K-VESSEL"), ("REVIEW", "INSUFFICIENT_DATA"))

    def test_date_of_death_must_be_before_screening(self):
        self.assertEqual(self.got("A-K", "H-K-FUTURE")[0], "REVIEW")
        self.assertEqual(self.got("A-K", "H-K-DEADYEAR")[0], "REVIEW")   # 2026 may be after 2026-11-01

    def test_numeric_date_read_in_full_only_when_unambiguous(self):
        # 3/25/1988 can only be 25 March: same year, other month and day, so a contradiction.
        self.assertEqual(self.got("A-K", "H-K-NUMERIC"), ("DIFFERENT_ENTITY", "DOB_MISMATCH"))
        # 3/5/1988 could be March or May: only the year is used, and it agrees.
        self.assertEqual(self.got("A-K", "H-K-NUMERIC-AMBIGUOUS")[0], "SAME_ENTITY")

    def test_identity_check_notes_that_cannot_be_relied_on(self):
        # Verified beats declared: the note's 1985 date is used, not the record's 1970.
        r = self.results[("A-N1", "H-N1")]
        self.assertEqual((r["decision"], r["evidence"][0]), ("SAME_ENTITY", "NOTE-N1"))
        # The approved note's birth date could not be read, so the record's date must not clear.
        r = self.results[("A-N2", "H-N2")]
        self.assertEqual((r["decision"], r["reason_code"]), ("REVIEW", "INSUFFICIENT_DATA"))
        self.assertIn("NOTE-N2", r["reason"])
        # A failed check is not evidence, but the country on its document stops a location clear.
        self.assertEqual(self.got("A-N3", "H-N3"), ("REVIEW", "INSUFFICIENT_DATA"))
        self.assertEqual(self.got("A-N3", "H-N3-PERU"), ("DIFFERENT_ENTITY", "LOCATION_MISMATCH"))
        # A heading in capitals was read as the verified name. It shares nothing with the name
        # on record, so it must not clear the hit as a name mismatch.
        r = self.results[("A-N4", "H-N4")]
        self.assertNotEqual(r["decision"], "DIFFERENT_ENTITY")

    def test_age_at_the_report_is_counted_on_years(self):
        # Born 2004, report in 2020: he could have been 16, so the hit is not cleared as too young,
        # although his birthday that year had not come. The practice labels count the same way.
        self.assertEqual(self.got("A-T", "H-T-16"), ("REVIEW", "INSUFFICIENT_DATA"))
        # Report in 2019: at most 15 whatever the birthday.
        r = self.results[("A-T", "H-T-15")]
        self.assertEqual((r["decision"], r["reason_code"]), ("DIFFERENT_ENTITY", "TEMPORAL_IMPOSSIBILITY"))
        self.assertIn("at most 15", r["reason"])

    def test_alert_without_hits_and_hit_that_does_not_exist(self):
        self.assertFalse([k for k in self.results if k[0] == "A-NOHITS"])
        self.assertEqual(self.got("A-GHOST", "H-DOES-NOT-EXIST"), ("REVIEW", "INSUFFICIENT_DATA"))


def reason_problems(result, known_ids=None):
    """What is wrong with one written reason; an empty list when it is well formed."""
    import re
    reason, out = result["reason"], []
    bare = re.sub(r"\s*\([^)]*\)", "", re.sub(r'"[^"]*"', "Q", reason))   # names and record IDs set aside
    n = len(re.findall(r"[.!?](?=\s|$)", bare))
    if not 2 <= n <= 4:
        out.append(f"{n} sentences")
    if re.search(r"\.\.|,,|  | [.,;:]|\(\)|;;|\bNone\b|\{|\}", bare):
        out.append("odd punctuation or an unfilled value")
    if known_ids is not None:
        for rid in re.findall(r"\b(?:CUS|KYC|NOTE|SR)-[0-9A-Z]+\b", reason):
            if rid not in known_ids:
                out.append(f"{rid} is not one of this hit's records")
    return out


def ids_of(result):
    """Every record a hit's decision could rightly name: the customer's and the hit's."""
    ids = set(result["evidence"])
    trace = result.get("trace") or {}
    for s_ in (trace.get("hit") or {}).get("search_considered", []):
        ids.add(s_["result_id"])
    for c in trace.get("candidates", []):
        cu = c["customer"]
        ids.update(cu["records"])
        ids.update(x for x in (cu.get("verified_check"), cu.get("dob_source")) if x)
        ids.update(f["source"] for f in cu["countries"])
        for check in c["checks"]:
            ids.update(check["sources"])
            ids.update(__import__("re").findall(r"\b(?:CUS|KYC|NOTE|SR)-[0-9A-Z]+\b", check["detail"]))
    return ids


class Reasons(unittest.TestCase):
    """A reason must be readable on its own: two to four sentences, this hit's facts, real record IDs."""

    def test_invented_folders(self):
        for case in (Policy, EdgeCases):
            case.setUpClass()
            try:
                for key, r in case.results.items():
                    self.assertEqual(reason_problems(r, ids_of(r) if r.get("trace") else None), [], (key, r["reason"]))
            finally:
                case.tearDownClass()

    def test_a_reason_says_why(self):
        Policy.setUpClass()
        try:
            got = {k: v["reason"] for k, v in Policy.results.items()}
        finally:
            Policy.tearDownClass()
        # a name clear says which part matched and what a match needs
        self.assertIn("only Musa matches", got[("A-5", "H-NAME")])
        self.assertIn("two matching parts", got[("A-5", "H-NAME")])
        # a review says what would settle it
        self.assertIn("would settle it", got[("A-6", "H-PLACE")])
        # a country review says which tie would confirm it
        self.assertIn("a tie between the customer and Canada would confirm it", got[("A-1", "H-ABROAD")])
        # several customers: each one's outcome in words
        self.assertIn("would be cleared", got[("A-2", "H-SEPT")])

    def test_development_set(self):
        if not os.path.isdir(Golden.FOLDER):
            self.skipTest("dev data not present")
        from casebook.engine import decide_all
        for r in decide_all(Data(Golden.FOLDER)):
            self.assertEqual(reason_problems(r, ids_of(r) if r.get("trace") else None), [], (r["hit_id"], r["reason"]))


class PracticeLabels(unittest.TestCase):
    """Everything the organisers check on a right decision, against the 50 answered hits:
    the decision, an accepted reason code, an accepted customer record, the exact evidence,
    and every fact the reference reason states."""

    def test_every_label_matches_and_every_reference_fact_is_stated(self):
        import re
        folder = Golden.FOLDER
        labels_path = os.path.join(folder, "practice_labels.jsonl")
        if not os.path.isfile(labels_path):
            self.skipTest("dev data not present")
        from casebook.engine import decide_all
        ours = {r["hit_id"]: r for r in decide_all(Data(folder))}
        fact = re.compile(r"\b(?:CUS|KYC|NOTE|SR)-[0-9A-F]{6}\b|\b\d{4}(?:-\d\d){0,2}\b|\b\d{1,3}\b")
        with open(labels_path, encoding="utf-8") as f:
            labels = [json.loads(line) for line in f if line.strip()]
        self.assertGreater(len(labels), 0)
        for label in labels:
            r, where = ours[label["hit_id"]], label["hit_id"]
            self.assertEqual(r["decision"], label["decision"], where)
            self.assertIn(r["reason_code"], label["reason_codes"], where)
            self.assertIn(r["customer_record_id"], label["customer_record_ids"], where)
            self.assertEqual(sorted(r["evidence"]), sorted(label["evidence"]), where)
            reference = label["reference_reason"]
            missing = set(fact.findall(reference)) - set(fact.findall(r["reason"]))
            self.assertEqual(missing, set(), (where, reference, r["reason"]))
            for name in re.findall(r"hit's name (.+?)(?:\.(?:\s|$)|$)", reference):
                self.assertIn(name.strip(), r["reason"], where)
            for countries in re.findall(r"linked (?:only )?to ([A-Z][^.]*?)(?: but|\.|$)", reference):
                for country_ in countries.split(","):
                    self.assertIn(country_.strip(), r["reason"], where)


class Golden(unittest.TestCase):
    """The scored output must not move unless the policy code is changed on purpose.

    ML, LLM and app work must leave decisions byte-identical. Skipped when the
    dev export is not on this machine.
    """
    _DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    FOLDER = os.environ.get("CASEBOOK_DATA") or next(
        (p for p in (os.path.join(_DATA, "screening", "dev"), os.path.join(_DATA, "dev_from_workbook"))
         if os.path.isdir(p)), os.path.join(_DATA, "screening", "dev"))
    SHA256 = "b35940707fbce104cb171bf5ccaf685b92b4581234ea5bd00e83a75955e419d8"

    def test_decisions_unchanged(self):
        if not os.path.isdir(self.FOLDER):
            self.skipTest("dev data not present")
        import hashlib
        from casebook.engine import decide_all
        from casebook.export import DECISION_FIELDS, write_decisions
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "decisions.jsonl")
            write_decisions(decide_all(Data(self.FOLDER)), path, DECISION_FIELDS)
            with open(path, "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), self.SHA256)


if __name__ == "__main__":
    unittest.main()
