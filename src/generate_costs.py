"""Generate synthetic acquisition-cost data — Table 2 of 2.
Les do teh 2nd table
Grain: one row per month x acquisition_channel x segment.
Keyed to Table 1 so CAC reconciles with real new-customer counts.

Planted cross-table story:
  4. Efficiency trap: paid_social spend balloons while the customers it buys
     are low-value / fast-churning (Story 2) -> LTV:CAC for paid_social collapses.
     Requires JOINING Table 1 (value, churn) with Table 2 (spend) to see.
"""
import numpy as np
import pandas as pd

rng = np.random.default_rng(7)   # own seed, independent of Table 1

snap = pd.read_parquet("data/mrr_snapshots.parquet")
MONTHS = sorted(snap["month"].unique())

# --- derive REAL new-customer counts from Table 1 (guarantees reconciliation) ---
new_cust = (snap[snap["status"] == "new"]
            .groupby(["month", "acquisition_channel", "segment"])["customer_id"]
            .nunique()
            .reset_index(name="new_customers"))

# baseline cost assumptions per channel (cost to acquire ONE customer, roughly)
CHANNEL_BASE_CAC = {
    "organic":     200,    # cheap
    "referral":    350,
    "paid_search": 900,
    "outbound":   1500,
    "paid_social": 700,    # starts moderate — the trap raises it
}
# serving cost as a fraction of MRR (drives gross margin), per segment
SEG_SERVE_FRAC = {"SMB": 0.25, "Mid-Market": 0.20, "Enterprise": 0.15}

rows = []
for _, r in new_cust.iterrows():
    m, ch, seg, n = r["month"], r["acquisition_channel"], r["segment"], int(r["new_customers"])
    mi = MONTHS.index(m)

    base_cac = CHANNEL_BASE_CAC[ch]

    # ---- STORY 4: paid_social CAC balloons across Q1-Q2 2025 (months 12-17) ----
    if ch == "paid_social" and 12 <= mi <= 17:
        ramp = 1.0 + 0.35 * (mi - 11)        # +35% per month, compounding the waste
        base_cac *= ramp

    # marketing spend = per-customer CAC * customers acquired, with noise
    marketing_spend = base_cac * n * rng.uniform(0.9, 1.1)
    sales_spend = marketing_spend * rng.uniform(0.2, 0.4)   # sales is smaller slice

    # cost to serve this cohort's segment, tied to their revenue
    seg_mrr = snap[(snap.month == m) & (snap.acquisition_channel == ch)
                   & (snap.segment == seg)]["mrr"].sum()
    cost_to_serve = seg_mrr * SEG_SERVE_FRAC[seg] * rng.uniform(0.9, 1.1)

    rows.append({"month": m, "acquisition_channel": ch, "segment": seg,
                 "new_customers": n,
                 "marketing_spend": round(marketing_spend, 2),
                 "sales_spend": round(sales_spend, 2),
                 "cost_to_serve": round(cost_to_serve, 2)})

costs = pd.DataFrame(rows)
costs.to_parquet("data/acquisition_costs.parquet", index=False)

print(f"Wrote {len(costs):,} cost rows across {costs.month.nunique()} months")
print("\nCAC by channel over time (spend / new customers):")
cac = (costs.groupby(["month", "acquisition_channel"])
       .apply(lambda g: (g.marketing_spend.sum() + g.sales_spend.sum()) / max(g.new_customers.sum(), 1),
              include_groups=False)
       .unstack().round(0))
print(cac.tail(10).to_string())