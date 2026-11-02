"""Cross-table verification of Story 4: the efficiency trap.
LTV = ARPU * gross_margin / monthly_churn_rate  (textbook SaaS formula).
Churn is computed PER WINDOW so the Q2-2025 paid_social spike isn't diluted.
Joins Table 1 (value + churn) with Table 2 (spend) -> LTV:CAC by channel.
"""
import pandas as pd
import numpy as np

snap = pd.read_parquet("data/mrr_snapshots.parquet")
costs = pd.read_parquet("data/acquisition_costs.parquet")
GROSS_MARGIN = 0.80
MONTHS = sorted(snap["month"].unique())
midx = {m: i for i, m in enumerate(MONTHS)}

# ---------- helper: monthly churn rate for a subset of snapshot rows ----------
def monthly_churn_rate(df):
    """Fraction of active customers who disappear month-over-month, averaged."""
    present = df.groupby("month")["customer_id"].apply(set)
    present = present.reindex(MONTHS)  # align to full timeline
    rates = []
    for i in range(1, len(MONTHS)):
        prev, cur = present.iloc[i-1], present.iloc[i]
        if isinstance(prev, set) and len(prev) > 0:
            cur = cur if isinstance(cur, set) else set()
            churned = len(prev - cur)
            rates.append(churned / len(prev))
    return np.mean(rates) if rates else np.nan

# ---------- CAC by month x channel (Table 2) ----------
cac = (costs.groupby(["month", "acquisition_channel"])
       .agg(spend=("marketing_spend", "sum"),
            sales=("sales_spend", "sum"),
            new=("new_customers", "sum")).reset_index())
cac["CAC"] = (cac["spend"] + cac["sales"]) / cac["new"].clip(lower=1)

# ---------- Channel-level LTV = ARPU * margin / churn ----------
rows = []
for ch, g in snap.groupby("acquisition_channel"):
    arpu = g.groupby("customer_id")["mrr"].mean().mean()   # avg MRR per customer
    churn = monthly_churn_rate(g)
    ltv = arpu * GROSS_MARGIN / churn if churn and churn > 0 else np.nan
    rows.append({"acquisition_channel": ch, "ARPU": round(arpu),
                 "monthly_churn": round(churn, 3), "LTV": round(ltv)})
ltv_tbl = pd.DataFrame(rows).sort_values("LTV", ascending=False)
print("=== Channel LTV = ARPU x margin / churn (blended over all time) ===")
print(ltv_tbl.to_string(index=False))

# ---------- Q2-2025 COHORT proof: churn of customers acquired in Q2, by channel ----------
first_month = snap.groupby("customer_id")["month"].min()
q2_ids = first_month[first_month.isin(["2025-03","2025-04","2025-05","2025-06"])].index
q2 = snap[snap["customer_id"].isin(q2_ids)]
print("\n=== Q2-2025 acquisition cohort: churn & LTV by channel (the trap cohort) ===")
rows = []
for ch, g in q2.groupby("acquisition_channel"):
    arpu = g.groupby("customer_id")["mrr"].mean().mean()
    churn = monthly_churn_rate(g)
    ltv = arpu * GROSS_MARGIN / churn if churn and churn > 0 else np.nan
    rows.append({"channel": ch, "ARPU": round(arpu),
                 "monthly_churn": round(churn, 3), "cohort_LTV": round(ltv)})
print(pd.DataFrame(rows).sort_values("cohort_LTV").to_string(index=False))

# ---------- LTV:CAC over time (channel LTV / monthly CAC) ----------
cac = cac.merge(ltv_tbl[["acquisition_channel", "LTV"]], on="acquisition_channel")
cac["LTV_CAC"] = (cac["LTV"] / cac["CAC"]).round(2)
print("\n=== LTV:CAC ratio by channel over time (higher = healthier; <3 worry) ===")
print(cac.pivot(index="month", columns="acquisition_channel",
                values="LTV_CAC").tail(12).to_string())