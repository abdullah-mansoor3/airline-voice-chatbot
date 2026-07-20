from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


HEADING_RE = re.compile(
    r"^(?P<heading>(#{2,5}\s+.+|D\d{1,2}(?:\.\d+)?\s+.+|Article\s+\d+.+|RULE\s+\d+.+))$",
    re.IGNORECASE | re.MULTILINE,
)
SUBCLAUSE_RE = re.compile(r"(?=^\s*(?:[-*]\s+)?(?:[A-Z]|\d{1,2})[\).]\s+)", re.MULTILINE)
MAX_WORDS = 520


@dataclass(frozen=True)
class PolicyDocument:
    title: str
    version: str | None
    effective_date: str | None
    jurisdiction: str
    category: list[str]
    source_url: str | None
    document_type: str | None
    carrier: str | None
    regulator: str | None
    document_id: str | None


@dataclass(frozen=True)
class PolicyChunk:
    chunk_index: int
    heading: str
    chunk_text: str
    embedded_text: str
    metadata: dict[str, Any]


def chunk_policy_file(path: str | Path) -> tuple[PolicyDocument, list[PolicyChunk]]:
    path = Path(path)
    raw_text = path.read_text(encoding="utf-8")
    return chunk_policy_text(raw_text, source_name=path.name, path_for_inference=path)


def chunk_policy_text(
    raw_text: str, source_name: str, path_for_inference: Path | None = None
) -> tuple[PolicyDocument, list[PolicyChunk]]:
    frontmatter, body = _split_frontmatter(raw_text)
    document = _document_from_frontmatter(source_name, frontmatter, body, path_for_inference)
    sections = _split_sections(body)

    chunks: list[PolicyChunk] = []
    for heading, section_text in sections:
        for piece in _split_oversized_section(section_text):
            chunk_index = len(chunks)
            breadcrumb = _breadcrumb(document, heading)
            chunk_text = piece.strip()
            embedded_text = f"{breadcrumb} — {chunk_text}"
            chunks.append(
                PolicyChunk(
                    chunk_index=chunk_index,
                    heading=heading,
                    chunk_text=chunk_text,
                    embedded_text=embedded_text,
                    metadata={
                        "title": document.title,
                        "version": document.version,
                        "effective_date": document.effective_date,
                        "jurisdiction": document.jurisdiction,
                        "category": document.category,
                        "source_url": document.source_url,
                        "document_type": document.document_type,
                        "carrier": document.carrier,
                        "regulator": document.regulator,
                        "document_id": document.document_id,
                        "heading": heading,
                    },
                )
            )

    return document, chunks


def _split_frontmatter(raw_text: str) -> tuple[dict[str, Any], str]:
    if raw_text.startswith("---"):
        _, frontmatter_text, body = raw_text.split("---", 2)
        try:
            parsed = yaml.safe_load(frontmatter_text) or {}
        except yaml.YAMLError:
            parsed = _parse_loose_frontmatter(frontmatter_text)
        return parsed, body.strip()
    return {}, raw_text.strip()


def _parse_loose_frontmatter(frontmatter_text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in frontmatter_text.splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip().strip('"').strip("'")
    return parsed


def _document_from_frontmatter(
    source_name: str, frontmatter: dict[str, Any], body: str, path_for_inference: Path | None = None
) -> PolicyDocument:
    title = _first_heading(body) or source_name.replace("_", " ").replace(".md", "").title()
    if title and len(title) > 200:
        title = title[:197] + "..."
    
    # Fallbacks for category/jurisdiction if path isn't provided
    default_cat = _infer_category(path_for_inference) if path_for_inference else "customer_refund"
    default_jur = _infer_jurisdiction(path_for_inference, frontmatter) if path_for_inference else "international"
    
    category = _as_list(frontmatter.get("category") or default_cat)
    carrier = frontmatter.get("carrier")
    regulator = frontmatter.get("regulator")
    jurisdiction = default_jur if path_for_inference else (frontmatter.get("jurisdiction") or "international")

    return PolicyDocument(
        title=title,
        version=str(frontmatter.get("version")) if frontmatter.get("version") else None,
        effective_date=(
            str(frontmatter.get("effective_date") or frontmatter.get("date_of_implementation"))
            if (frontmatter.get("effective_date") or frontmatter.get("date_of_implementation"))
            else None
        ),
        jurisdiction=jurisdiction,
        category=category,
        source_url=frontmatter.get("source_url"),
        document_type=frontmatter.get("document_type"),
        carrier=carrier,
        regulator=regulator,
        document_id=frontmatter.get("document_id"),
    )


def _split_sections(body: str) -> list[tuple[str, str]]:
    matches = list(HEADING_RE.finditer(body))
    if not matches:
        return [("Document", body)]

    sections: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        heading = _clean_heading(match.group("heading"))
        sections.append((heading, body[start:end].strip()))
    return sections


def _split_oversized_section(section_text: str) -> list[str]:
    if _word_count(section_text) <= MAX_WORDS:
        return [section_text]

    parts = [part.strip() for part in SUBCLAUSE_RE.split(section_text) if part.strip()]
    if len(parts) <= 1:
        return _split_by_paragraph(section_text)

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for part in parts:
        part_words = _word_count(part)
        if current and current_words + part_words > MAX_WORDS:
            chunks.append("\n\n".join(current))
            current = [part]
            current_words = part_words
        else:
            current.append(part)
            current_words += part_words
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _split_by_paragraph(text: str) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for paragraph in paragraphs:
        words = _word_count(paragraph)
        if current and current_words + words > MAX_WORDS:
            chunks.append("\n\n".join(current))
            current = [paragraph]
            current_words = words
        else:
            current.append(paragraph)
            current_words += words
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text]


def _breadcrumb(document: PolicyDocument, heading: str) -> str:
    label = document.document_id or document.title
    return f"{label} {heading}".strip()


def _first_heading(body: str) -> str | None:
    for line in body.splitlines():
        if line.startswith("# "):
            return _clean_heading(line)
    return None


def _clean_heading(heading: str) -> str:
    cleaned = heading.strip().lstrip("#").strip()
    if len(cleaned) > 200:
        cleaned = cleaned[:197] + "..."
    return cleaned


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return ["customer_refund"]


def _infer_category(path: Path) -> str:
    name = path.name.lower()
    if "baggage" in name:
        return "customer_baggage"
    if "crew" in name or "duty" in name or "117" in name:
        return "crew_duty_rest"
    if "faa" in name or "pcaa" in name or "montreal" in name:
        return "regulatory"
    return "customer_refund"


def _infer_jurisdiction(path: Path, frontmatter: dict[str, Any]) -> str:
    country = str(frontmatter.get("country") or "").lower()
    regulator = str(frontmatter.get("regulator") or "").lower()
    name = path.name.lower()
    if "pakistan" in country or "pcaa" in name or "pia" in name or "airblue" in name or "serene" in name:
        return "PK"
    if "montreal" in name or "icao" in regulator or "iata" in regulator:
        return "international"
    if "faa" in name or "delta" in name or "southwest" in name:
        return "US"
    return "international"


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))
