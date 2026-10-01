"""Timing probe: which candidate dominates the defect-model cross-validation sweep?"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml"))

from features.defect_features import FEATURE_COLUMNS
from models.candidates import tabular_classifier_candidates
from preprocessing.build_datasets import load_or_extract_defect_dataset
from sklearn.impute import SimpleImputer
from sklearn.metrics import f1_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline

frame, _ = load_or_extract_defect_dataset()
frame = frame.sort_values("authored_at").reset_index(drop=True)
cut = int(len(frame) * 0.85)
train = frame.iloc[:cut]

X = train[FEATURE_COLUMNS].to_numpy(float)
y = train["is_defective"].to_numpy()
print(f"train={X.shape} positive_rate={y.mean():.4f}")

cv = StratifiedKFold(5, shuffle=True, random_state=42)
splits = list(cv.split(X, y))

for name, estimator in tabular_classifier_candidates().items():
    pipeline = Pipeline(
        [("impute", SimpleImputer(strategy="median")), ("clf", estimator)]
    )
    started = time.perf_counter()
    try:
        predictions = cross_val_predict(pipeline, X, y, cv=splits, n_jobs=1)
        score = f1_score(y, predictions, average="macro")
        print(
            f"  {name:<22} cv_f1_macro={score:.4f}  {time.perf_counter() - started:7.1f}s"
        )
    except Exception as exc:
        print(
            f"  {name:<22} FAILED {type(exc).__name__}: {exc}  "
            f"{time.perf_counter() - started:7.1f}s"
        )
