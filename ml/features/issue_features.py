"""FEATURE ENGINEERING for the three issue models (classification, priority, effort).

Category and priority labels are *derived from real repository label vocabulary* — the
signal already exists in the data, we only map it onto a common vocabulary. Where a
project has no severity label, the row is excluded rather than guessed.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from preprocessing.clean_text import build_document, clean_text, token_stats

# ------------------------------------------------------------------ categories
CATEGORY_BUG = "BUG"
CATEGORY_FEATURE = "FEATURE_REQUEST"
CATEGORY_DOCS = "DOCUMENTATION"
CATEGORY_QUESTION = "QUESTION"
CATEGORY_ENHANCEMENT = "ENHANCEMENT"
CATEGORY_OTHER = "OTHER"
CATEGORIES = [
    CATEGORY_BUG,
    CATEGORY_FEATURE,
    CATEGORY_DOCS,
    CATEGORY_QUESTION,
    CATEGORY_ENHANCEMENT,
    CATEGORY_OTHER,
]

_BUG_RE = re.compile(
    r"^(bug|defect|error|failure|broken|crash|regression|issue|"
    r"problem|incident|p0|p1|hotfix)$",
    re.I,
)
_FEATURE_RE = re.compile(
    r"^(feature|feat|feature[- ]request|new feature|"
    r"support (for )?|implement|add|proposal|rfc|epic)$",
    re.I,
)
_DOCS_RE = re.compile(
    r"^(doc|docs|documentation|documentation[- ]?fix|typo|"
    r"readme|docstring|comment|comment[s]? doc|api[- ]docs?|"
    r"spell[- ]?check|grammar)$",
    re.I,
)
_QUESTION_RE = re.compile(
    r"^(question|q|how|how[- ]?to|help|support|usage|"
    r"wondering|clarification|discussion|meta|rfdl)$",
    re.I,
)
_ENHANCEMENT_RE = re.compile(
    r"^(enhancement|enh|improve|improvement|optimi[sz]ation|"
    r"performance|perf|refactor|cleanup|clean[- ]up|"
    r"maintenance|tech[- ]debt|usability|ux|api[- ]change)$",
    re.I,
)

# Ordered because a single label can match more than one family.
_CATEGORY_RULES = (
    (_BUG_RE, CATEGORY_BUG),
    (_DOCS_RE, CATEGORY_DOCS),
    (_FEATURE_RE, CATEGORY_FEATURE),
    (_ENHANCEMENT_RE, CATEGORY_ENHANCEMENT),
    (_QUESTION_RE, CATEGORY_QUESTION),
)
_TEXT_CATEGORY_HINTS = (
    (
        re.compile(
            r"\b(stack ?trace|traceback|exception|regression|segfault|"
            r"does ?n'?t work|not working|broken|crash(?:es|ed)?|"
            r"unexpected(?:ly)?|fail(?:s|ed|ure)?)\b",
            re.I,
        ),
        CATEGORY_BUG,
    ),
    (
        re.compile(
            r"\b(how (do|can|to)|is it possible|any (idea|one)|"
            r"could you explain|what is the|why does)\b",
            re.I,
        ),
        CATEGORY_QUESTION,
    ),
    (
        re.compile(
            r"\b(document|readme|typo|docstring|spelling|grammar|"
            r"comment[s]? out)\b",
            re.I,
        ),
        CATEGORY_DOCS,
    ),
    (
        re.compile(
            r"\b(please add|add support|feature request|would be (nice|great|useful)|"
            r"propose|implement|rfc)\b",
            re.I,
        ),
        CATEGORY_FEATURE,
    ),
    (
        re.compile(
            r"\b(improve|optimi[sz]e|refactor|clean ?up|performance|slow|"
            r"usability|technical debt)\b",
            re.I,
        ),
        CATEGORY_ENHANCEMENT,
    ),
)

# ------------------------------------------------------------------- priority
PRIORITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
_CRITICAL_RE = re.compile(
    r"^(p0|sev0|severity[- ]?0|critical|blocker|"
    r"urgent|production[- ]down|outage|breaking)$",
    re.I,
)
_HIGH_RE = re.compile(
    r"^(p1|sev1|severity[- ]?1|high|major|important|regression|"
    r"security|vulnerab(ility)?|data[- ]loss|cve-\d+)$",
    re.I,
)
_MEDIUM_RE = re.compile(
    r"^(p2|sev2|severity[- ]?2|medium|moderate|normal|"
    r"enhancement|feature|bug|minor|default)$",
    re.I,
)
_LOW_RE = re.compile(
    r"^(p3|p4|sev3|sev4|severity[- ]?3|low|cosmetic|trivial|"
    r"nice[- ]to[- ]have|question|docs?|documentation|"
    r"wontfix|duplicate|good first issue|help wanted)$",
    re.I,
)
_PRIORITY_RULES = (
    (_CRITICAL_RE, "CRITICAL"),
    (_HIGH_RE, "HIGH"),
    (_MEDIUM_RE, "MEDIUM"),
    (_LOW_RE, "LOW"),
)
_CRITICAL_BODY_RE = re.compile(
    r"\b(data loss|corrupt|security|vulnerab|exploit|credential|password leak|"
    r"cannot install|complete(ly)? broken|production (is )?down|outage|"
    r"crash(es)? on (start|import|load)|segfault)\b",
    re.I,
)
_WORKAROUND_RE = re.compile(
    r"\b(workaround|for now (i|we) (can|am|are)|temporar(y|ily)|until (this|it) is fixed|"
    r"i'?m (using|on) )\b",
    re.I,
)
_STACKTRACE_RE = re.compile(
    r"\b(traceback|stack ?trace|\bat \w+ \(|exception|error:|panic:|fatal:)\b", re.I
)


# ------------------------------------------------------------------- labelling
def _labels_of(row: pd.Series) -> list[str]:
    raw = row.get("labels") or []
    if isinstance(raw, str):
        return [p.strip().lower() for p in raw.split(",") if p.strip()]
    return [str(l).strip().lower() for l in raw if str(l).strip()]


def label_category(labels: list[str], text: str) -> str | None:
    """Derive a category from real label vocabulary; fall back to title/body wording.

    Returns None when neither the labels nor the text carry a usable signal, so the
    row is dropped rather than forced into a wrong class.
    """
    for pattern, category in _CATEGORY_RULES:
        for lbl in labels:
            if pattern.fullmatch(lbl):
                return category
    title_words = text.split(" ", 3)
    head = title_words[0] if title_words else ""
    for pattern, category in _CATEGORY_RULES:
        if pattern.fullmatch(head):
            return category
    for pattern, category in _TEXT_CATEGORY_HINTS:
        if pattern.search(text[:1200]):
            return category
    return None


_LABEL_PRIORITY: tuple[tuple[str, str], ...] = (
    (
        r"^(p0|sev0|severity[- ]?0|critical|blocker|data[- ]loss|security|"
        r"vulnerab(ility)?|regression|breaking|production[- ]down|outage)$",
        "CRITICAL",
    ),
    (r"^(bug|defect|error|hotfix)$", "HIGH"),
    (
        r"^(p1|sev1|severity[- ]?1|high|major|important|performance|perf|"
        r"enhancement|enh|improvement|api[- ]change|metric)$",
        "MEDIUM",
    ),
    (
        r"^(p2|p3|p4|sev2|sev3|sev4|severity[- ]?[234]|medium|low|normal|default|"
        r"documentation|docs|question|q|how[- ]?to|usage|refactor(ing)?|"
        r"cleanup|clean[- ]up|tech[- ]debt|test|testing|typing|style|"
        r"good first issue|help wanted|documentation[- ]?fix|"
        r"dataset request|metric request|dataset discussion|metric discussion|"
        r"generic discussion|dataset-viewer|nlp-viewer|vision-viewer|"
        r"wontfix|wont[- ]fix|duplicate|stale|question[- ]?w[- ]?extra)$",
        "LOW",
    ),
    # Neutral/moderate buckets: a request or a scoped work item.
    (
        r"^(feature|feat|feature[- ]request|new feature|proposal|rfc|epic|"
        r"support|streaming|parquet|tensorflow|pytorch|datasets?|"
        r"speech|vision|nlp|audio|multimodal|evaluation|metric(s)?)$",
        "MEDIUM",
    ),
)


def label_priority(labels: list[str], text: str, comments: int) -> str | None:
    """Derive urgency from the issue's **human-applied labels** only.

    Text and comment count are deliberately ignored. An earlier version used them as a
    fallback, but the model receives the same text and comment count as *features*, so
    those rules leak straight into the target and inflate the score to a meaningless
    ~0.95 F1. Keeping the target label-only means the evaluation measures how well
    content predicts a human triage decision, not how well it recovers our own regexes.

    Returns None when no label maps to a level, so the row is dropped rather than
    guessed.
    """
    for lbl in labels:
        for pattern, level in _LABEL_PRIORITY:
            if re.fullmatch(pattern, lbl):
                return level
    return None


# -------------------------------------------------------------------- features
ASSOCIATION_RANK = {
    "owner": 3,
    "member": 3,
    "collaborator": 2,
    "contributor": 1,
    None: 0,
    "": 0,
    "none": 0,
    "first_time_contributor": 0,
    "first_timer": 0,
}


def structured_features(row: pd.Series) -> dict[str, float]:
    """Numeric, non-text features shared by the priority and effort models."""
    labels = _labels_of(row)
    text = row.get("_text", "")
    title = row.get("_title_text", "")
    comments = float(row.get("comments_count") or 0)
    assoc = str(row.get("author_association") or "").lower()
    label_blob = " ".join(labels)

    stats = token_stats(text)
    return {
        "comments_count": np.log1p(comments),
        "comments_raw": min(comments, 200.0),
        "has_comments": float(comments > 0),
        "author_association_rank": float(ASSOCIATION_RANK.get(assoc, 0)),
        "label_count": float(len(labels)),
        "has_severity_label": float(
            bool(
                re.search(
                    r"\b(p[0-4]|sev[0-4]|severity|priority|critical|blocker|"
                    r"urgent|high|medium|low)\b",
                    label_blob,
                )
            )
        ),
        "has_bug_label": float(bool(_BUG_RE.search(label_blob))),
        "has_feature_label": float(bool(_FEATURE_RE.search(label_blob))),
        "has_docs_label": float(bool(_DOCS_RE.search(label_blob))),
        "has_question_label": float(bool(_QUESTION_RE.search(label_blob))),
        "has_duplicate_label": float(bool(re.search(r"\bduplicate\b", label_blob))),
        "has_wontfix_label": float(bool(re.search(r"\bwontfix\b", label_blob))),
        "has_good_first_issue": float("good first issue" in label_blob),
        "length_chars": min(stats["length_chars"], 20000.0),
        "length_words": min(stats["length_words"], 4000.0),
        "length_sentences": min(stats["length_sentences"], 400.0),
        "unique_word_ratio": stats["unique_word_ratio"],
        "title_length": float(len(title)),
        "has_stacktrace": float(bool(_STACKTRACE_RE.search(text[:4000]))),
        "has_critical_language": float(bool(_CRITICAL_BODY_RE.search(text[:4000]))),
        "has_workaround": float(bool(_WORKAROUND_RE.search(text[:4000]))),
        "title_has_question": float("?" in title),
        "is_question_like": float(
            bool(
                re.search(
                    r"^\s*(how|why|what|when|where|which|can|could|is|are|does|do)\b",
                    title,
                    re.I,
                )
            )
        ),
    }


STRUCTURED_COLUMNS = list(
    structured_features(
        pd.Series(
            {
                "labels": [],
                "_text": "",
                "_title_text": "",
                "comments_count": 0,
                "author_association": None,
            }
        )
    ).keys()
)

# Features that are functions of the issue's own labels.
#
# The priority target is derived from those labels, so feeding any label-presence feature
# to the priority model would let it read the target directly — `has_feature_label` alone
# reproduced the FEATURE bucket of the label mapping and drove F1 to ~0.97. The priority
# model therefore trains without this group; the effort model keeps them, because its target
# (time-to-close) is independent of the labels.
LABEL_DERIVED_COLUMNS = [
    "has_severity_label",
    "has_bug_label",
    "has_feature_label",
    "has_docs_label",
    "has_question_label",
    "has_duplicate_label",
    "has_wontfix_label",
    "has_good_first_issue",
    "label_count",
]

PRIORITY_STRUCTURED_COLUMNS = [
    c for c in STRUCTURED_COLUMNS if c not in set(LABEL_DERIVED_COLUMNS)
]


def prepare_issue_frame(issues: pd.DataFrame, *, with_effort: bool = True) -> pd.DataFrame:
    """Clean text and derive all three label sets plus the effort target.

    Returns only rows where every required target is available — no imputation, no
    invented labels.
    """
    df = issues.copy()
    df["_title_text"] = df["title"].fillna("").map(clean_text)
    df["_text"] = [
        build_document(t, b) for t, b in zip(df["title"].fillna(""), df["body"].fillna(""))
    ]
    df["_text"] = df["_text"].str.slice(0, 8000)

    df["category"] = [
        label_category(_labels_of(row), row["_text"]) for _, row in df.iterrows()
    ]
    df["priority"] = [
        label_priority(_labels_of(row), row["_text"], int(row.get("comments_count") or 0))
        for _, row in df.iterrows()
    ]

    if with_effort and {"created_at", "closed_at"}.issubset(df.columns):
        created = pd.to_datetime(df["created_at"], unit="ms", utc=True, errors="coerce")
        closed = pd.to_datetime(df["closed_at"], unit="ms", utc=True, errors="coerce")
        # Fall back to ISO parsing for corpora that store strings.
        if created.isna().any():
            created = created.fillna(
                pd.to_datetime(df["created_at"], utc=True, errors="coerce")
            )
        if closed.isna().any():
            closed = closed.fillna(pd.to_datetime(df["closed_at"], utc=True, errors="coerce"))
        hours = (closed - created).dt.total_seconds() / 3600.0
        df["resolution_hours"] = hours
    elif with_effort and "resolution_hours" not in df.columns:
        df["resolution_hours"] = np.nan

    struct = pd.DataFrame(
        [structured_features(row) for _, row in df.iterrows()], index=df.index
    )
    # `struct` overlaps the corpus columns (`comments_count` exists in both), so a plain
    # concat produces a frame with duplicate column labels. Selecting by name later would
    # then return more columns than requested and the fitted scaler would disagree with
    # the inference frame. Keep the struct (engineered) value and drop the duplicate.
    df = pd.concat([df, struct], axis=1)
    df = df.loc[:, ~df.columns.duplicated(keep="last")]
    return df


def filter_labelled(
    df: pd.DataFrame, columns: list[str], *, min_words: int = 5
) -> pd.DataFrame:
    """Keep only rows with a derived label and enough text to learn from."""
    mask = df[columns].notna().all(axis=1)
    mask &= df["_text"].str.split().str.len() >= min_words
    return df[mask].reset_index(drop=True)
