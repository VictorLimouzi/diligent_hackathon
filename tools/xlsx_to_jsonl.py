"""Export the browse workbook to JSON Lines, as a stand-in for the data room's dev/ folder.

Usage: python3 -I tools/xlsx_to_jsonl.py "<workbook.xlsx>" <out_dir>

The workbook is a read-only view (long texts cut at 32,000 characters), so the
real dev/ files should replace this export as soon as they are available.
Standard library only.
"""
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

N = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

TABLES = {
    "alerts": "alerts",
    "hits": "hits",
    "customers": "customers",
    "accounts": "accounts",
    "onboarding": "onboarding",
    "kyc checks": "kyc_checks",
    "notes": "notes",
    "search results": "search_results",
    "practice labels": "practice_labels",
}


def col_index(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def shared_strings(z):
    out = []
    if "xl/sharedStrings.xml" not in z.namelist():
        return out
    for _, el in ET.iterparse(z.open("xl/sharedStrings.xml")):
        if el.tag == N + "si":
            out.append("".join(t.text or "" for t in el.iter(N + "t")))
            el.clear()
    return out


def sheet_rows(z, path, ss):
    for _, el in ET.iterparse(z.open(path)):
        if el.tag != N + "row":
            continue
        row = {}
        for c in el:
            if c.tag != N + "c":
                continue
            t = c.get("t")
            v = c.find(N + "v")
            if t == "s" and v is not None:
                val = ss[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(N + "t"))
            else:
                val = v.text if v is not None else None
            if val is not None and val != "":
                row[col_index(c.get("r"))] = val
        yield row
        el.clear()


def main(xlsx, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    z = zipfile.ZipFile(xlsx)
    ss = shared_strings(z)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rmap = {r.get("Id"): r.get("Target") for r in rels}
    for s in wb.find(N + "sheets"):
        name = s.get("name")
        target = rmap[s.get(R + "id")]
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        key = name.strip().lower()
        rows = sheet_rows(z, path, ss)
        if key in TABLES:
            header = next(rows)
            cols = [header.get(i) for i in range(max(header) + 1)]
            n = 0
            with open(os.path.join(out_dir, TABLES[key] + ".jsonl"), "w", encoding="utf-8") as f:
                for row in rows:
                    if not row:
                        continue
                    rec = {cols[i]: row.get(i) for i in range(len(cols)) if cols[i]}
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
            print(f"{name}: {n} rows")
        elif key == "policy":
            lines = [row.get(0) or "" for row in rows]
            while lines and not lines[-1]:
                lines.pop()
            cut = lines.index("name_variants.json") if "name_variants.json" in lines else len(lines)
            with open(os.path.join(out_dir, "POLICY.md"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines[:cut]).rstrip() + "\n")
            groups, forms = [], []
            for line in lines[cut + 2:]:
                if line.lower().startswith("company legal forms:"):
                    forms = [x.strip() for x in line.split(":", 1)[1].split(",") if x.strip()]
                elif line.strip():
                    groups.append([x.strip() for x in line.split(",") if x.strip()])
            with open(os.path.join(out_dir, "name_variants.json"), "w", encoding="utf-8") as f:
                json.dump({"name_variants": groups, "company_legal_forms": forms}, f, ensure_ascii=False, indent=1)
            print(f"{name}: policy {cut} lines, {len(groups)} variant groups, {len(forms)} legal forms")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
