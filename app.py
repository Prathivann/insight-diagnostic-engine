"""Insight Diagnostic Engine — Streamlit UI.
PM-style metrics board (left, hero) + compact diagnostic pane (right).
  - Ask (chat): natural-language question -> parse -> diagnose -> narrate. Needs Groq key.
  - Guided (dropdowns): pick metric + periods. Works with NO API key.
The KPI board reflects the active comparison window set by either tab.
Theme: charcoal + muted gold ("terminal").
"""
import sys
from pathlib import Path
import streamlit as st
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from semantic_layer import load_semantic_layer
from engine import compute, diagnose, _all_months, _resolve_table

SPEC = load_semantic_layer()

if "range" not in st.session_state:
    st.session_state["range"] = None

LLM_OK = False
try:
    from llm import ask
    import os
    LLM_OK = bool(os.environ.get("GROQ_API_KEY"))
except Exception:
    LLM_OK = False

st.set_page_config(page_title="Insight Diagnostic Engine", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
  :root{
    --bg:#16181D; --panel:#1E2127; --card:#20242B; --ink:#E8E6E0; --muted:#8A9099;
    --line:#2E333C; --signal:#C9A24B; --warn:#D08A3E; --down:#D9736B; --up:#7FB88A;
  }
  .stApp{ background:var(--bg); color:var(--ink);
    font-family:"Inter","Segoe UI",system-ui,sans-serif; }
  /* kill Streamlit's default white header bar */
  header[data-testid="stHeader"]{ background:var(--bg) !important; }
  .idehead{ font-size:1.5rem; font-weight:700; letter-spacing:-0.02em; color:var(--ink); }
  .idesub{ color:var(--muted); font-size:0.92rem; margin-top:-2px; }

  .card{ background:var(--card); border:1px solid var(--line); border-radius:14px;
         padding:16px 18px; box-shadow:0 1px 3px rgba(0,0,0,.35); height:100%; }
  .card .metric-label{ color:var(--muted); font-size:0.82rem; font-weight:600;
         text-transform:none; }
  .card .bignum{ font-variant-numeric:tabular-nums; font-weight:700; font-size:1.9rem;
                 letter-spacing:-0.03em; line-height:1.1; margin-top:2px; color:var(--ink); }
  .delta-down{ color:var(--down); font-size:0.85rem; } .delta-up{ color:var(--up); font-size:0.85rem; }

  .panehead{ font-weight:650; font-size:1.02rem; margin-bottom:2px; color:var(--ink); }
  .step{ border-left:2px solid var(--line); padding:6px 0 6px 16px; margin-left:4px; }
  .step .slice{ font-weight:620; color:var(--ink); }
  .pill{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:0.72rem;
         font-variant-numeric:tabular-nums; border:1px solid var(--line); color:var(--muted); }
  .pill.signal{ color:var(--signal); border-color:var(--signal); }
  .pill.warn{ color:var(--warn); border-color:var(--warn); }
  .metric-label{ font-variant-numeric:tabular-nums; color:var(--muted); font-size:0.82rem; }
  .narration{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
              padding:14px 16px; font-size:0.98rem; line-height:1.55; color:var(--ink); }

  /* tabs */
  div[role="tablist"]{ gap:8px !important; border-bottom:none !important; }
  div[data-testid="stTab"]{
    background:var(--panel) !important; border:1px solid var(--line) !important;
    border-radius:10px 10px 0 0 !important; padding:8px 20px !important; }
  div[data-testid="stTab"] p{ font-size:1rem !important; font-weight:650 !important;
    color:var(--muted) !important; margin:0 !important; }
  div[data-testid="stTab"][aria-selected="true"]{
    background:var(--signal) !important; border-color:var(--signal) !important; }
  div[data-testid="stTab"][aria-selected="true"] p{ color:#16181D !important; }
  .react-aria-SelectionIndicator{ display:none !important; }

  /* primary button — gold with dark text */
  button[kind="primary"], button[data-testid="stBaseButton-primary"]{
    background:var(--signal) !important; border-color:var(--signal) !important;
    color:#16181D !important; font-weight:650 !important; }

  /* dropdowns / inputs dark */
  div[data-baseweb="select"] > div, div[data-baseweb="input"] > div{
    background:var(--panel) !important; border-color:var(--line) !important;
    color:var(--ink) !important; }
  div[data-baseweb="select"] *{ color:var(--ink) !important; }
  input{ color:var(--ink) !important; }
  label p, .stSelectbox label, .stTextInput label{ color:var(--muted) !important; }
  div[data-testid="stExpander"]{ border-color:var(--line) !important; }
  .stCode, pre{ background:var(--panel) !important; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="idehead">Insight Diagnostic Engine</div>', unsafe_allow_html=True)
st.markdown('<div class="idesub">From <em>what</em> changed to <em>why</em>. '
            'The engine computes; the numbers are verified.</div>', unsafe_allow_html=True)
st.write("")

# ---------------- helpers ----------------
METRIC_LABELS = {k: v.get("label", k) for k, v in SPEC["metrics"].items()}
TABLE_MONTHS = {t: _all_months(t) for t in SPEC["tables"]}

def months_for(metric): return TABLE_MONTHS[_resolve_table(metric)]

def period_options(metric):
    months = months_for(metric)
    years = sorted({m.split("-")[0] for m in months})
    return months + [f"{y}Q{q}" for y in years for q in (1,2,3,4)]

def fmt(metric, val):
    f = SPEC["metrics"][metric].get("format", "number")
    if val is None: return "—"
    if f == "currency": return f"${val:,.0f}"
    if f == "percent":  return f"{val*100:.1f}%"
    if f == "ratio":    return f"{val:.2f}×"
    if f == "integer":  return f"{val:,.0f}"
    return f"{val:,.1f}"

def sparkline(metric):
    months = months_for(metric)
    vals = [compute(metric, m) for m in months]
    fig = go.Figure(go.Scatter(x=months, y=vals, mode="lines",
                    line=dict(color="#C9A24B", width=2), fill="tozeroy",
                    fillcolor="rgba(201,162,75,0.10)",
                    hovertemplate="%{x}: %{y}<extra></extra>"))
    fig.update_layout(height=80, margin=dict(l=0,r=0,t=4,b=0),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(visible=False), yaxis=dict(visible=False))
    return fig

def metric_card(metric):
    months = months_for(metric)
    rng = st.session_state.get("range")
    if rng:
        pa, pb = rng
        try:
            latest, prev = compute(metric, pb), compute(metric, pa)
            caption = f"{pa} → {pb}"
        except Exception:
            latest, prev = compute(metric, months[-1]), compute(metric, months[-2])
            caption = "vs prior mo."
    else:
        latest, prev = compute(metric, months[-1]), compute(metric, months[-2])
        caption = "vs prior mo."
    delta = (latest or 0) - (prev or 0)
    dcls = "delta-down" if delta < 0 else "delta-up"
    sign = "" if delta < 0 else "+"
    st.markdown(f'<div class="card"><div class="metric-label">{METRIC_LABELS[metric]}</div>'
                f'<div class="bignum">{fmt(metric, latest)}</div>'
                f'<div class="{dcls}">{sign}{fmt(metric, delta)} {caption}</div></div>',
                unsafe_allow_html=True)
    st.plotly_chart(sparkline(metric), use_container_width=True,
                    config={"displayModeBar": False}, key=f"spark_{metric}")

def render_path(diag):
    node, depth = diag, 0
    while node:
        verdict = node.get("verdict")
        if depth == 0 and node.get("factors"):
            chips = "  ".join(
                f'<span class="pill {"warn" if f.get("pct_change",0)<0 else "signal"}">'
                f'{f["factor"]}: {f.get("pct_change"):+.1f}%</span>' for f in node["factors"])
            st.markdown(f'<div class="step">{chips}</div>', unsafe_allow_html=True)
        if verdict == "offsetting":
            ts = node.get("top_slice", {})
            st.markdown(f'<div class="step"><span class="slice">Flat headline — offsetting drivers</span> '
                        f'on <b>{node.get("dimension")}</b>: '
                        f'<span class="delta-up">{ts.get("value")} grew</span> while others fell.</div>',
                        unsafe_allow_html=True)
            break
        ts = node.get("top_slice")
        if ts and verdict not in ("broad_based","immaterial"):
            hhi = node.get("hhi")
            pill = f'<span class="pill signal">HHI {hhi:.2f}</span>' if hhi is not None else ""
            st.markdown(f'<div class="step"><span class="slice">{node.get("dimension")} = '
                        f'{ts.get("value")}</span> &nbsp;{pill} &nbsp;'
                        f'<span class="metric-label">{ts.get("pct_of_total_change"):+.0f}% of change</span></div>',
                        unsafe_allow_html=True)
        if verdict in ("broad_based","immaterial","leaf","max_depth"): break
        node = node.get("child"); depth += 1

# ---------------- layout ----------------
board, pane = st.columns([2.1, 1])

with board:
    st.markdown('<div class="panehead">Metrics</div>', unsafe_allow_html=True)
    row1 = st.columns(3)
    for col, metric in zip(row1, ["mrr", "customer_count", "arpu"]):
        with col: metric_card(metric)
    st.write("")
    row2 = st.columns(3)
    for col, metric in zip(row2, ["new_customers", "marketing_spend", "churned_customers"]):
        with col: metric_card(metric)

with pane:
    st.markdown('<div class="panehead">Diagnose</div>', unsafe_allow_html=True)
    tab_ask, tab_guided = st.tabs(["Ask", "Guided"])

    with tab_ask:
        if not LLM_OK:
            st.info("Ask needs a GROQ_API_KEY in your .env. Guided works without it.")
        else:
            q = st.text_input("Question", key="ask_q", label_visibility="collapsed",
                              placeholder="Why did MRR fall last quarter?")
            if st.button("Diagnose", key="ask_btn", type="primary") and q:
                with st.spinner("Parsing, computing, explaining…"):
                    story, parsed, diag = ask(q)
                st.session_state["range"] = (parsed["period_a"], parsed["period_b"])
                st.markdown(f'<div class="metric-label">Interpreted as: '
                            f'{METRIC_LABELS.get(parsed["metric"], parsed["metric"])} · '
                            f'{parsed["period_a"]} → {parsed["period_b"]}</div>', unsafe_allow_html=True)
                st.write("")
                st.markdown(f'<div class="narration">{story}</div>', unsafe_allow_html=True)
                st.write("")
                st.markdown('<div class="metric-label">Investigation path</div>', unsafe_allow_html=True)
                render_path(diag)

    with tab_guided:
        metric = st.selectbox("Metric", list(METRIC_LABELS),
                              format_func=lambda k: METRIC_LABELS[k], key="g_metric")
        opts = period_options(metric)
        pa = st.selectbox("From", opts, index=max(0, len(opts)-14), key="g_pa")
        pb = st.selectbox("To", opts, index=len(opts)-1, key="g_pb")
        if st.button("Diagnose", key="g_btn", type="primary"):
            st.session_state["range"] = (pa, pb)
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                diag = diagnose(metric, pa, pb)
            st.markdown('<div class="metric-label">Investigation path</div>', unsafe_allow_html=True)
            render_path(diag)
            with st.expander("Raw engine trace"):
                st.code(buf.getvalue() or "(no trace)")