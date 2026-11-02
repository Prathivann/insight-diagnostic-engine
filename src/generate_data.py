"""Generating synthetic SaaS MRR data with three planted stories.

Will be having 2 tables, now first generating the 1st table
Table 1 of 2: one row per active customer per month.
Stories planted:
  1. Mix-shift mirage  (Q3 2025): Enterprise churns, SMB floods -> MRR dips but count rises
  2. Single-channel leak (Q2 2025): paid_social customers churn hard
  3. Flat line hiding a war (Q4 2025): Enterprise grows, SMB collapses, net ~flat
"""
import numpy as np
import pandas as pd

rng = np.random.default_rng(42)   # fixed seed = everyone generates identical data

MONTHS = pd.period_range("2024-01", "2025-12", freq="M").astype(str).tolist()
SEGMENTS = ["SMB", "Mid-Market", "Enterprise"]
REGIONS = ["NA", "EMEA", "APAC"]
CHANNELS = ["organic", "paid_search", "paid_social", "referral", "outbound"]

SEG_MRR = {"SMB": 150, "Mid-Market": 800, "Enterprise": 4000}   # the "price" per segment
SEG_TIER = {"SMB": "Basic", "Mid-Market": "Pro", "Enterprise": "Enterprise"}


def make_customer(cid, segment, channel, region, start_idx):
    base = SEG_MRR[segment] * rng.uniform(0.8, 1.2)
    return {"customer_id": cid, "segment": segment, "region": region,
            "acquisition_channel": channel, "plan_tier": SEG_TIER[segment],
            "base_mrr": base, "start": start_idx}


# --- baseline population that grows steadily ---
customers = []
cid = 0
for _ in range(400):
    seg = rng.choice(SEGMENTS, p=[0.6, 0.3, 0.1])
    customers.append(make_customer(cid, seg, rng.choice(CHANNELS), rng.choice(REGIONS), 0))
    cid += 1

for mi, m in enumerate(MONTHS):
    n = rng.integers(25, 40)
    for _ in range(n):
        seg = rng.choice(SEGMENTS, p=[0.6, 0.3, 0.1])
        customers.append(make_customer(cid, seg, rng.choice(CHANNELS), rng.choice(REGIONS), mi))
        cid += 1

# --- churn schedule: each customer gets a lifespan (month index when they leave) ---
lifespans = {}
for c in customers:
    base_life = {"SMB": 14, "Mid-Market": 20, "Enterprise": 30}[c["segment"]]
    lifespans[c["customer_id"]] = c["start"] + max(1, int(rng.normal(base_life, 4)))

# ---- STORY 2: paid_social churns hard in Q2 2025 (months 15-17) ----
for c in customers:
    if c["acquisition_channel"] == "paid_social" and c["start"] <= 14:
        if rng.random() < 0.55:
            lifespans[c["customer_id"]] = int(rng.integers(15, 18))

# ---- STORY 1: Enterprise exodus + SMB flood in Q3 2025 (months 18-20) ----
for c in customers:
    if c["segment"] == "Enterprise" and c["start"] <= 17:
        if rng.random() < 0.30:
            lifespans[c["customer_id"]] = int(rng.integers(18, 21))
for mi in [18, 19, 20]:
    for _ in range(60):
        customers.append(make_customer(cid, "SMB", rng.choice(CHANNELS), rng.choice(REGIONS), mi))
        lifespans[cid] = mi + int(rng.integers(6, 14)); cid += 1

# ---- STORY 3: Q4 2025 (months 21-23) Enterprise grows, SMB collapses, net ~flat ----
for mi in [21, 22, 23]:
    for _ in range(4):          # was 12 — smaller Enterprise wave so it doesn't swamp
        customers.append(make_customer(cid, "Enterprise", rng.choice(CHANNELS), rng.choice(REGIONS), mi))
        lifespans[cid] = mi + int(rng.integers(8, 20)); cid += 1
for c in customers:
    if c["segment"] == "SMB" and c["start"] <= 20:
        if rng.random() < 0.45:     # was 0.35 — slightly stronger SMB collapse
            lifespans[c["customer_id"]] = min(lifespans[c["customer_id"]], int(rng.integers(21, 24)))

# --- emit one row per active customer per month ---
cust_lookup = {c["customer_id"]: c for c in customers}
rows = []
for cid_, c in cust_lookup.items():
    for mi in range(c["start"], min(lifespans[cid_], len(MONTHS))):
        mrr = c["base_mrr"] * (1 + 0.01 * (mi - c["start"]))   # slight monthly expansion
        status = "new" if mi == c["start"] else "active"
        rows.append({"customer_id": cid_, "month": MONTHS[mi],
                     "segment": c["segment"], "region": c["region"],
                     "acquisition_channel": c["acquisition_channel"],
                     "plan_tier": c["plan_tier"], "mrr": round(mrr, 2),
                     "status": status})

snap = pd.DataFrame(rows)
snap.to_parquet("data/mrr_snapshots.parquet", index=False)

print(f"Wrote {len(snap):,} rows, {snap.customer_id.nunique():,} customers, {snap.month.nunique()} months")
print("\nTotal MRR by month (last 10):")
print(snap.groupby("month")["mrr"].sum().round(0).tail(10))