"""Diagnostic: check whether the planted stories are actually detectable."""
import pandas as pd

snap = pd.read_parquet("data/mrr_snapshots.parquet")

# --- Overall: MRR, customer count, ARPU by month ---
monthly = snap.groupby("month").agg(
    mrr=("mrr", "sum"),
    customers=("customer_id", "nunique"),
).reset_index()
monthly["arpu"] = (monthly["mrr"] / monthly["customers"]).round(0)
print("=== OVERALL (last 12 months) ===")
print(monthly.tail(12).to_string(index=False))

# --- Story 1 check: Q3 2025 (Jun->Sep). Expect count UP, ARPU DOWN ---
print("\n=== STORY 1 (mix-shift): customers by segment ===")
seg = snap.groupby(["month", "segment"])["customer_id"].nunique().unstack()
print(seg.tail(10).to_string())

# --- Story 2 check: Q2 2025 churn by channel ---
print("\n=== STORY 2 (channel leak): active customers by channel ===")
ch = snap.groupby(["month", "acquisition_channel"])["customer_id"].nunique().unstack()
print(ch.tail(12).to_string())

# --- Story 3 check: Q4 2025. Expect Ent UP, SMB DOWN, MRR flat ---
print("\n=== STORY 3 (hidden war): MRR by segment ===")
segmrr = snap.groupby(["month", "segment"])["mrr"].sum().unstack().round(0)
print(segmrr.tail(8).to_string())