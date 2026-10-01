"""CLEANING + TRANSFORMATION for issue text."""

from __future__ import annotations

import html
import re
import unicodedata

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]{1,200}`")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]{1,200}>")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_ISSUE_REF_RE = re.compile(r"(?:^|\s)(#\d+|GH-\d+|[\w.-]+/[\w.-]+#\d+)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_QUOTE_RE = re.compile(r"^\s*>.*$", re.MULTILINE)
_HEADER_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_NUMBERED_RE = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*", re.MULTILINE)
_NON_WORD_RE = re.compile(r"[^\w\s#.+/_-]", re.UNICODE)
_REPEAT_CHAR_RE = re.compile(r"(.)\1{3,}")


def clean_text(text: str | None, *, keep_issue_refs: bool = False) -> str:
    """Normalise a raw GitHub issue/PR body into TF-IDF-friendly text.

    Removes code blocks, HTML, URLs, e-mails and diff/quote noise. Issue references are
    masked out because `#1234` appears in almost every issue and carries no category or
    priority signal — keeping it would let the model memorise issue numbers.
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = _COMMENT_RE.sub(" ", s)
    s = _FENCE_RE.sub(" CODEBLOCK ", s)
    s = _INLINE_CODE_RE.sub(" code ", s)
    s = _HTML_TAG_RE.sub(" ", s)
    s = _IMAGE_RE.sub(" ", s)
    s = html.unescape(s)
    s = _URL_RE.sub(" URL ", s)
    s = _EMAIL_RE.sub(" EMAIL ", s)
    s = _QUOTE_RE.sub(" ", s)
    s = _CHECKBOX_RE.sub(" ", s)
    s = _HEADER_RE.sub(" ", s)
    s = _BULLET_RE.sub(" ", s)
    s = _NUMBERED_RE.sub(" ", s)
    s = _REPEAT_CHAR_RE.sub(r"\1\1\1", s)
    if not keep_issue_refs:
        s = _ISSUE_REF_RE.sub(" REF ", s)
    s = _NON_WORD_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s)
    s = _MULTI_NL_RE.sub("\n\n", s)
    return s.strip().lower()


def clean_title(title: str | None) -> str:
    return clean_text(title, keep_issue_refs=False)


def build_document(title: str | None, body: str | None) -> str:
    """Title is repeated so short titles are not drowned out by long bodies in TF-IDF."""
    t = clean_title(title)
    b = clean_text(body)
    return f"{t} {t} {b}".strip()


def truncate_words(text: str, max_words: int = 400) -> str:
    parts = text.split()
    return " ".join(parts[:max_words]) if len(parts) > max_words else text


def token_stats(text: str) -> dict[str, float]:
    words = text.split()
    return {
        "length_chars": float(len(text)),
        "length_words": float(len(words)),
        "length_sentences": float(text.count(".") + text.count("!") + text.count("?")),
        "unique_word_ratio": (len(set(words)) / len(words)) if words else 0.0,
        "has_code_marker": float("codeblock" in text or "code" in text.split()),
    }
