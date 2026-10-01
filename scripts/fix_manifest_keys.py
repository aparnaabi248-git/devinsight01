"""One-off codemod: drop the redundant integer `cv_folds` from each manifest.

`"cv_folds": CV_FOLDS` (an int) shadowed the real `cv_folds` list written earlier in the
same dict literal, so the per-fold detail was silently discarded.
"""

import re
import sys
from pathlib import Path

PATTERN = re.compile(r'"seed": RANDOM_SEED, "cv_folds": CV_FOLDS,')
REPLACEMENT = '"seed": RANDOM_SEED, "n_cv_folds": CV_FOLDS,'

total = 0
for path in Path("ml/training").glob("train_*.py"):
    src = path.read_text(encoding="utf-8")
    new, n = PATTERN.subn(REPLACEMENT, src)
    if n:
        path.write_text(new, encoding="utf-8")
        print(f"  {path}: renamed {n} key(s) to n_cv_folds")
        total += n
print(f"done ({total} total)")
sys.exit(0)
