from __future__ import annotations

import pandas as pd


ISSUE_TYPES = (
    "Wrong_Store_Transfer", "No_Reason_Adjustment",
    "Time_Travel_Count", "Double_Shrinkage_Transfer",
)


def _scores(tp: int, fp: int, tn: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"true_positive": tp, "false_positive": fp, "true_negative": tn,
            "false_negative": fn, "precision": precision, "recall": recall, "f1_score": f1}


def evaluate_detections(
    ground_truth: pd.DataFrame, detections: pd.DataFrame,
    movements: pd.DataFrame, cycle_counts: pd.DataFrame,
) -> pd.DataFrame:
    """Compare independently detected target/category pairs against isolated labels."""
    rows: list[dict[str, object]] = []
    for issue in ISSUE_TYPES:
        truth = ground_truth.loc[ground_truth.issue_type.eq(issue)]
        predicted = detections.loc[detections.issue_type.eq(issue)]
        truth_ids = set(zip(truth.entity_type, truth.target_id))
        predicted_ids = set(zip(predicted.entity_type, predicted.target_id))
        tp = len(truth_ids & predicted_ids)
        fp = len(predicted_ids - truth_ids)
        fn = len(truth_ids - predicted_ids)
        if issue == "Time_Travel_Count":
            population = len(cycle_counts)
        elif issue == "Wrong_Store_Transfer":
            population = int(movements.movement_type.eq("Transfer_Out").sum())
        elif issue == "No_Reason_Adjustment":
            population = int((movements.movement_type.eq("Adjustment") & movements.qty_change.lt(0)).sum())
        else:
            population = len(movements)
        score = _scores(tp, fp, max(0, population - tp - fp - fn), fn)
        rows.append({"issue_type": issue, "candidate_population": population, **score})
    overall_counts = {
        key: sum(int(row[key]) for row in rows)
        for key in ("true_positive", "false_positive", "true_negative", "false_negative")
    }
    rows.append({"issue_type": "Overall_Micro", "candidate_population": sum(r["candidate_population"] for r in rows),
                 **_scores(**{
                     "tp": overall_counts["true_positive"], "fp": overall_counts["false_positive"],
                     "tn": overall_counts["true_negative"], "fn": overall_counts["false_negative"],
                 })})
    return pd.DataFrame(rows, columns=["issue_type", "candidate_population", "true_positive",
        "false_positive", "true_negative", "false_negative", "precision", "recall", "f1_score"])
