"""Print the real shape and size of the ingested datasets.

Every figure in the README's dataset section comes from here, so the documentation can be
regenerated rather than remembered. Read-only: it inspects `data/processed` and the raw
issue corpus and prints what is actually there.
"""

import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"


def load_commits() -> list[dict]:
    with (PROCESSED / "commits.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def truthy(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def main() -> int:
    commits = load_commits()
    print("=" * 70)
    print("DATASET INVENTORY  (all rows are real; nothing here is synthetic)")
    print("=" * 70)

    key = "repo" if "repo" in commits[0] else next(
        (k for k in commits[0] if "repo" in k.lower()), None
    )
    print(f"\ncommits.csv  rows={len(commits):,}  columns={len(commits[0])}")
    print(f"  columns: {', '.join(list(commits[0])[:10])}"
          f"{' …' if len(commits[0]) > 10 else ''}")

    if key:
        counts = collections.Counter(row[key] for row in commits)
        print("\n  commits per repository")
        for name, count in counts.most_common():
            print(f"    {name:<46} {count:>7,}")

    print(f"\n  merge commits      : {sum(truthy(r.get('is_merge')) for r in commits):,}")
    # `is_bugfix` is derived downstream, not stored in commits.csv, so re-derive it with
    # the pipeline's own regex. Reporting a column that does not exist would print a
    # confident 0 and imply the corpus contains no fix-intent commits.
    sys.path.insert(0, str(ROOT / "ml"))
    from preprocessing.clean_history import FIX_INTENT_RE, MERGE_RE

    fix_intent = sum(1 for r in commits if FIX_INTENT_RE.search(r.get("subject") or ""))
    merged_by_msg = sum(1 for r in commits if MERGE_RE.match(r.get("subject") or ""))
    stored_merges = sum(truthy(r.get("is_merge")) for r in commits)
    print(f"  fix-intent commits : {fix_intent:,}")
    # `is_merge` comes from the parent count and is the authoritative flag; the subject
    # regex misses squash/rebase merges and any merge with a custom subject, so the two
    # counts are expected to differ.
    print(f"  merges by parent count (authoritative) : {stored_merges:,}")
    print(f"  merges by 'merge'-prefixed subject      : {merged_by_msg:,}")
    print(f"    -> {stored_merges - merged_by_msg:,} merges carry a non-standard subject "
          f"(squash/rebase or custom wording)")

    changes_path = PROCESSED / "file_changes.csv"
    if changes_path.exists():
        with changes_path.open(encoding="utf-8", newline="") as handle:
            changes = list(csv.DictReader(handle))
        print(f"\nfile_changes.csv  rows={len(changes):,}  columns={len(changes[0])}")
        if changes:
            print(f"  columns: {', '.join(list(changes[0])[:8])}")
            adds = sum(int(float(r.get("additions") or 0)) for r in changes)
            dels = sum(int(float(r.get("deletions") or 0)) for r in changes)
            print(f"  line churn: +{adds:,} / -{dels:,}")

    meta_path = PROCESSED / "defect_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        print(f"\ndefect_meta.json  keys={list(meta)[:10]}")

    corpus = RAW / "github_issues.jsonl"
    if corpus.exists():
        lines = sum(1 for _ in corpus.open(encoding="utf-8"))
        size = corpus.stat().st_size / 1024 / 1024
        print(f"\ngithub_issues.jsonl  issues={lines:,}  ({size:.1f} MB)")
        first = json.loads(corpus.open(encoding="utf-8").readline())
        print(f"  fields: {', '.join(list(first)[:10])}")
        repos = collections.Counter()
        labels = collections.Counter()
        with corpus.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                url = str(row.get("html_url", ""))
                parts = url.split("/")
                if len(parts) > 4:
                    repos[f"{parts[3]}/{parts[4]}"] += 1
                for label in row.get("labels") or []:
                    name = label.get("name") if isinstance(label, dict) else label
                    if name:
                        labels[str(name)] += 1
        print("  issues per repository")
        for name, count in repos.most_common(10):
            print(f"    {name:<46} {count:>6,}")
        print(f"  distinct labels: {len(labels):,}")
        print("  top labels")
        for name, count in labels.most_common(8):
            print(f"    {name:<46} {count:>6,}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())