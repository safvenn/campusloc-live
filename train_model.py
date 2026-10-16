"""Train an EXPERIMENTAL Wi-Fi floor classifier from a BSSID/RSSI pivot table.

Usage: python train_model.py --csv '/path/to/wifi1-data (1).csv'
Requires: pandas, numpy, scikit-learn, joblib

This does not repair mislabeled records; an irreducible label ambiguity warning
is included in the serialized model and in the report.
"""
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             classification_report, confusion_matrix, f1_score)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

SEED = 42


def candidates():
    return {
        "majority_baseline": DummyClassifier(strategy="most_frequent"),
        "knn_3": Pipeline([("scale", StandardScaler()), ("model", KNeighborsClassifier(n_neighbors=3, weights="distance"))]),
        "knn_7": Pipeline([("scale", StandardScaler()), ("model", KNeighborsClassifier(n_neighbors=7, weights="distance"))]),
        "logistic_balanced": Pipeline([("scale", StandardScaler()), ("model", LogisticRegression(max_iter=2000, class_weight="balanced", C=0.1))]),
        "rbf_svm_balanced": Pipeline([("scale", StandardScaler()), ("model", SVC(kernel="rbf", C=1, class_weight="balanced", probability=True, random_state=SEED))]),
        "random_forest": RandomForestClassifier(n_estimators=200, max_features="sqrt", min_samples_leaf=2, class_weight="balanced_subsample", random_state=SEED, n_jobs=-1),
        "extra_trees": ExtraTreesClassifier(n_estimators=250, max_features="sqrt", min_samples_leaf=2, class_weight="balanced", random_state=SEED, n_jobs=-1),
    }


def load_and_audit(path):
    data = pd.read_csv(path)
    if "floor" not in data:
        raise ValueError("CSV needs a 'floor' column")
    # Only BSSID-shaped columns can be used by the live scanning API.
    cols = sorted([c for c in data if len(c.split(":")) == 6 and all(len(s)==2 for s in c.split(":"))])
    if not cols:
        raise ValueError("No BSSID-named columns found")
    values = data[cols].apply(pd.to_numeric, errors="coerce")
    # Unified encoding for genuinely absent scans. Out-of-range sentinels are invalid.
    values = values.fillna(-110).clip(lower=-110, upper=0).astype(np.float32)
    floors = data["floor"].astype(str)
    if floors.isna().any():
        raise ValueError("Missing floor labels")
    # Hash only features, so contradictory labels have the same group.
    hashes = pd.util.hash_pandas_object(values, index=False).astype("uint64")
    temp = pd.DataFrame({"hash": hashes, "floor": floors})
    unique_labels = temp.groupby("hash")["floor"].nunique()
    size = temp.groupby("hash").size()
    majority_counts = temp.groupby(["hash", "floor"]).size().groupby(level=0).max()
    max_fit_rate = float(majority_counts.sum() / len(temp))
    conflict_hashes = unique_labels.index[unique_labels > 1]
    ambiguity = {
        "rows": len(data), "bssid_features": len(cols),
        "unique_fingerprints": int(unique_labels.size),
        "ambiguous_fingerprints": int(len(conflict_hashes)),
        "rows_with_ambiguous_fingerprints": int(size.loc[conflict_hashes].sum()),
        "best_possible_in_sample_accuracy_for_deterministic_mapping": round(max_fit_rate, 6),
        "floor_counts": {str(k): int(v) for k, v in floors.value_counts().items()},
        "removed_non_wifi_columns": [c for c in data if c not in cols and c != "floor"],
        "note": "Conflicting floor labels cannot be repaired from this pivoted file. Verify original survey recordings before production use.",
    }
    return values, floors, hashes, cols, ambiguity


def run(csv_path: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    X, y, groups, features, audit = load_and_audit(csv_path)
    # Outer split keeps identical fingerprints entirely in one partition.
    # Choose first split reproducibly. Test labels are not used to select a model.
    outer = StratifiedGroupKFold(n_splits=4, shuffle=True, random_state=SEED)
    train_idx, test_idx = next(outer.split(X, y, groups=groups))
    Xtr, Xte = X.iloc[train_idx], X.iloc[test_idx]
    ytr, yte = y.iloc[train_idx], y.iloc[test_idx]
    gtr = groups.iloc[train_idx]
    cv = StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=123)
    scores = {}
    for name, model in candidates().items():
        fold_scores = []
        fold_accuracy = []
        for ti, vi in cv.split(Xtr, ytr, gtr):
            model.fit(Xtr.iloc[ti], ytr.iloc[ti])
            p = model.predict(Xtr.iloc[vi])
            fold_scores.append(float(f1_score(ytr.iloc[vi], p, average="macro", zero_division=0)))
            fold_accuracy.append(float(accuracy_score(ytr.iloc[vi], p)))
        scores[name] = {"cv_macro_f1_mean": float(np.mean(fold_scores)),
                        "cv_macro_f1_folds": [round(v, 4) for v in fold_scores],
                        "cv_accuracy_mean": float(np.mean(fold_accuracy))}
    # Keep a genuine predictor; never deploy majority class baseline as 'best trained model'.
    viable = [name for name in scores if name != "majority_baseline"]
    selected = max(viable, key=lambda name: (scores[name]["cv_macro_f1_mean"], scores[name]["cv_accuracy_mean"]))
    model = candidates()[selected]
    model.fit(Xtr, ytr)
    pred = model.predict(Xte)
    labels = sorted(y.unique().tolist())
    heldout = {
        "outer_split_strategy": "StratifiedGroupKFold: identical fingerprints never split across train/test",
        "train_rows": int(len(train_idx)), "test_rows": int(len(test_idx)),
        "test_class_counts": {str(k):int(v) for k,v in yte.value_counts().items()},
        "accuracy": float(accuracy_score(yte, pred)),
        "macro_f1": float(f1_score(yte, pred, average="macro", zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(yte, pred)),
        "classification_report": classification_report(yte, pred, labels=labels, output_dict=True, zero_division=0),
        "confusion_matrix": confusion_matrix(yte, pred, labels=labels).tolist(),
        "labels": labels,
    }
    # Train final artifact on all available rows. Model remains experimental.
    model.fit(X, y)
    payload = {
        "model": model,
        "feature_names": features,
        "classes": [str(c) for c in model.classes_],
        "status": "EXPERIMENTAL_NOT_VALIDATED_FOR_REAL_WORLD_LOCATION",
        "uses_rssi_missing_value": -110.0,
        "selected_model": selected,
        "trained_utc": datetime.now(timezone.utc).isoformat(),
        "training_source": str(csv_path.name),
        "warning": audit["note"],
    }
    model_path = output_dir / "wifi_floor_experimental.pkl"
    joblib.dump(payload, model_path)
    report = {
        "label": "EXPERIMENTAL / NOT SAFE FOR REAL-WORLD FLOOR LOCATION USE",
        "dataset_audit": audit,
        "evaluation": heldout,
        "model_selection": scores,
        "selected_model": selected,
        "evaluation_limitation": "No independent correctly labeled survey sessions available. Even grouped-split performance is NOT real-world deployment validation.",
    }
    (output_dir / "evaluation.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("DATA AUDIT:", json.dumps(audit, indent=2))
    print("CV SCORES:", json.dumps(scores, indent=2))
    print("SELECTED MODEL:", selected)
    print("GROUPED HOLDOUT:", json.dumps({k:heldout[k] for k in ("train_rows", "test_rows", "accuracy", "macro_f1", "balanced_accuracy")}, indent=2))
    print("MODEL FILE:", model_path)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="wifi1-data (1).csv")
    parser.add_argument("--out", default="outputs")
    args = parser.parse_args()
    run(Path(args.csv), Path(args.out))
