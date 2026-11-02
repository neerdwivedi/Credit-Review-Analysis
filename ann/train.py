"""Train the page classifier (TF-IDF + Keras MLP) and evaluate it on the
held-out test companies.

Two phases, both with a fixed seed:
1. Fit on the training companies minus VAL_COMPANIES; after every epoch,
   score the validation companies and keep the epoch with the best macro F1
   over pnl / balance_sheet.
2. Refit a fresh vectorizer + model on all training companies for that many
   epochs (statement pages are scarce, so the validation companies' pages are
   worth adding back), then evaluate once on the test companies.

Run from the project root after prepare_dataset.py and make_split.py:

    python ann/train.py

Writes to ann/model/: page_classifier.keras (with optimizer state, to resume
training), page_classifier_slim.keras (weights only, for prediction and
deployment), tfidf.pkl, metrics.txt, confusion_matrix.png and history.json
(per-epoch phase 1 curves, used by notebook.ipynb).
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")  # hide TensorFlow info logs

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.utils.class_weight import compute_class_weight  # noqa: E402

import keras  # noqa: E402

ANN_DIR = Path(__file__).resolve().parent
DATA_DIR = ANN_DIR / "_data"
MODEL_DIR = ANN_DIR / "model"

# Index order of the softmax output.
LABELS = ("pnl", "balance_sheet", "other")
STATEMENT_LABELS = ("pnl", "balance_sheet")

# Held out from phase 1 for epoch selection: one bank (Form A/B statements)
# and one NBFC group (Ind AS statements), never mixed with the fit companies.
VAL_COMPANIES = frozenset({"HDFC Bank", "Bajaj group"})

SEED = 42
MAX_FEATURES = 5000
MAX_EPOCHS = 40
PATIENCE = 8
BATCH_SIZE = 64


def build_vectorizer() -> TfidfVectorizer:
    return TfidfVectorizer(
        max_features=MAX_FEATURES,
        stop_words="english",
        lowercase=True,
        sublinear_tf=True,
        min_df=2,
        dtype=np.float32,
    )


def build_model(n_features: int) -> keras.Model:
    model = keras.Sequential(
        [
            keras.Input(shape=(n_features,)),
            keras.layers.Dense(256, activation="relu"),
            keras.layers.Dropout(0.3),
            keras.layers.Dense(128, activation="relu"),
            keras.layers.Dropout(0.3),
            keras.layers.Dense(len(LABELS), activation="softmax"),
        ]
    )
    model.compile(loss="categorical_crossentropy", optimizer="adam")
    return model


def encode(labels: pd.Series) -> np.ndarray:
    return labels.map({label: i for i, label in enumerate(LABELS)}).to_numpy()


def class_weights(y: np.ndarray) -> dict[int, float]:
    # "balanced": n_samples / (n_classes * class_count), so each class
    # contributes equally to the loss despite `other` being ~99% of pages
    classes = np.arange(len(LABELS))
    weights = compute_class_weight("balanced", classes=classes, y=y)
    return {int(c): float(w) for c, w in zip(classes, weights)}


def statement_f1(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Macro F1 over pnl and balance_sheet (the classes we care about)."""
    return float(
        f1_score(y_true, y_pred, labels=[LABELS.index(c) for c in STATEMENT_LABELS], average="macro", zero_division=0)
    )


class StatementF1Checkpoint(keras.callbacks.Callback):
    """Track validation statement F1 per epoch, keep the best weights, stop early."""

    def __init__(self, x_val: np.ndarray, y_val: np.ndarray, patience: int):
        super().__init__()
        self.x_val, self.y_val, self.patience = x_val, y_val, patience
        self.y_val_onehot = keras.utils.to_categorical(y_val, len(LABELS))
        self.best_f1, self.best_epoch, self.best_weights = -1.0, 0, None
        self.history: list[float] = []
        self.val_loss: list[float] = []  # unweighted, for the notebook's curve

    def on_epoch_end(self, epoch, logs=None):
        proba = self.model.predict(self.x_val, verbose=0)
        self.val_loss.append(float(np.mean(keras.losses.categorical_crossentropy(self.y_val_onehot, proba))))
        f1 = statement_f1(self.y_val, proba.argmax(axis=1))
        self.history.append(f1)
        if f1 > self.best_f1:
            self.best_f1, self.best_epoch = f1, epoch + 1
            self.best_weights = self.model.get_weights()
        elif epoch + 1 - self.best_epoch >= self.patience:
            self.model.stop_training = True


def fit_model(x: np.ndarray, y: np.ndarray, epochs: int, callbacks=()) -> tuple[keras.Model, keras.callbacks.History]:
    keras.utils.set_random_seed(SEED)
    model = build_model(x.shape[1])
    history = model.fit(
        x,
        keras.utils.to_categorical(y, len(LABELS)),
        epochs=epochs,
        batch_size=BATCH_SIZE,
        class_weight=class_weights(y),
        callbacks=list(callbacks),
        shuffle=True,
        verbose=0,
    )
    return model, history


def save_slim(model: keras.Model, path: Path) -> None:
    """Save architecture + weights only (no optimizer state): about a third of the size."""
    # from_config gives an uncompiled copy; clone_model would carry the optimizer over
    slim = model.__class__.from_config(model.get_config())
    slim.set_weights(model.get_weights())
    slim.save(path)


def predict(model: keras.Model, x: np.ndarray) -> np.ndarray:
    return model.predict(x, verbose=0).argmax(axis=1)


def report_block(title: str, y_true: np.ndarray, y_pred: np.ndarray) -> str:
    idx = list(range(len(LABELS)))
    cm = confusion_matrix(y_true, y_pred, labels=idx)
    width = max(len(label) for label in LABELS) + 2
    cm_lines = ["true \\ pred".ljust(width) + "".join(label.rjust(width) for label in LABELS)]
    for label, row in zip(LABELS, cm):
        cm_lines.append(label.ljust(width) + "".join(str(v).rjust(width) for v in row))
    return "\n".join(
        [
            f"--- {title} ---",
            classification_report(y_true, y_pred, labels=idx, target_names=LABELS, digits=3, zero_division=0),
            "Confusion matrix (rows = true, columns = predicted):",
            *cm_lines,
            "",
            f"Statement macro F1 (pnl, balance_sheet): {statement_f1(y_true, y_pred):.3f}",
            f"Accuracy: {accuracy_score(y_true, y_pred):.4f}",
            "",
        ]
    )


def save_confusion_png(y_true: np.ndarray, y_pred: np.ndarray, path: Path) -> None:
    cm = confusion_matrix(y_true, y_pred, labels=list(range(len(LABELS))))
    fig, ax = plt.subplots(figsize=(5.2, 4.4))
    ax.imshow(np.log1p(cm), cmap="Blues")  # log scale: `other` dwarfs the rest
    for i in range(len(LABELS)):
        for j in range(len(LABELS)):
            ax.text(j, i, str(cm[i, j]), ha="center", va="center", color="white" if cm[i, j] > cm.max() / 4 else "black")
    ax.set_xticks(range(len(LABELS)), LABELS)
    ax.set_yticks(range(len(LABELS)), LABELS)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title("Page classifier — held-out test companies")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--train", type=Path, default=DATA_DIR / "train.csv")
    parser.add_argument("--test", type=Path, default=DATA_DIR / "test.csv")
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    args = parser.parse_args()

    keras.utils.set_random_seed(SEED)
    try:
        import tensorflow as tf

        tf.config.experimental.enable_op_determinism()
    except Exception:  # older TF or a non-TF backend: seed alone still applies
        pass

    train = pd.read_csv(args.train, keep_default_na=False)
    test = pd.read_csv(args.test, keep_default_na=False)
    overlap = set(train["company"]) & set(test["company"])
    if overlap:
        print(f"Companies in both train and test: {sorted(overlap)}")
        return 1
    missing_val = VAL_COMPANIES - set(train["company"])
    if missing_val:
        print(f"VAL_COMPANIES not in train.csv: {sorted(missing_val)}")
        return 1

    is_val = train["company"].isin(VAL_COMPANIES)
    fit_df, val_df = train[~is_val], train[is_val]
    y_fit, y_val, y_train, y_test = (encode(d["label"]) for d in (fit_df, val_df, train, test))

    out: list[str] = []

    def emit(line: str = "") -> None:
        print(line)
        out.append(line)

    def counts(d: pd.DataFrame) -> str:
        c = d["label"].value_counts()
        return "  ".join(f"{label}={c.get(label, 0)}" for label in LABELS)

    emit("Page classifier: TF-IDF + Keras MLP (Dense 256 -> 128 -> 3)")
    emit(f"Seed {SEED}, max_features {MAX_FEATURES}, batch {BATCH_SIZE}, label order {list(LABELS)}")
    emit()
    emit(f"Phase 1 fit  ({fit_df['company'].nunique()} companies, {len(fit_df)} pages): {counts(fit_df)}")
    emit(f"Phase 1 val  ({val_df['company'].nunique()} companies: {', '.join(sorted(VAL_COMPANIES))}; {len(val_df)} pages): {counts(val_df)}")
    emit(f"Final train  ({train['company'].nunique()} companies, {len(train)} pages): {counts(train)}")
    emit(f"Test         ({test['company'].nunique()} companies: {', '.join(sorted(test['company'].unique()))}; {len(test)} pages): {counts(test)}")
    emit("Class weights (phase 1): " + ", ".join(f"{LABELS[k]}={v:.2f}" for k, v in class_weights(y_fit).items()))
    emit()

    # Phase 1: choose the epoch count on validation companies
    vectorizer = build_vectorizer()
    x_fit = vectorizer.fit_transform(fit_df["page_text"]).toarray()
    x_val = vectorizer.transform(val_df["page_text"]).toarray()
    checkpoint = StatementF1Checkpoint(x_val, y_val, PATIENCE)
    model, phase1 = fit_model(x_fit, y_fit, MAX_EPOCHS, callbacks=[checkpoint])
    best_epoch = checkpoint.best_epoch
    model.set_weights(checkpoint.best_weights)
    emit(f"Phase 1: ran {len(checkpoint.history)} epochs; best validation statement F1 "
         f"{checkpoint.best_f1:.3f} at epoch {best_epoch}")
    emit("Validation statement F1 by epoch: " + " ".join(f"{f:.2f}" for f in checkpoint.history))
    emit()
    emit(report_block("Validation companies (phase 1 model, best epoch)", y_val, predict(model, x_val)))

    # Phase 2: refit vectorizer + model on every training company for best_epoch epochs
    vectorizer = build_vectorizer()
    x_train = vectorizer.fit_transform(train["page_text"]).toarray()
    model, _ = fit_model(x_train, y_train, best_epoch)
    pred_test = predict(model, vectorizer.transform(test["page_text"]).toarray())

    emit(report_block("TEST: held-out companies (final model)", y_test, pred_test))
    base = (y_test == LABELS.index("other")).mean()
    emit(f"Why accuracy is not the headline: {base:.2%} of test pages are `other`, so a model that "
         f"never predicts a statement scores {base:.2%} accuracy with zero recall on pnl and "
         "balance_sheet. Per-class precision/recall/F1 show whether statements are actually found.")
    emit()

    rule = encode(test["auto_label"])
    emit(report_block("Baseline: keyword/line-item rules (auto_label) on the same test pages", y_test, rule))
    emit("Caveat: test labels were hand-checked starting from these rule labels, so this baseline "
         "is biased upward; it differs from the hand labels on only the corrected pages.")
    emit()

    test = test.assign(pred=[LABELS[i] for i in pred_test])
    wrong = test[(test["label"] != test["pred"])]
    emit(f"Test errors ({len(wrong)}):")
    for _, r in wrong.sort_values(["company", "page_number"]).iterrows():
        emit(f"  {r.company:<22} p{r.page_number:<4} true={r.label:<14} pred={r.pred}")

    args.model_dir.mkdir(parents=True, exist_ok=True)
    model.save(args.model_dir / "page_classifier.keras")
    save_slim(model, args.model_dir / "page_classifier_slim.keras")
    history = {
        "phase1_train_loss_weighted": [float(v) for v in phase1.history["loss"]],
        "phase1_val_loss": checkpoint.val_loss,
        "phase1_val_statement_f1": checkpoint.history,
        "best_epoch": best_epoch,
        "val_companies": sorted(VAL_COMPANIES),
    }
    (args.model_dir / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    vectorizer.stop_words_ = None  # dropped-terms set is only for inspection; keeps the pickle small
    with (args.model_dir / "tfidf.pkl").open("wb") as fh:
        pickle.dump(vectorizer, fh)
    (args.model_dir / "metrics.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
    save_confusion_png(y_test, pred_test, args.model_dir / "confusion_matrix.png")
    print(f"\nSaved model, vectorizer, metrics and confusion matrix to {args.model_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
