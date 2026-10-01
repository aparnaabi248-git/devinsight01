"""CLEANING + TRANSFORMATION for real git history.

Nothing is invented: the defect label is *derived* from observable follow-up fixes in the
real history, and every temporal feature is computed with expanding windows over strictly
prior commits, which prevents future leakage.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from config import DEFECT_WINDOW_DAYS

# Fix-intent detection on real commit subjects (English + conventional-commit prefixes).
FIX_INTENT_RE = re.compile(
    r"(?:"
    r"^\s*(?:fix|hotfix|bugfix|patch|revert)\b[:(!]?"  # conventional commits
    r"|\b(?:fix(?:e[sd])?|bug|hotfix|bugfix|patch|regression|broken|crash|"
    r"exception|error|fault|defect|leak|segfault|deadlock|incorrect|wrong|"
    r"typo|off[- ]by[- ]one|npe|npe|issue)\b"
    r"|(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*#\d+"
    r"|#\d+"
    r")",
    re.IGNORECASE,
)
MERGE_RE = re.compile(r"^\s*merge\b", re.IGNORECASE)
DOC_EXT_RE = re.compile(r"\.(md|rst|txt|adoc|cfg|ini|toml|yaml|yml|json|lock)\s*$", re.I)
TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|spec|specs|testing|__tests__)(/|$)|(^|/)test_|_test\.|\.test\.", re.I
)
LOCKFILE_RE = re.compile(
    r"(package-lock\.json|yarn\.lock|poetry\.lock|Pipfile\.lock|"
    r"Cargo\.lock|composer\.lock|go\.sum)$",
    re.I,
)
VENDOR_RE = re.compile(
    r"(^|/)(node_modules|vendor|third_party|dist|build|\.git|"
    r"site-packages|__pycache__)(/|$)",
    re.I,
)
GENERATED_RE = re.compile(
    r"(\.min\.(js|css)$|\.lock$|\.pb\.go$|_pb2\.py$|\.generated\.|"
    r"\.map$|/static/)",
    re.I,
)

# Files whose churn carries no predictive signal about the author's intent.
LOW_SIGNAL_PATH_RE = re.compile(
    r"(^|/)(CHANGELOG|CHANGES|HISTORY|NEWS|AUTHORS|"
    r"\.gitattributes|\.gitignore)(/|\.|$)",
    re.I,
)


def is_fix_intent(message: str) -> bool:
    """True when a commit message plausibly announces a fix (the defect-signal proxy)."""
    if not message:
        return False
    subject = message.split("\n", 1)[0]
    if len(subject) > 300:  # long subjects are usually not fix announcements
        subject = subject[:300]
    return bool(FIX_INTENT_RE.search(subject))


def path_class(path: str) -> str:
    """Coarse file role, used both as a feature and as a noise filter."""
    p = path or ""
    if LOCKFILE_RE.search(p) or GENERATED_RE.search(p):
        return "generated"
    if VENDOR_RE.search(p):
        return "vendor"
    if TEST_PATH_RE.search(p):
        return "test"
    if DOC_EXT_RE.search(p):
        return "doc"
    if re.search(
        r"\.(py|js|ts|tsx|jsx|go|rs|java|rb|c|cc|cpp|h|hpp|cs|php|scala|kt|swift)$", p, re.I
    ):
        return "source"
    return "other"


def is_low_signal_path(path: str) -> bool:
    return bool(LOW_SIGNAL_PATH_RE.search(path or ""))


def normalise_author(row: pd.Series) -> str:
    """Prefer the GitHub login; fall back to a normalised email/name identity."""
    login = row.get("author_login")
    if isinstance(login, str) and login.strip():
        return login.strip().lower()
    email = row.get("author_email")
    if isinstance(email, str) and email and "@" in email and "users.noreply" not in email:
        return email.strip().lower()
    name = row.get("author_name")
    if isinstance(name, str) and name.strip():
        return name.strip().lower()
    return "unknown"


def clean_commits(commits: pd.DataFrame) -> pd.DataFrame:
    """Drop unusable rows and derive the message-level signals."""
    df = commits.copy()
    df = df.dropna(subset=["authored_at", "sha"])
    df = df[df["message"].notna() | df["subject"].notna()]
    df["authored_at"] = pd.to_datetime(df["authored_at"], utc=True, errors="coerce")
    df = df.dropna(subset=["authored_at"])
    df = df[df["authored_at"].dt.year >= 2005]  # Git launched 2008; guard against junk
    df = df[df["authored_at"] <= pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1)]

    df["author"] = df.apply(normalise_author, axis=1)
    df["message"] = df.get("message", df.get("subject", "")).fillna("")
    df["subject"] = df["message"].str.split("\n").str[0].str.slice(0, 300)
    df["is_merge"] = df.apply(
        lambda r: bool(r.get("is_merge")) or bool(MERGE_RE.match(r["subject"])), axis=1
    )
    df["is_fix_intent"] = df["message"].map(is_fix_intent)
    # A merge commit is a merge, not a bug announcement.
    df["is_bugfix"] = (df["is_fix_intent"] & ~df["is_merge"]).astype(int)
    for col, default in (("additions", 0), ("deletions", 0), ("files_changed", 0)):
        df[col] = pd.to_numeric(df.get(col), errors="coerce").fillna(default).clip(lower=0)
    df["net_lines"] = df["additions"] - df["deletions"]
    df["churn"] = df["additions"] + df["deletions"]
    df["log_additions"] = np.log1p(df["additions"])
    df["log_deletions"] = np.log1p(df["deletions"])
    df["log_churn"] = np.log1p(df["churn"])
    df["log_files"] = np.log1p(df["files_changed"])
    df["hour"] = df["authored_at"].dt.hour
    df["weekday"] = df["authored_at"].dt.weekday
    df["is_weekend"] = (df["weekday"] >= 5).astype(int)
    df["is_business_hours"] = ((df["hour"] >= 9) & (df["hour"] < 18)).astype(int)
    df["repo"] = df["repository"]
    return df.sort_values("authored_at").reset_index(drop=True)


def build_defect_labels(
    commits: pd.DataFrame, files: pd.DataFrame, window_days: int = DEFECT_WINDOW_DAYS
) -> pd.DataFrame:
    """Attach the `is_defective` proxy label by scanning forward in real history.

    For each commit C we look for a later commit F within `window_days` such that
    F.author != C.author, F is a fix-intent commit, and F touches >= 1 path that C also
    touched. This is the bug-inducing-change (BIC) construction from the JIT literature.
    """
    df = commits.sort_values("authored_at").reset_index(drop=True)
    if files is None or files.empty:
        df["is_defective"] = 0
        return df

    files = files.copy()
    files = files[~files["path"].map(is_low_signal_path)]
    files = files[~files["path"].map(lambda p: path_class(p) in {"generated", "vendor"})]
    if files.empty:
        df["is_defective"] = 0
        return df

    # path -> ordered [(timestamp, author)] of *fix-intent* commits only.
    fix_events: dict[str, list[tuple[pd.Timestamp, str]]] = {}
    sha_author = dict(zip(df["sha"], df["author"]))
    sha_time = dict(zip(df["sha"], df["authored_at"]))
    sha_fix = dict(zip(df["sha"], df["is_fix_intent"]))
    for sha, path in files[["sha", "path"]].itertuples(index=False):
        if not sha_fix.get(sha, False):
            continue
        fix_events.setdefault(path, []).append(
            (sha_time.get(sha), sha_author.get(sha, "unknown"))
        )
    for entries in fix_events.values():
        entries.sort(key=lambda t: t[0])

    window = pd.Timedelta(days=window_days)
    labels = np.zeros(len(df), dtype=np.int8)
    days_to_fix = np.full(len(df), np.nan)
    paths_of = {
        sha: set(files.loc[files["sha"] == sha, "path"].unique()) for sha in df["sha"].unique()
    }

    for idx, (sha, when, author) in enumerate(
        df[["sha", "authored_at", "author"]].itertuples(index=False)
    ):
        touched = paths_of.get(sha)
        if not touched:
            continue
        hit: pd.Timestamp | None = None
        for path in touched:
            for t, other_author in fix_events.get(path, ()):
                if t <= when:
                    continue
                if t > when + window:
                    break
                if other_author and other_author != author:
                    hit = t
                    break
            if hit is not None:
                break
        if hit is not None:
            labels[idx] = 1
            days_to_fix[idx] = (hit - when).total_seconds() / 86400.0

    df["is_defective"] = labels
    df["days_to_fix"] = days_to_fix
    return df
