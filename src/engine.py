"""Decomposition engine — the analytical core (Phase 3a, with guardrails).

Capabilities:
  compute()                 — metric value for a period, optional slice & filters
  decompose_by_dimension()  — "which slice drove the change?" (additive metrics)
  decompose_rate_by_dimension() — rate metrics, via their additive base (counts)
  decompose_by_factors()    — "which lever?" (log-decomposition, e.g. MRR = count x ARPU)
  diagnose()                — orchestrator: gate -> factor -> dimension -> recurse,
                              with materiality, drift, HHI concentration, and
                              offsetting-driver detection.

Formula evaluation uses a safe AST walker (no eval): only +,-,*,/ over defined metrics.
"""
import ast
import operator
import numpy as np
import pandas as pd
from pathlib import Path
from semantic_layer import load_semantic_layer

ROOT = Path(__file__).resolve().parent.parent
SPEC = load_semantic_layer()

TABLES = {
    "mrr_snapshots":     pd.read_parquet(ROOT / "data" / "mrr_snapshots.parquet"),
    "acquisition_costs": pd.read_parquet(ROOT / "data" / "acquisition_costs.parquet"),
}

# ======================================================================
#  Safe formula evaluation (replaces eval)
# ======================================================================
_ALLOWED_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}

def safe_eval(formula, variables):
    def _ev(node):
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError(f"disallowed constant: {node.value!r}")
        if isinstance(node, ast.Name):
            if node.id in variables:
                return variables[node.id]
            raise ValueError(f"unknown variable: {node.id}")
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
            return _ALLOWED_OPS[type(node.op)](_ev(node.left), _ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
            return _ALLOWED_OPS[type(node.op)](_ev(node.operand))
        raise ValueError(f"disallowed expression: {ast.dump(node)}")
    return _ev(ast.parse(formula, mode="eval"))

# ======================================================================
#  Period handling
# ======================================================================
def _period_to_months(period):
    if "Q" in period:
        year, q = period.split("Q")
        q_months = {"1": ["01","02","03"], "2": ["04","05","06"],
                    "3": ["07","08","09"], "4": ["10","11","12"]}[q]
        return [f"{year}-{mm}" for mm in q_months]
    return [period]

def _period_filter(df, time_col, period):
    return df[df[time_col].isin(_period_to_months(period))]

def _apply_filters(df, filters):
    if filters:
        for dim, val in filters.items():
            col = SPEC["dimensions"][dim]["column"]
            df = df[df[col] == val]
    return df

# ======================================================================
#  Metric primitives
# ======================================================================
def _simple(m, df):
    if "filter" in m:
        df = df.query(m["filter"])
    col, agg = m["column"], m["agg"]
    return df[col].nunique() if agg == "nunique" else getattr(df[col], agg)()

def _churned_customer_set(df):
    """Customers present in an earlier month of the window but absent the next."""
    months = sorted(df["month"].unique())
    present = df.groupby("month")["customer_id"].apply(set)
    churned = set()
    for i in range(1, len(months)):
        prev = present.get(months[i-1], set()); cur = present.get(months[i], set())
        churned |= (prev - cur)
    return churned

def _monthly_churn(df):
    months = sorted(df["month"].unique())
    present = df.groupby("month")["customer_id"].apply(set)
    rates = []
    for i in range(1, len(months)):
        prev = present.get(months[i-1], set()); cur = present.get(months[i], set())
        if len(prev) > 0:
            rates.append(len(prev - cur) / len(prev))
    return float(np.mean(rates)) if rates else np.nan

def _churned_count(df):
    return len(_churned_customer_set(df))

def _ltv(df, gross_margin=0.80):
    arpu = df.groupby("customer_id")["mrr"].mean().mean()
    churn = _monthly_churn(df)
    return arpu * gross_margin / churn if churn and churn > 0 else np.nan

def compute_on(metric, sub):
    m = SPEC["metrics"][metric]; kind = m["kind"]
    if kind == "simple":
        return _simple(m, sub)
    if kind == "ratio":
        num = compute_on(m["numerator"], sub); den = compute_on(m["denominator"], sub)
        return num / den if den else np.nan
    if kind == "derived":
        vals = {inp: compute_on(inp, sub) for inp in m["inputs"]}
        return safe_eval(m["formula"], vals)
    if kind == "special":
        if m["routine"] == "monthly_churn": return _monthly_churn(sub)
        if m["routine"] == "ltv": return _ltv(sub, **m.get("params", {}))
        if m["routine"] == "churned_count": return _churned_count(sub)
    raise ValueError(f"unknown kind {kind}")

def _resolve_table(metric):
    m = SPEC["metrics"][metric]
    if "table" in m:
        return m["table"]
    ref = m.get("numerator") or m.get("inputs", [None])[0]
    return _resolve_table(ref)

def compute(metric, period, by=None, filters=None):
    table = _resolve_table(metric)
    df = _period_filter(TABLES[table], SPEC["tables"][table]["time_column"], period)
    df = _apply_filters(df, filters)
    if by is None:
        return compute_on(metric, df)
    col = SPEC["dimensions"][by]["column"]
    return df.groupby(col, group_keys=True).apply(
        lambda sub: compute_on(metric, sub), include_groups=False)

# ======================================================================
#  Decomposers
# ======================================================================
def decompose_by_dimension(metric, period_a, period_b, dimension, filters=None):
    """Additive metrics: slices sum to the total."""
    a = compute(metric, period_a, by=dimension, filters=filters)
    b = compute(metric, period_b, by=dimension, filters=filters)
    idx = a.index.union(b.index)
    a = a.reindex(idx).fillna(0); b = b.reindex(idx).fillna(0)
    change = b - a
    total = change.sum()
    out = pd.DataFrame({
        period_a: a.round(0), period_b: b.round(0), "change": change.round(0),
        "pct_of_total_change": (change / total * 100).round(1) if total else np.nan,
    }).sort_values("change", key=abs, ascending=False)
    return out, total

def decompose_rate_by_dimension(metric, period_a, period_b, dimension, filters=None):
    """Rate metrics (e.g. churn_rate): decompose the ADDITIVE BASE declared in
    `decompose_via` (e.g. churned_customer count), which does sum across slices,
    then also show each slice's own rate for context."""
    m = SPEC["metrics"][metric]
    base_metric = m["decompose_via"]            # an additive count metric
    a = compute(base_metric, period_a, by=dimension, filters=filters)
    b = compute(base_metric, period_b, by=dimension, filters=filters)
    idx = a.index.union(b.index)
    a = a.reindex(idx).fillna(0); b = b.reindex(idx).fillna(0)
    change = b - a
    total = change.sum()
    # each slice's own rate, for narration
    ra = compute(metric, period_a, by=dimension, filters=filters).reindex(idx)
    rb = compute(metric, period_b, by=dimension, filters=filters).reindex(idx)
    out = pd.DataFrame({
        f"{base_metric}_{period_a}": a.round(0),
        f"{base_metric}_{period_b}": b.round(0),
        "change": change.round(0),
        "pct_of_total_change": (change / total * 100).round(1) if total else np.nan,
        f"rate_{period_a}": ra.round(3),
        f"rate_{period_b}": rb.round(3),
    }).sort_values("change", key=abs, ascending=False)
    return out, total

def decompose_by_factors(metric, period_a, period_b, filters=None):
    m = SPEC["metrics"][metric]
    factors = m.get("decompositions", {}).get("factors")
    if not factors:
        return None, None
    va = compute(metric, period_a, filters=filters)
    vb = compute(metric, period_b, filters=filters)
    total_change = vb - va
    rows, log_contribs = [], {}
    for f in factors:
        fa = compute(f, period_a, filters=filters); fb = compute(f, period_b, filters=filters)
        log_contribs[f] = (np.log(fb) - np.log(fa)) if (fa > 0 and fb > 0) else np.nan
        rows.append({"factor": f, period_a: round(fa, 2), period_b: round(fb, 2),
                     "pct_change": round((fb/fa - 1)*100, 1) if fa else np.nan})
    total_log = sum(log_contribs.values())
    for r in rows:
        share = (log_contribs[r["factor"]] / total_log) if total_log else np.nan
        r["contribution_to_change"] = round(share * total_change, 0)
        r["pct_of_change"] = round(share * 100, 1)
    return pd.DataFrame(rows).set_index("factor"), round(total_change, 0)

# ======================================================================
#  Guardrails
# ======================================================================
def normalized_hhi(contributions):
    """HHI on absolute contribution shares, normalized to [0,1] for slice count.
    0 = perfectly even (broad-based); 1 = all change in one slice (concentrated)."""
    absc = contributions.abs()
    total = absc.sum()
    if total == 0:
        return np.nan
    shares = absc / total
    hhi = (shares ** 2).sum()
    n = len(shares)
    if n <= 1:
        return 1.0
    return (hhi - 1/n) / (1 - 1/n)

def materiality_ok(metric, period_a, period_b, filters=None, pct_floor=0.025):
    base = compute(metric, period_a, filters=filters)
    change = compute(metric, period_b, filters=filters) - base
    floor = abs(base) * pct_floor if base and not np.isnan(base) else 0.0
    return abs(change) >= floor, round(change, 4), round(floor, 4)

def _all_months(table):
    return sorted(TABLES[table]["month"].unique())

def drift_zscore(metric, period_a, period_b, filters=None):
    table = _resolve_table(metric)
    months = _all_months(table)
    series = pd.Series(
        {mo: compute(metric, mo, filters=filters) for mo in months}).sort_index()
    monthly_changes = series.diff().dropna()
    std = monthly_changes.std()
    end_a = _period_to_months(period_a)[-1]
    end_b = _period_to_months(period_b)[-1]
    span = max(1, months.index(end_b) - months.index(end_a))
    observed = compute(metric, period_b, filters=filters) - compute(metric, period_a, filters=filters)
    per_month = observed / span
    z = (per_month / std) if std and std > 0 else np.nan
    return z, std

def _is_rate(metric):
    """A metric that needs base-count decomposition rather than additive slicing."""
    return "decompose_via" in SPEC["metrics"][metric]

def find_offsetting_drivers(metric, period_a, period_b, filters=None, pct_floor=0.025):
    """When the NET change is immaterial, check whether sub-slices move materially
    in OPPOSING directions (a flat aggregate hiding a tug-of-war)."""
    m = SPEC["metrics"][metric]
    tbl = _resolve_table(metric)
    base_val = abs(compute(metric, period_a, filters=filters))
    slice_floor = base_val * pct_floor
    for dim in m.get("dimensions", []):
        if SPEC["dimensions"][dim]["table"] != tbl:
            continue
        if filters and dim in filters:
            continue
        if _is_rate(metric):
            dtbl, _ = decompose_rate_by_dimension(metric, period_a, period_b, dim, filters)
        else:
            dtbl, _ = decompose_by_dimension(metric, period_a, period_b, dim, filters)
        if dtbl.shape[0] < 2:
            continue
        up = dtbl[dtbl["change"] > slice_floor]["change"].sum()
        down = dtbl[dtbl["change"] < -slice_floor]["change"].sum()
        if up > slice_floor and abs(down) > slice_floor:
            return dim, dtbl, up, down
    return None

# ======================================================================
#  Orchestrator
# ======================================================================
def diagnose(metric, period_a, period_b, filters=None, depth=0, max_depth=3,
             pct_floor=0.025, drift_sigma=2.0, hhi_concentrated=0.25,
             hhi_broad=0.10):
    indent = "  " * depth
    scope = f" within {filters}" if filters else ""
    node = {"metric": metric, "scope": filters, "depth": depth}

    # --- GATE 1: materiality ---
    ok, change, floor = materiality_ok(metric, period_a, period_b, filters, pct_floor)
    print(f"{indent}[{metric}{scope}] change={change} (materiality floor +/-{floor})")
    if not ok:
        # Fix #3: before giving up, look for offsetting sub-drivers
        off = find_offsetting_drivers(metric, period_a, period_b, filters, pct_floor)
        if off:
            dim, dtbl, up, down = off
            print(f"{indent}  -> NET IMMATERIAL but OFFSETTING drivers on '{dim}':")
            print(f"{indent}     up +{up:,.0f} vs down {down:,.0f} (a flat top-line hiding a tug-of-war)")
            print(dtbl.to_string().replace("\n", "\n" + indent + "     "))
            node["verdict"] = "offsetting"; node["dimension"] = dim
            node["top_slice"] = {"value": dtbl.index[0], "change": float(dtbl.iloc[0]["change"])}
            return node
        print(f"{indent}  -> IMMATERIAL: change below {pct_floor:.1%} of base, no offsetting drivers. Stop.")
        node["verdict"] = "immaterial"
        return node

    # --- GATE 2: drift (informational) ---
    z, std = drift_zscore(metric, period_a, period_b, filters)
    if not np.isnan(z):
        tag = "UNUSUAL vs history" if abs(z) >= drift_sigma else "within normal monthly variation"
        print(f"{indent}  drift z={z:.2f} (typical monthly std={std:.3f}) -> {tag}")
        node["drift_z"] = round(float(z), 2)

    # --- FACTOR decomposition (additive metrics with a factor identity) ---
    ftbl, _ = decompose_by_factors(metric, period_a, period_b, filters)
    if ftbl is not None:
        print(f"{indent}  factors:")
        for fac, row in ftbl.iterrows():
            print(f"{indent}    {fac:16s} {row['pct_change']:+6.1f}%  "
                  f"contrib {row['contribution_to_change']:+,.0f}")
        node["factors"] = ftbl.reset_index().to_dict("records")

    # --- DIMENSION decomposition + HHI gate ---
    m = SPEC["metrics"][metric]
    tbl = _resolve_table(metric)
    rate = _is_rate(metric)
    best = None
    for dim in m.get("dimensions", []):
        if SPEC["dimensions"][dim]["table"] != tbl:
            continue
        if filters and dim in filters:
            continue
        if rate:
            dtbl, total = decompose_rate_by_dimension(metric, period_a, period_b, dim, filters)
        else:
            dtbl, total = decompose_by_dimension(metric, period_a, period_b, dim, filters)
        if dtbl.shape[0] < 2:          # Fix #1: skip single-value dimensions
            continue
        hhi = normalized_hhi(dtbl["change"])
        if best is None or (not np.isnan(hhi) and (np.isnan(best[1]) or hhi > best[1])):
            best = (dim, hhi, dtbl)

    if best is None:
        print(f"{indent}  (no further dimensions to decompose) Stop.")
        node["verdict"] = "leaf"
        return node

    dim, hhi, dtbl = best
    print(f"{indent}  best dimension = '{dim}' (normalized HHI={hhi:.2f})")
    top = dtbl.iloc[0]
    node["dimension"] = dim
    node["hhi"] = round(float(hhi), 3) if not np.isnan(hhi) else None
    node["top_slice"] = {"value": dtbl.index[0], "change": float(top["change"]),
                         "pct_of_total_change": float(top["pct_of_total_change"])}

    if not np.isnan(hhi) and hhi < hhi_broad:
        print(f"{indent}  -> BROAD-BASED (HHI<{hhi_broad}): no single driver dominates. Stop.")
        node["verdict"] = "broad_based"
        return node

    co = "" if (not np.isnan(hhi) and hhi >= hhi_concentrated) else "  (moderate: watch co-drivers)"
    print(f"{indent}  -> top driver: {dim}={dtbl.index[0]} "
          f"({top['pct_of_total_change']:+.0f}% of change){co}")

    # --- RECURSE ---
    if depth + 1 >= max_depth:
        print(f"{indent}  (max depth reached) Stop.")
        node["verdict"] = "max_depth"
        return node

    child_filters = dict(filters or {}); child_filters[dim] = dtbl.index[0]
    node["child"] = diagnose(metric, period_a, period_b, child_filters,
                             depth+1, max_depth, pct_floor, drift_sigma,
                             hhi_concentrated, hhi_broad)
    return node

# ======================================================================
#  Self-tests
# ======================================================================
if __name__ == "__main__":
    for title, (met, pa, pb) in {
        "STORY 1 — MRR drop (Jun->Sep 2025)":  ("mrr", "2025-06", "2025-09"),
        "STORY 2 — churn spike (Q1->Q2 2025)": ("churn_rate", "2025Q1", "2025Q2"),
        "STORY 3 — flat MRR (Q3->Q4 2025)":    ("mrr", "2025Q3", "2025Q4"),
    }.items():
        print("\n" + "=" * 64)
        print(title)
        print("=" * 64)
        diagnose(met, pa, pb)