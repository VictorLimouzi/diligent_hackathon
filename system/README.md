# Casebook: is the hit the customer?

Casebook applies Diligent's screening SOP as code. For each watchlist hit it works out who the customer is, gathers the hit's facts, compares the two, and writes `DIFFERENT_ENTITY`, `SAME_ENTITY` or `REVIEW` with a reason and the record IDs it relied on.

```sh
system/run <data folder> <output file>
```

It reads only the given folder and uses the Python 3 standard library (3.8 or later). It calls no language model and no network.

## Architecture

```
data folder                          system/casebook/
───────────                          ────────────────
alerts, accounts,        load.py     index every file; group customer records into persons
customers, onboarding ─────────────▶ (same phone + full date of birth, or same document
kyc_checks, notes                    number on approved checks, transitively)
                                          │
alert ──────────────▶ customer.py    1. resolve: account_ref / login_handle / email,
                                        else search by name, date of birth, country, city
                                     2. picture, one per person: latest approved identity
                                        check (provider record or onboarding note),
                                        date of birth, every tied country
                                          │
hit, search_results ─▶ hit.py        names and aliases; birth dates from the dobs field,
                                     else the first paragraph of the profile notes, else
                                     search results about the hit; countries; earliest
                                     dated report; date of death
                                          │
                       policy.py     six checks → clear only on a contradiction,
                                     confirm only on agreement, otherwise REVIEW
                                          │
                       engine.py     one decision per person; several people →
                                     the shared decision, or AMBIGUOUS_CUSTOMER_RECORD
                                          │
                                     output file: one JSON line per hit
```

`extract.py` reads facts out of free text, `names.py` implements principle 8, `countries.py` maps names, codes and nationalities to one country, `dates.py` handles partial dates.

## How it finds the customer

1. **Follow the link the alert carries.** `account_ref` → accounts → customers; `login_handle` → accounts → customers; `email` → customers.
2. **A link to several people** (joint account holders) is narrowed by the screened name and date of birth.
3. **No link:** find records whose full or preferred name agrees with the screened name, whose date of birth equals the screened one in every part both give, whose residence or nationality is the screened country, and whose city or address contains the screened city.
4. **One person, one picture.** Records are grouped into persons before anything is compared, so all of a person's records, checks and notes count. A shared phone or address alone never groups records.
5. **Verified beats declared.** The person's most recent approved identity check gives the legal name, date of birth and document country. It can be a KYC provider record or an onboarding note; rejected and expired checks are ignored. Without one, the record's own details are used, and a declared name can never clear a hit.
6. **Several people fit:** the hit is decided for each. One shared decision stands; different decisions give `REVIEW` / `AMBIGUOUS_CUSTOMER_RECORD`. Either way `customer_record_id` is `AMBIGUOUS`.

Only the resolved person's records are ever read.

## How it finds the listed person

- **Names:** the hit's name and every alias, compared part by part: case, accents, punctuation and brackets ignored; listed variants; parts written together or apart; one edit for parts of 4 to 6 letters, two for longer ones. Placeholders, initials and company legal forms are not parts.
- **Birth dates,** in the order the SOP gives: the `dobs` field; else a sentence in the first paragraph of the profile notes (a birth date, or an age at a date, which gives two birth years); else search results for the hit's name. A search result counts only if it uses the listing phrase for the hit's category and names a country tied to the hit. The rest are namesakes and are dropped.
- **Countries:** `countries`, `nationality`, `citizenship`, the last part of each address and the place of birth. "International" and "Europe" are not countries.
- **Dated reports:** only lines of the form `According to …; September 10, 2025: …`.

Text that cannot be read with confidence yields no fact. A missing fact can only send a hit to `REVIEW`; it can never clear one.

## How evidence is represented

Every fact carries the ID of the record it came from (`CUS-`, `KYC-`, `NOTE-`, `SR-`). Each of the six checks (type, name, date of birth, country, age at the earliest report, date of death) returns an outcome (agree, contradict, cannot compare), one sentence, and its source IDs. The decision cites the sources of the checks that decided it:

- a clear cites the sources of each contradiction;
- a confirmation cites the date-of-birth source, the records that share a country with the hit, and any search result used for the hit's birth date;
- a review cites the records behind whatever did agree.

`reason` is those checks' sentences plus the rule applied, two to four sentences. When several reason codes apply, the one resting on the most reliable source is given first: type, date of birth, verified name, time, death, then country.

## Same result every run

The system is a pure function of the data folder: no model, no network, no clock, no randomness, and every loop runs in file order. Two runs on the same folder give byte-identical output, and the output is identical on Python 3.9, 3.11, 3.12 and 3.13.

## When it cannot tell, it does not clear

Clearing a real match costs twenty times an unnecessary review, so every uncertainty resolves toward `REVIEW`:

- A note or snippet that cannot be read gives no fact. A birth date read from a note is dropped if it falls within ten years of the check date, and an age outside 16 to 100 is not used: both are more likely a misread than a fact.
- A country value that is not recognised (on either side) makes location unknown, so it cannot clear. A country a support note says the customer lived or worked in is tied to them. A country a note only mentions (a trip, a transfer) is not a tie, but it stops a location clear and stops a confirmation, because it might be one.
- A `dobs` value that is not a date counts as a date that might agree: it blocks a date-of-birth clear, and a country clear when the name agrees.
- A declared name never clears; only a verified legal name does. A note that reads as a failed, pending or expired check is not evidence.
- A hit whose names are all in another script (Cyrillic or Arabic only, say) cannot be compared with the verified name, so it cannot be cleared on name. An entity type that is neither a person nor a company (a vessel) is not a type mismatch.
- A numeric date in watchlist text is read in full only when it can be read one way: `6/20/1955` is 20 June, but `3/5/1988` gives its year only.
- If no customer record is found, location cannot clear. If an alert cannot be processed, or the folder cannot be read, the affected hits are written as `REVIEW`.
- Text is cleaned on load (non-breaking and zero-width spaces, accent forms), so typography cannot change which search result is taken as the hit's.

We tested this in four ways.

- **A second reader.** At development time a language model was given the policy and each of the 819 cases and asked for its own decision. It disagreed with the system on 23. We read every one. In 16 the system cleared and the model wanted a review. Twelve clear on country and four on the verified name; all apply the policy as written, and the practice labels decide the same way in the same situations (a matching name with no date to compare and no shared country is labelled `DIFFERENT_ENTITY`). One disagreement was a real fault: a profile giving `DOB: 6/20/1955` was read as the year 1955 only, so a customer born in November 1955 was confirmed. It is now cleared on date of birth.
- **Rare situations.** The tests include an invented data folder with the cases the development set barely shows: records chained into one person by document number and phone, an approved check with no birth date, a newer expired check, joint holders where the screened details fit both or neither, companies (incorporated after the report, no country, legal form only), partial dates, empty and placeholder names, and a hit that does not exist. That found a hit named only in Cyrillic being cleared as a name mismatch; it now goes to review.

- **Same meaning, different form.** We rewrote the development data in ways that keep its meaning (date formats, country codes and nationalities, name order and accents, list and string fields, odd spaces, file names, malformed rows) and re-ran. That found and fixed real faults, including a non-breaking space that made a namesake's search result clear a true match.
- **Same facts, different wording.** The unseen set words its free text differently, so we generated 1,266 new sentences of the seven kinds the system reads (approved and failed identity checks, residence notes, listing and ordinary search snippets, birth and age sentences), filled them with invented values and compared what the patterns read with what was put in. No development record was sent to a model; it was used at development time only, to write sentence templates. The first run read 48 sentences wrongly. After fixing the patterns, none is read wrongly and 14 (1.1%) are left unread, each of which can only cost a `REVIEW`. The failed-check wordings matter most: none of the 186 is taken as an approved check.

## What it does not do

- It applies the name rule literally. A misspelt verified name set against a listed variant of the hit's name (a typo of one spelling against another spelling of the same name) is a mismatch, as the practice labels treat a name against its short form.
- It reads a date of death only from the `deceased_date` field, not from profile text, where a "died on" sentence is often about someone else.
- It reads ages and birth dates from text with patterns. Wording the patterns do not cover is left unread, which costs a review, not a wrong answer.
- It has seen 50 answers. The review rate on the development set is 28%; most of those hits have a matching name and no birth date anywhere in the files.

## Model, prompts, time and cost

No model and no prompts. A run on the development set takes about 5 seconds on a laptop and costs nothing.

We chose this because the SOP is precise enough to implement exactly, the worst mistake (clearing a true match) is one a deterministic rule can rule out, and the notes and search snippets, though paraphrased, carry a small fixed set of facts that patterns read reliably. On the development set the patterns read all but 4 of 22,575 identity-check notes and all but 5 of 16,882 search snippets; each miss costs at most a `REVIEW`.

## Results on the practice labels

`validate.py --score` reports 819 lines, 0 problems, 50 of 50 right decisions, no unsafe clearances and a decision score of 100. On every label the reason code is among the accepted codes, the customer record is an accepted record, and the evidence IDs equal the label's.

## Three cases

**A clear mismatch: `H-622EDD` (alert `A-AE9222`) → `DIFFERENT_ENTITY`, `DOB_MISMATCH`.**
The alert's login handle leads to `CUS-270502`. No provider check exists, but onboarding note `NOTE-CA9BBC` records an approved check with date of birth 1979-04-17. The hit has no `dobs` and no birth sentence in its notes, so search results for its name are read: most are namesakes, and `SR-A88751` is the one describing the same kind of listing in the hit's country. It gives an age at a date, so the hit was born in 1972 or 1973. Both years are more than a year from 1979, so every birth date the hit gives is contradicted. Evidence: `NOTE-CA9BBC`, `SR-A88751`.

**A match: `H-0181BD` (alert `A-957E63`) → `SAME_ENTITY`, `NAME_DOB_LOCATION_MATCH`.**
The account reference leads to two joint holders; the screened name and date of birth fit `CUS-775419`. `CUS-138588` shares that record's phone and full date of birth, so both are one person. An onboarding note (`NOTE-CB2CB8`) verifies the name and a date of birth of 1985-09-12. The hit's birth years come from `SR-590704` (1985 or 1986), both sides are tied to Brazil, the verified name agrees with the hit's, and nothing contradicts. Evidence: `NOTE-CB2CB8`, `SR-590704`, `CUS-138588`, `CUS-775419`.

**Left for review: `H-ADF453` (alert `A-0DF328`) → `REVIEW`, `INSUFFICIENT_DATA`.**
The login handle leads to `CUS-375608`, whose latest approved check `KYC-A932A4` verifies the legal name and a date of birth of 1979-08-14. The hit was born in 1979 or 1980: the names agree (one part is a listed variant, the other is one edit away) and the dates agree. But the customer is tied only to Germany and the hit only to the United States. The SOP says a country difference does not clear a hit whose name and date of birth agree, and a confirmation needs nothing to contradict it, so a person decides. Evidence: `KYC-A932A4`, `CUS-375608`.
