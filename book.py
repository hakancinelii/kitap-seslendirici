import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


MAX_SEGMENT_CHARS = 220


_DECORATIVE_SYMBOLS = re.compile("[\u2600-\u27BF\uFE0F]+")
_ROMAN_MARKER = re.compile(r"^[IVX]{1,4}\.\s*")
_MD_EMPH = re.compile(r"[*_~`]+")
_SENT_SPLIT = re.compile(r"(?<=[.!?…;])\s+")
_OVERLONG_SPLIT = re.compile(r"(?<=[,:—])\s+")


@dataclass
class Segment:
    id: str
    para_id: str
    idx: int
    text: str


@dataclass
class Paragraph:
    id: str
    text: str
    segments: List[Segment] = field(default_factory=list)


@dataclass
class Heading:
    id: str
    level: int
    text: str


@dataclass
class Book:
    title: str
    items: list  # ordered mix of Heading and Paragraph
    paragraphs: List[Paragraph] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)


def _clean_sentence(text: str) -> str:
    text = _ROMAN_MARKER.sub("", text)
    text = _DECORATIVE_SYMBOLS.sub("", text)
    text = _MD_EMPH.sub("", text)
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u00ad", "")
    return re.sub(r"\s+", " ", text).strip()


def _split_sentences(text: str) -> List[str]:
    parts = _SENT_SPLIT.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def _split_overlong(sentence: str) -> List[str]:
    if len(sentence) <= MAX_SEGMENT_CHARS:
        return [sentence]
    out: List[str] = []
    for clause in _OVERLONG_SPLIT.split(sentence):
        clause = clause.strip()
        if not clause:
            continue
        if len(clause) <= MAX_SEGMENT_CHARS:
            out.append(clause)
            continue
        words = clause.split(" ")
        buf = ""
        for w in words:
            if buf and len(buf) + 1 + len(w) > MAX_SEGMENT_CHARS:
                out.append(buf)
                buf = w
            else:
                buf = f"{buf} {w}".strip()
        if buf:
            out.append(buf)
    return out


def _split_paragraph(text: str, para_id: str, seg_id_start: int) -> List[Segment]:
    raw_sentences = _split_sentences(text)
    fragments: List[str] = []
    for s in raw_sentences:
        cleaned = _clean_sentence(s)
        if not cleaned:
            continue
        fragments.extend(_split_overlong(cleaned))

    segments: List[str] = []
    buf: List[str] = []
    buf_len = 0
    for frag in fragments:
        if buf and buf_len + 1 + len(frag) > MAX_SEGMENT_CHARS:
            segments.append(" ".join(buf))
            buf = [frag]
            buf_len = len(frag)
        else:
            if buf:
                buf_len += 1 + len(frag)
            else:
                buf_len = len(frag)
            buf.append(frag)
    if buf:
        segments.append(" ".join(buf))

    result: List[Segment] = []
    for i, seg_text in enumerate(segments):
        result.append(
            Segment(
                id=f"seg_{seg_id_start + i}",
                para_id=para_id,
                idx=i,
                text=seg_text,
            )
        )
    return result


def parse_book(path: str | Path) -> Book:
    lines = Path(path).read_text(encoding="utf-8").splitlines()

    title = ""
    items: list = []
    paragraphs: List[Paragraph] = []
    segments: List[Segment] = []

    buf: List[str] = []

    def flush_paragraph() -> None:
        nonlocal buf
        if not buf:
            return
        para_text = re.sub(r"\s+", " ", " ".join(buf)).strip()
        buf = []
        if not para_text:
            return
        para = Paragraph(id=f"p{len(paragraphs)}", text=para_text)
        segs = _split_paragraph(para_text, para.id, len(segments))
        para.segments = segs
        paragraphs.append(para)
        segments.extend(segs)
        items.append(para)

    for raw in lines:
        stripped = raw.strip()
        if stripped in ("---", "***", "___") or set(stripped) <= {"*", "-", "_"}:
            flush_paragraph()
            continue
        if not stripped:
            flush_paragraph()
            continue
        if stripped.startswith("#"):
            flush_paragraph()
            level = len(stripped) - len(stripped.lstrip("#"))
            heading_text = stripped.lstrip("#").strip()
            if level == 1:
                title = heading_text or title
            items.append(Heading(id=f"h{len(items)}", level=level, text=heading_text))
            continue

        buf.append(stripped)

    flush_paragraph()

    return Book(title=title or Path(path).stem, items=items, paragraphs=paragraphs, segments=segments)
