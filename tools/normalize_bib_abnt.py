#!/usr/bin/env python3
import re
import sys
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional


ENTRY_START_RE = re.compile(r"^\s*@([a-zA-Z]+)\s*\{\s*([^,\s]+)\s*,\s*$")
FIELD_RE = re.compile(r"^(?P<indent>\s*)(?P<name>[A-Za-z][A-Za-z0-9_-]*)\s*=\s*\{(?P<value>.*)\}\s*,?\s*$")


def split_entries(text: str) -> List[str]:
    entries: List[str] = []
    current: List[str] = []
    depth = 0
    in_entry = False
    for line in text.splitlines():
        if not in_entry:
            if line.lstrip().startswith("@"):  # begin of entry
                in_entry = True
                current = [line]
                # naive depth count for braces
                depth = line.count("{") - line.count("}")
            else:
                # skip non-entry text between entries
                continue
        else:
            current.append(line)
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                entries.append("\n".join(current).strip())
                current = []
                in_entry = False
                depth = 0
    # handle last unterminated (shouldn't happen)
    if current:
        entries.append("\n".join(current).strip())
    return entries


def parse_entry(entry_text: str) -> Tuple[str, str, List[Tuple[str, str, str]], List[str]]:
    """
    Returns: (entry_type, key, fields [ (indent, name, value) ], unknown_lines)
    unknown_lines are lines that didn't match FIELD_RE and are inside the entry body (kept as-is)
    """
    lines = entry_text.splitlines()
    if not lines:
        raise ValueError("Empty entry text")
    m = ENTRY_START_RE.match(lines[0].rstrip())
    if not m:
        raise ValueError(f"Invalid entry start: {lines[0]}")
    entry_type = m.group(1)
    key = m.group(2)
    body_lines = lines[1:]

    fields: List[Tuple[str, str, str]] = []
    unknown: List[str] = []
    for body_line in body_lines[:-1]:  # exclude last '}' line
        stripped = body_line.rstrip()
        if stripped == "":
            unknown.append(body_line)
            continue
        fm = FIELD_RE.match(stripped)
        if fm:
            fields.append((fm.group("indent"), fm.group("name"), fm.group("value")))
        else:
            unknown.append(body_line)
    # last line assumed to be closing '}'
    return entry_type, key, fields, unknown


def build_entry(entry_type: str, key: str, fields: List[Tuple[str, str, str]], unknown: List[str], indent_style: str) -> str:
    lines: List[str] = []
    lines.append(f"@{entry_type}{{{key},")
    for indent, name, value in fields:
        use_indent = indent if indent is not None else indent_style
        lines.append(f"{use_indent}{name} = {{{value}}},")
    lines.extend(unknown)
    lines.append("}")
    return "\n".join(lines)


def normalize_date_pt(date_str: str) -> Optional[str]:
    # Expected patterns like '25 mar. 2025', '17 de abril de 2025', '25 mar 2025'
    months = {
        "jan": 1, "janeiro": 1,
        "fev": 2, "fevereiro": 2,
        "mar": 3, "março": 3, "marco": 3,
        "abr": 4, "abril": 4,
        "mai": 5, "maio": 5,
        "jun": 6, "junho": 6,
        "jul": 7, "julho": 7,
        "ago": 8, "agosto": 8,
        "set": 9, "setembro": 9,
        "out": 10, "outubro": 10,
        "nov": 11, "novembro": 11,
        "dez": 12, "dezembro": 12,
    }
    s = date_str.strip().lower()
    s = s.replace("de ", "").replace(".", "")
    parts = s.split()
    try:
        if len(parts) == 3:
            day = int(parts[0])
            month = months.get(parts[1][:3])
            year = int(parts[2])
            if month:
                return f"{year:04d}-{month:02d}-{day:02d}"
    except Exception:
        return None
    # fallback: try to parse YYYY-MM-DD already
    try:
        dt = datetime.fromisoformat(s)
        return dt.date().isoformat()
    except Exception:
        return None


def extract_url_and_access(note_value: str) -> Tuple[Optional[str], Optional[str]]:
    # Look for 'Disponível em: <url>' or 'Disponível em: url,' and 'Acesso em: date'
    url = None
    urldate = None
    # between 'Disponível em:' and either space + 'Acesso', or end
    # Remove angle brackets
    note = note_value.replace("<", "").replace(">", "")
    m = re.search(r"Dispon[ií]vel\s+em:\s*([^\s,;]+)", note, flags=re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
        if candidate.endswith('.'):
            candidate = candidate[:-1]
        url = candidate
    m2 = re.search(r"Acess(?:o|ado)\s+em:\s*([^.;\n]+)", note, flags=re.IGNORECASE)
    if m2:
        urldate = normalize_date_pt(m2.group(1))
    return url, urldate


def is_trivial_note(note_value: str) -> bool:
    text = note_value
    text = re.sub(r"Dispon[ií]vel\s+em:[^.;\n]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Acess(?:o|ado)\s+em:[^.;\n]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[<>\s.,;:]+", " ", text).strip()
    return text == ""


def clean_url_value(value: str) -> Optional[str]:
    # Remove angle brackets and any leading 'Disponível em:' prefix
    candidate = value.strip().replace("<", "").replace(">", "")
    # If contains an explicit URL, extract first http(s) substring
    m = re.search(r"https?://\S+", candidate)
    if m:
        url = m.group(0)
        # strip trailing punctuation
        url = url.rstrip(".,);]")
        return url
    # If no URL but looks like 'www.' or domain, return it
    m2 = re.search(r"\b(?:www\.)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/\S*)?", candidate)
    if m2:
        return m2.group(0)
    # Nothing valid
    return None


def _smart_title_case(token: str) -> str:
    if "-" in token:
        return "-".join(_smart_title_case(part) for part in token.split("-"))
    if "." in token:
        return token.upper()
    if len(token) == 0:
        return token
    lower = token.lower()
    return lower[0].upper() + lower[1:]


def _format_author_abnt(name: str) -> str:
    particles = {"da", "de", "do", "das", "dos", "e", "d", "di", "du", "van", "von", "der", "del", "della", "la", "le"}
    name = re.sub(r"\s+", " ", name.strip())
    if not name:
        return name
    # If already in 'Surname, Given' form
    if "," in name:
        left, right = name.split(",", 1)
        surname = left.strip().upper()
        given_parts = [p for p in right.strip().split() if p]
        formatted_given: list[str] = []
        for idx, part in enumerate(given_parts):
            pl = part.lower().strip(".,")
            if pl in particles and idx != 0:
                formatted_given.append(pl)
            else:
                formatted_given.append(_smart_title_case(part))
        return f"{surname}, {' '.join(formatted_given)}".strip()
    # Otherwise assume 'Given Middle Surname'
    parts = name.split()
    if len(parts) == 1:
        return parts[0].upper()
    surname = parts[-1].upper()
    given_parts = parts[:-1]
    formatted_given: list[str] = []
    for idx, part in enumerate(given_parts):
        pl = part.lower().strip(".,")
        if pl in particles and idx != 0:
            formatted_given.append(pl)
        else:
            formatted_given.append(_smart_title_case(part))
    return f"{surname}, {' '.join(formatted_given)}".strip()


def normalize_author_list_abnt(author_value: str) -> str:
    # Split by BibTeX 'and'
    parts = re.split(r"\s+and\s+", author_value.strip())
    normalized: list[str] = []
    for p in parts:
        token = p.strip().strip("{} ")
        if not token:
            continue
        if re.search(r"\bothers\.?\b", token, flags=re.IGNORECASE):
            normalized.append("others.")
            continue
        normalized.append(_format_author_abnt(token))
    return " and ".join(normalized)


def normalize_fields(entry_type: str, key: str, fields: List[Tuple[str, str, str]], entries_map: Optional[Dict[str, Tuple[str, List[Tuple[str, str, str]], List[str]]]] = None) -> List[Tuple[str, str, str]]:
    # Map fields; maintain order but with normalized names
    new_fields: List[Tuple[str, str, str]] = []
    seen_names: set = set()
    note_value_for_url: Optional[str] = None
    # Resolve crossref
    crossref_key: Optional[str] = None
    for _, n, v in fields:
        if n.lower() == "crossref":
            crossref_key = v.strip()
            break
    merged_fields: List[Tuple[str, str, str]] = list(fields)
    if crossref_key and entries_map and crossref_key in entries_map:
        parent_type, parent_fields, _ = entries_map[crossref_key]
        child_names = {n.lower() for _, n, _ in merged_fields}
        for p_indent, p_name, p_value in parent_fields:
            pl = p_name.lower()
            if pl not in child_names and pl not in {"key", "crossref"}:
                merged_fields.append((p_indent, p_name, p_value))
        merged_fields = [(i, n, v) for (i, n, v) in merged_fields if n.lower() != "crossref"]

    for indent, name, value in merged_fields:
        lname = name.lower()
        # Trim whitespace around value
        v = value.strip()
        # Normalize URL fields that mistakenly include extra text
        if lname == "url":
            cleaned = clean_url_value(v)
            if cleaned:
                v = cleaned
        # Fix doi format
        if lname == "doi":
            v = v.replace("http://doi.org/", "").replace("https://doi.org/", "").strip()
        # Normalize author 'et al.' to BibTeX-friendly 'and others'
        if lname == "author":
            v = re.sub(r"\bet\s*al\.?\b", "and others", v, flags=re.IGNORECASE)
            v = normalize_author_list_abnt(v)
        # Capture note for URL extraction
        if lname == "note":
            note_value_for_url = v
            if is_trivial_note(v):
                # skip trivial note
                continue
        # If bibliographic fields contain URL/note fragments, split and extract
        if lname in {"journal", "publisher", "booktitle", "address"}:
            if re.search(r"Dispon[ií]vel\s+em:|https?://", v, flags=re.IGNORECASE):
                # try to extract URL and urldate
                url_from_field, urldate_from_field = extract_url_and_access(v)
                # strip text starting at 'Disponível em:'
                v = re.split(r"Dispon[ií]vel\s+em:\s*", v, flags=re.IGNORECASE)[0].strip()
                v = v.rstrip(" .;")
                # add url/urldate if not present yet
                existing_names = {n for _, n, _ in new_fields}
                if url_from_field and "url" not in existing_names:
                    new_fields.append((indent, "url", url_from_field))
                if urldate_from_field and "urldate" not in existing_names:
                    new_fields.append((indent, "urldate", urldate_from_field))
        # Drop non-standard 'key' field
        if lname == "key":
            continue
        # Fix Bardin book common mistakes
        if key.lower() == "bardin2016":
            if lname == "edition" and v == "70":
                # drop bogus edition field
                # skip adding this field
                continue
            if lname == "publisher" and v.strip("{} ") in {"São Paulo", "Sao Paulo"}:
                v = "Edições 70"
            if lname == "address" and v.strip() == "":
                v = "São Paulo"
        new_fields.append((indent, lname, v))
        seen_names.add(lname)

    # If Bardin2016 missing correct publisher/address, add them
    if key.lower() == "bardin2016":
        names = {n for _, n, _ in new_fields}
        if "publisher" not in names:
            new_fields.append((new_fields[0][0] if new_fields else "", "publisher", "Edições 70"))
        if "address" not in names:
            new_fields.append((new_fields[0][0] if new_fields else "", "address", "São Paulo"))

    # Extract url/urldate from note for misc/online entries
    if note_value_for_url and not any(n == "url" for _, n, _ in new_fields):
        url, urldate = extract_url_and_access(note_value_for_url)
        if url:
            new_fields.append((new_fields[0][0] if new_fields else "", "url", url))
        if urldate and not any(n == "urldate" for _, n, _ in new_fields):
            new_fields.append((new_fields[0][0] if new_fields else "", "urldate", urldate))

    # ABNT-friendly enrichment for misc entries in Portuguese context
    if entry_type.lower() == "misc":
        # If author is missing, try to infer from key
        names = {n for _, n, _ in new_fields}
        key_lower = key.lower()
        if "author" not in names:
            inferred_author: Optional[str] = None
            # 'BRASIL' often used for legal acts and gov sites
            if "brasil" in key_lower:
                inferred_author = "BRASIL"
            elif key_lower.startswith("w3c"):
                inferred_author = "W3C"
            if inferred_author:
                new_fields.append((new_fields[0][0] if new_fields else "", "author", inferred_author))

        # If title missing, extract from note before 'Disponível em:'
        if "title" not in names and note_value_for_url:
            raw = note_value_for_url.replace("<", "").replace(">", "")
            mtitle = re.split(r"Dispon[ií]vel\s+em:\s*", raw, flags=re.IGNORECASE)
            candidate = mtitle[0].strip() if mtitle else None
            if candidate:
                # Remove trailing 'Acesso em...' if present, and extra punctuation
                candidate = re.split(r"Acess(?:o|ado)\s+em:\s*", candidate, flags=re.IGNORECASE)[0].strip()
                candidate = candidate.rstrip(" .;")
                if candidate:
                    new_fields.append((new_fields[0][0] if new_fields else "", "title", candidate))

        # If year missing, use urldate year
        names = {n for _, n, _ in new_fields}
        if "year" not in names:
            urldate_val = None
            for _, n, v in new_fields:
                if n == "urldate":
                    urldate_val = v
                    break
            if urldate_val and re.match(r"^\d{4}-\d{2}-\d{2}$", urldate_val):
                new_fields.append((new_fields[0][0] if new_fields else "", "year", urldate_val[:4]))
        # howpublished when URL present
        if any(n == "url" for _, n, _ in new_fields) and not any(n == "howpublished" for _, n, _ in new_fields):
            new_fields.append((new_fields[0][0] if new_fields else "", "howpublished", "Online"))

    # language detection for PT-BR
    if not any(n == "language" for _, n, _ in new_fields):
        pt_markers = [
            r"Dispon[ií]vel", r"Acesso", r"Lei nº", r"Decreto nº", r"Educa[çc][aã]o",
            r"Sociedade", r"São Paulo", r"Edições", r"Revista", r"Anais"
        ]
        is_pt = False
        for _, n, v in new_fields:
            text = f"{n} {v}"
            if any(re.search(p, text, flags=re.IGNORECASE) for p in pt_markers):
                is_pt = True
                break
        if is_pt:
            new_fields.append((new_fields[0][0] if new_fields else "", "language", "portuguese"))

    return new_fields


def order_fields(entry_type: str, fields: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
    order_map = {
        "article": ["author", "title", "journal", "volume", "number", "pages", "year", "doi", "url", "urldate", "language"],
        "book": ["author", "title", "edition", "address", "publisher", "year", "isbn", "doi", "url", "urldate", "language"],
        "inproceedings": ["author", "title", "booktitle", "year", "organization", "publisher", "address", "pages", "doi", "url", "urldate", "language"],
        "techreport": ["author", "title", "institution", "year", "number", "address", "doi", "url", "urldate", "language"],
        "misc": ["author", "title", "year", "howpublished", "url", "urldate", "language", "note"],
    }
    desired = order_map.get(entry_type.lower(), [])
    ordered: List[Tuple[str, str, str]] = []
    used = set()
    indent_style = fields[0][0] if fields else ""
    # add desired fields first
    for name in desired:
        for indent, n, v in fields:
            if n == name and n not in used:
                ordered.append((indent_style, n, v))
                used.add(n)
                break
    # then remaining fields
    for indent, n, v in fields:
        if n not in used:
            ordered.append((indent_style, n, v))
            used.add(n)
    return ordered


def entry_fingerprint(entry_type: str, key: str, fields: List[Tuple[str, str, str]]) -> Tuple[str, str, str]:
    doi = ""
    url = ""
    title = ""
    for _, name, value in fields:
        lname = name.lower()
        if lname == "doi":
            doi = value.strip().lower()
        elif lname == "url":
            url = value.strip().lower()
        elif lname == "title":
            t = value.strip().lower()
            t = re.sub(r"[^a-z0-9]+", "", t)
            title = t
    return doi, url, title


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: normalize_bib_abnt.py /absolute/path/to/biblio.bib")
        return 2
    bib_path = Path(sys.argv[1])
    if not bib_path.exists():
        print(f"File not found: {bib_path}")
        return 1

    original = bib_path.read_text(encoding="utf-8")
    backup_path = bib_path.with_suffix(bib_path.suffix + ".bak")
    shutil.copyfile(bib_path, backup_path)

    entries = split_entries(original)
    normalized_entries: List[str] = []
    seen: set = set()
    dropped = 0
    changed = 0
    # First parse all entries to support crossref resolution
    parsed: List[Tuple[str, str, List[Tuple[str, str, str]], List[str]]] = []
    entries_map: Dict[str, Tuple[str, List[Tuple[str, str, str]], List[str]]] = {}

    for entry_text in entries:
        try:
            entry_type, key, fields, unknown = parse_entry(entry_text)
        except Exception:
            parsed.append(("", "", [], [entry_text]))
            continue
        parsed.append((entry_type, key, fields, unknown))
        entries_map[key] = (entry_type, fields, unknown)

    for entry_type, key, fields, unknown in parsed:
        if not entry_type:
            normalized_entries.append("\n".join(unknown))
            continue

        # Determine indent style from the first field if present
        indent_style = ""
        if fields:
            indent_style = fields[0][0]

        new_fields = normalize_fields(entry_type, key, fields, entries_map)
        new_fields = order_fields(entry_type, new_fields)

        # Deduplicate by fingerprint
        fp = entry_fingerprint(entry_type, key, new_fields)
        if any(fp):
            if fp in seen:
                dropped += 1
                continue
            seen.add(fp)

        # Detect if changed
        if new_fields != fields:
            changed += 1

        normalized = build_entry(entry_type, key, new_fields, unknown, indent_style)
        normalized_entries.append(normalized)

    # Join with two newlines between entries, matching common .bib formatting
    output_text = "\n\n".join(normalized_entries) + "\n"
    bib_path.write_text(output_text, encoding="utf-8")

    print(f"Processed entries: {len(entries)}")
    print(f"Modified entries: {changed}")
    print(f"Removed as duplicates: {dropped}")
    print(f"Backup saved to: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

