"""Model definitions: candidate pipelines per task, all from sklearn/XGBoost."""

from __future__ import annotations

from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import (
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC

# Candidate model factories. Every train script sweeps these and selects on real CV.


def tabular_classifier_candidates() -> dict[str, object]:
    """Candidate classifiers for the defect-risk model (binary, tabular).

    Tree counts are sized so a 5-fold CV sweep over ~20k rows completes in minutes on a
    laptop — enough capacity to be competitive, cheap enough for CI and Docker.
    """
    candidates: dict[str, object] = {
        "logistic_regression": Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        max_iter=2000, C=1.0, class_weight="balanced", solver="lbfgs"
                    ),
                ),
            ]
        ),
        "random_forest": RandomForestClassifier(
            n_estimators=150,
            max_depth=20,
            min_samples_leaf=5,
            max_features="sqrt",
            class_weight="balanced_subsample",
            n_jobs=-1,
            random_state=42,
        ),
        "gradient_boosting": GradientBoostingClassifier(
            n_estimators=150,
            learning_rate=0.08,
            max_depth=3,
            subsample=0.9,
            random_state=42,
        ),
    }
    try:  # XGBoost is the strongest tabular baseline but an optional dependency
        from xgboost import XGBClassifier

        candidates["xgboost"] = XGBClassifier(
            n_estimators=250,
            learning_rate=0.08,
            max_depth=4,
            subsample=0.9,
            colsample_bytree=0.8,
            min_child_weight=4,
            reg_lambda=2.0,
            eval_metric="logloss",
            tree_method="hist",
            random_state=42,
            n_jobs=4,
        )
    except ImportError:  # pragma: no cover
        pass
    return candidates


def text_classifier_candidates() -> dict[str, Pipeline]:
    """TF-IDF -> linear models for the issue category and priority classifiers.

    Word n-grams capture vocabulary; character n-grams capture morphology, misspellings
    and code identifiers, so both views are combined. They must run in *parallel* over the
    raw documents — a `Pipeline` would feed the first vectoriser's sparse matrix into the
    second, hence `FeatureUnion`.
    """
    features = FeatureUnion(
        [
            (
                "word",
                TfidfVectorizer(
                    analyzer="word",
                    ngram_range=(1, 2),
                    min_df=2,
                    max_df=0.85,
                    sublinear_tf=True,
                    strip_accents="unicode",
                    lowercase=True,
                    max_features=60000,
                ),
            ),
            (
                "char",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    min_df=3,
                    max_features=40000,
                    sublinear_tf=True,
                ),
            ),
        ]
    )
    return {
        "tfidf_logreg": Pipeline(
            [
                ("features", features),
                ("clf", LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced")),
            ]
        ),
        "tfidf_linear_svc": Pipeline(
            [
                ("features", clone(features)),
                (
                    "clf",
                    CalibratedClassifierCV(
                        LinearSVC(C=0.5, class_weight="balanced"), cv=3, method="sigmoid"
                    ),
                ),
            ]
        ),
    }


def regression_candidates() -> dict[str, object]:
    """Candidate regressors for the effort model.

    These are *base* estimators: the `log1p` target transform lives in the
    `HybridTextRegressor` wrapper, so it must not be duplicated here.
    """
    return {
        "ridge": Pipeline(
            [
                # with_mean=False: this pipeline is fit on a sparse hstack.
                ("scale", StandardScaler(with_mean=False)),
                ("reg", Ridge(alpha=1.0)),
            ]
        ),
        "random_forest": RandomForestRegressor(
            n_estimators=150,
            min_samples_leaf=5,
            max_features="sqrt",
            n_jobs=-1,
            random_state=42,
        ),
        "gradient_boosting": GradientBoostingRegressor(
            n_estimators=150,
            learning_rate=0.08,
            max_depth=3,
            subsample=0.9,
            random_state=42,
        ),
    }
