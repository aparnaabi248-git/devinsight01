"""Hybrid text ⊕ structured estimators.

The priority and effort models need two different views of the same issue: sparse TF-IDF
over its text, and a scaled block of numeric metadata (label severity, comment volume,
stack-trace detection, …). Concatenating them in one sparse matrix lets a single linear
model use both, and keeps the artefact a single object.

These are genuine scikit-learn estimators so they work with `cross_val_predict`,
`clone`, the model registry and `joblib` without special-casing.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.sparse import csr_matrix, hstack
from sklearn.base import BaseEstimator, ClassifierMixin, RegressorMixin, TransformerMixin
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import StandardScaler

WORD_NGRAMS = (1, 2)
CHAR_NGRAMS = (3, 5)
WORD_MIN_DF = 3
CHAR_MIN_DF = 5
MAX_WORD_FEATURES = 30000
MAX_CHAR_FEATURES = 20000


class _TextPlusStructured(BaseEstimator):
    """Shared TF-IDF(title) ⊕ scaled-structured fitting and transformation."""

    def __init__(
        self,
        estimator: Any = None,
        text_column: str = "_text",
        title_column: str = "_title_text",
        columns: list[str] | None = None,
    ):
        self.estimator = estimator
        self.text_column = text_column
        self.title_column = title_column
        self.columns = columns

    # ------------------------------------------------------------------ setup
    def _build_vectorisers(self) -> None:
        self.word_vectoriser_ = TfidfVectorizer(
            analyzer="word",
            ngram_range=WORD_NGRAMS,
            min_df=WORD_MIN_DF,
            sublinear_tf=True,
            strip_accents="unicode",
            max_features=MAX_WORD_FEATURES,
        )
        self.char_vectoriser_ = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=CHAR_NGRAMS,
            min_df=CHAR_MIN_DF,
            sublinear_tf=True,
            max_features=MAX_CHAR_FEATURES,
        )
        # The structured block is hstacked into a sparse matrix, which StandardScaler
        # cannot centre. with_mean=False is the documented remedy: the TF-IDF columns are
        # already zero-mean-ish, and the tree/linear candidates do not require centring.
        self.scaler_ = StandardScaler(with_mean=False)

    def _columns(self, X) -> list[str]:
        if self.columns is not None:
            return list(self.columns)
        if hasattr(X, "columns"):
            numeric = [c for c in X.columns if str(X[c].dtype.kind in "ifb")]
            if numeric:
                return numeric
        raise ValueError("no structured columns available; pass columns=...")

    # --------------------------------------------------------------- transform
    def _text_block(self, X, fit: bool) -> csr_matrix:
        if self.text_column in getattr(X, "columns", []):
            documents = X[self.text_column].astype(str).tolist()
        else:
            documents = [str(v) for v in X]
        if fit:
            word = self.word_vectoriser_.fit_transform(documents)
            char = self.char_vectoriser_.fit_transform(documents)
        else:
            word = self.word_vectoriser_.transform(documents)
            char = self.char_vectoriser_.transform(documents)
        return hstack([word, char], format="csr")

    def _sparse(self, X, fit: bool = False) -> csr_matrix:
        if fit:
            self._build_vectorisers()
            self.columns_ = self._columns(X)
        text = self._text_block(X, fit)
        structured = np.nan_to_num(
            X[self.columns_].to_numpy(dtype=float)
            if hasattr(X, "columns")
            else np.asarray(X, dtype=float)
        )
        scaled = (
            self.scaler_.fit_transform(structured)
            if fit
            else self.scaler_.transform(structured)
        )
        return hstack([text, csr_matrix(scaled)], format="csr")

    def _get_estimator(self):
        if self.estimator is None:
            raise ValueError("no base estimator was provided")
        return self.estimator


class HybridTextClassifier(ClassifierMixin, _TextPlusStructured):
    """TF-IDF ⊕ structured → calibrated linear classifier."""

    def fit(self, X, y):
        self._get_estimator().fit(self._sparse(X, fit=True), np.asarray(y))
        self.classes_ = getattr(self._get_estimator(), "classes_", np.unique(y))
        return self

    def predict(self, X):
        return self._get_estimator().predict(self._sparse(X))

    def predict_proba(self, X):
        return self._get_estimator().predict_proba(self._sparse(X))

    def decision_function(self, X):
        return self._get_estimator().decision_function(self._sparse(X))


class HybridTextRegressor(RegressorMixin, _TextPlusStructured):
    """TF-IDF ⊕ structured → regressor, trained on a `log1p`-transformed target.

    Time-to-close is heavy-tailed, so the model fits `log1p(hours)` and the transform is
    inverted at prediction time. Every reported metric is computed on the original scale.
    """

    def __init__(
        self,
        estimator: Any = None,
        text_column: str = "_text",
        title_column: str = "_title_text",
        columns: list[str] | None = None,
        clip_hours: float = 24 * 365,
    ):
        super().__init__(estimator, text_column, title_column, columns)
        self.clip_hours = clip_hours

    def fit(self, X, y):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._get_estimator().fit(
                self._sparse(X, fit=True),
                np.log1p(np.clip(np.asarray(y, dtype=float), 0, None)),
            )
        return self

    def predict(self, X) -> np.ndarray:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            raw = np.asarray(self._get_estimator().predict(self._sparse(X)), dtype=float)
        return np.expm1(np.clip(raw, 0.0, np.log1p(self.clip_hours)))


class Binarizer(TransformerMixin, BaseEstimator):
    """Map a probability to a discrete level using explicit thresholds."""

    def __init__(
        self, thresholds: dict[str, float] | None = None, order: list[str] | None = None
    ):
        self.thresholds = thresholds or {}
        self.order = order or []

    def fit(self, X, y=None):  # noqa: N803 - sklearn's signature
        return self

    def level(self, probability: float) -> str:
        for name, upper in self.thresholds.items():
            if probability < upper:
                return name
        return self.order[-1] if self.order else "UNKNOWN"
