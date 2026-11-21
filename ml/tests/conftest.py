"""An invented data folder, big enough for every model to train for real."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

FIRST = ["Amina", "Tunde", "Kofi", "Marta", "Lukas", "Sofia", "Ibrahim", "Elena", "Carlos", "Nils", "Grace", "Omar"]
LAST = ["Bello", "Musa", "Mensah", "Novak", "Weber", "Conti", "Lawal", "Garcia", "Hughes", "Lindqvist", "Okafor",
        "Hussein"]
MIDDLE = ["Rose", "Ade", "Yaw", "Jan", "Paul", "Lucia"]
COUNTRIES = ["Nigeria", "Ghana", "Germany", "Italy", "Spain", "Sweden"]
# what each alert's hit is, relative to the customer
KINDS = ["same", "other_dob", "same", "other_dob", "no_dob", "same", "other_dob", "no_dob", "abroad", "other_name"]


def write_jsonl(folder, name, rows):
    with open(os.path.join(folder, name + ".jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def make_folder(folder, n=80):
    customers, accounts, onboarding, kyc, alerts, hits, notes, search = [], [], [], [], [], [], [], []
    for i in range(n):
        first, last = FIRST[i % 12], LAST[(i * 5 + i // 12) % 12]
        name = f"{first} {last}"
        year = 1955 + (i * 7) % 45
        dob = f"{year}-{1 + i % 12:02d}-{1 + i % 27:02d}"
        country = COUNTRIES[i % 6]
        cid, acc, app, aid, hid = f"CUS-{i:03d}", f"ACC-{i:03d}", f"APP-{i:03d}", f"A-{i:03d}", f"H-{i:03d}"
        email = f"c{i}@x.example"
        customers.append({"record_id": cid, "entity_type": "Individual", "full_name": name, "dob": dob,
                          "nationality": country, "residence_country": country, "city": "Town",
                          "address": "1 Road, Town", "email": email, "phone": f"+1 {1000 + i}"})
        accounts.append({"account_id": acc, "customer_id": cid, "login_handle": f"user{i}"})
        onboarding.append({"account_id": acc, "customer_id": cid, "kyc_applicant_id": app})
        kind = KINDS[i % len(KINDS)]
        verified = f"{first} {MIDDLE[i % 6]} {last}" if i % 3 == 0 else name
        if kind == "other_name":
            verified = f"{FIRST[(i + 5) % 12]} {MIDDLE[i % 6]} {last}"
        kyc.append({"check_id": f"KYC-{i:03d}", "applicant_id": app, "verified_name": verified, "verified_dob": dob,
                    "document_country": country, "result": "approved", "checked_at": "2024-01-01",
                    "document_number": f"D{i:05d}"})
        # a soft signal for the ranker to find: true matches score higher than namesakes
        score = {"same": 98 + i % 3, "other_dob": 90 + i % 4}.get(kind, 90 + i % 11)
        hit = {"hit_id": hid, "alert_id": aid, "entity_type": "Individual", "name": f"{last}, {first}",
               "countries": country, "categories": ["PEP", "Sanctions", "Enforcement"][i % 3],
               "match_score": str(score), "aliases": f"{first} {last[:-1]}{last[-1]}a (AKA)" if i % 4 == 0 else None}
        if kind == "same":
            hit["dobs"] = str(year)
        elif kind == "other_dob":
            hit["dobs"] = str(year - 12)
        elif kind == "abroad":
            hit["countries"] = "Japan"
        hits.append(hit)
        alerts.append({"alert_id": aid, "screened_at": "2026-11-01", "customer_name": name,
                       "entity_type": "Individual", "account_ref": acc, "hit_ids": hid})
        if i < 8:
            notes.append({"note_id": f"NOTE-K{i}", "requester_email": email, "created_at": "2024-02-02", "channel": "kyc",
                          "text": f"Persona verified {name.upper()} (DOB {dob}) on 2024-02-02 via a {country} passport."})
            notes.append({"note_id": f"NOTE-R{i}", "requester_email": email, "created_at": "2024-03-03", "channel": "phone",
                          "text": f"The customer lived and worked in {COUNTRIES[(i + 1) % 6]} from 20{10 + i} to 20{15 + i}."})
            notes.append({"note_id": f"NOTE-O{i}", "requester_email": email, "created_at": "2024-04-04", "channel": "app",
                          "text": ["Customer asked about card limits number %d.", "Statement number %d was sent.",
                                   "A new card was ordered, request %d.", "Password reset %d completed."][i % 4] % i})
            search.append({"result_id": f"SR-L{i}", "query": f"{first} {last}".lower(), "title": f"{name} - news",
                           "snippet": f"{name}, {30 + i}, a politically exposed person in {country}, appeared in a "
                                      f"May 2020 notice."})
            search.append({"result_id": f"SR-B{i}", "query": f"{first} {last}".lower(), "title": f"{name} - news",
                           "snippet": f"{name}, {40 + i}, a taxi driver in {country}, won a community award in June 2019."})
    for name, rows in (("alerts", alerts), ("hits", hits), ("customers", customers), ("accounts", accounts),
                       ("onboarding", onboarding), ("kyc_checks", kyc), ("notes", notes), ("search_results", search)):
        write_jsonl(folder, name, rows)
    return folder


@pytest.fixture(scope="session")
def folder(tmp_path_factory):
    return make_folder(str(tmp_path_factory.mktemp("data")))


@pytest.fixture(scope="session")
def built(folder, tmp_path_factory):
    """One run of the command line: (results by hit, scores, metrics, routing)."""
    from casebook.engine import decide_all
    from casebook.load import Data
    from ml.train import main

    out = str(tmp_path_factory.mktemp("out"))
    assert main([folder, "--out", out]) == 0
    load = lambda name: json.load(open(os.path.join(out, name), encoding="utf-8"))
    results = {r["hit_id"]: r for r in decide_all(Data(folder))}
    return results, load("scores.json"), load("metrics.json"), load("routing.json")
