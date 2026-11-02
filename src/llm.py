"""LLM layer — THIN by design. The model does two jobs and never touches math:
  parse()   : natural-language question -> {metric, period_a, period_b} (validated)
  narrate() : a verified diagnosis -> plain-English story (verified findings only)
  ask()     : end-to-end  question -> parse -> engine.diagnose -> narrate

The engine does all computation; the LLM only translates language <-> structure.
Model-agnostic: swap the client in one place. Default: Groq (OpenAI-compatible).
"""
import os
import io
import json
import contextlib
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq
from semantic_layer import load_semantic_layer
from engine import diagnose

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
SPEC = load_semantic_layer()

_client = Groq(api_key=os.environ["GROQ_API_KEY"])
MODEL = "openai/gpt-oss-120b"   # swappable; most capable general chat model on this account

def _chat(system, user, json_mode=False):
    """Single swappable entry point to the model. Change provider here only."""
    kwargs = {"model": MODEL, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user}]}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = _client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content

# ----------------------------------------------------------------------
#  parse: question -> structured intent (grounded on the semantic layer)
# ----------------------------------------------------------------------
def _metric_catalog():
    """Compact description of valid metrics, injected so the model can only
    choose real ones (grounding, not fine-tuning)."""
    return "\n".join(
        f"- {name}: {m.get('label', name)} — {m.get('description','')}"
        for name, m in SPEC["metrics"].items())

PARSE_SYSTEM = """You translate a business question into a JSON object describing
what to diagnose. You MUST choose a metric from the provided catalog — never invent one.

Return ONLY a JSON object with exactly these keys:
  "metric": one of the catalog metric names (the short id, e.g. "mrr", "churn_rate")
  "period_a": the earlier period, format "YYYY-MM" or "YYYYQn" (e.g. "2025Q2")
  "period_b": the later period, same format
  "note": a one-line restatement of what the user asked

Rules:
- "this quarter vs last" or vague phrasing: choose the two most recent consecutive
  periods that make sense; prefer quarters (YYYYQn) unless a month is named.
- If the user names a metric by a friendly name (e.g. "revenue"), map it to the closest
  catalog id (revenue -> mrr).
- Output JSON only. No prose, no backticks."""

def parse(question):
    user = f"Metric catalog:\n{_metric_catalog()}\n\nUser question: {question}"
    raw = _chat(PARSE_SYSTEM, user, json_mode=True)
    data = json.loads(raw)
    if data.get("metric") not in SPEC["metrics"]:
        raise ValueError(f"LLM picked unknown metric: {data.get('metric')!r}")
    return data

# ----------------------------------------------------------------------
#  summarize_findings: extract ONLY verified, concrete findings
# ----------------------------------------------------------------------
def summarize_findings(diag):
    """Walk the diagnosis tree and extract only verified, concrete findings —
    omitting broad-based / leaf / max-depth tails so the narrator can't embellish
    beyond what the engine concluded. Returns a flat list of dicts."""
    findings = []

    def walk(node, depth=0):
        verdict = node.get("verdict")
        if depth == 0 and node.get("factors"):
            findings.append({
                "type": "factors",
                "metric": node["metric"],
                "factors": [{"factor": f["factor"], "pct_change": f.get("pct_change")}
                            for f in node["factors"]],
            })
        if verdict == "offsetting":
            ts = node.get("top_slice", {})
            findings.append({
                "type": "offsetting",
                "metric": node["metric"],
                "dimension": node.get("dimension"),
                "grew_slice": ts.get("value"),
                "grew_change": ts.get("change"),
            })
            return
        ts = node.get("top_slice")
        if ts and verdict not in ("broad_based", "immaterial"):
            findings.append({
                "type": "driver",
                "metric": node["metric"],
                "dimension": node.get("dimension"),
                "slice": ts.get("value"),
                "pct_of_total_change": ts.get("pct_of_total_change"),
                "depth": depth,
            })
        child = node.get("child")
        if child and verdict not in ("broad_based", "immaterial", "leaf", "max_depth"):
            walk(child, depth + 1)

    walk(diag)
    return findings

# ----------------------------------------------------------------------
#  narrate: verified findings -> plain-English explanation
# ----------------------------------------------------------------------
NARRATE_SYSTEM = """You are a precise analyst explaining why a metric moved, for a
product manager. You are given a list of VERIFIED FINDINGS computed by a
deterministic engine.

ABSOLUTE RULES:
- Use ONLY the findings provided. Never state a number, percentage, segment, region,
  or channel that is not explicitly in the findings list.
- Never invent, estimate, or recompute any figure.
- Do NOT mention anything about regions or sub-levels that are not in the findings.
- If a "factors" finding shows one factor rising while the metric fell, call that out
  as a mix/price effect (e.g. "customers grew but revenue fell because ARPU dropped").
- If there is an "offsetting" finding, explain it as a flat headline hiding a segment
  growing while others shrank.
- 3-5 sentences, plain English, no bullet lists. Lead with the headline finding.
- Stop when the findings stop. Do not speculate about deeper causes."""

def narrate(diag, question=None):
    findings = summarize_findings(diag)
    payload = json.dumps(findings, default=str, indent=2)
    user = (f"User asked: {question}\n\n" if question else "") + \
           f"Verified findings (use ONLY these):\n{payload}"
    return _chat(NARRATE_SYSTEM, user)

# ----------------------------------------------------------------------
#  ask: end-to-end pipeline
# ----------------------------------------------------------------------
def ask(question, verbose=False):
    """Natural-language question -> parsed intent -> verified engine diagnosis
    -> plain-English narration. Returns (narration, parsed_intent, diagnosis)."""
    parsed = parse(question)
    metric, pa, pb = parsed["metric"], parsed["period_a"], parsed["period_b"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        diag = diagnose(metric, pa, pb)
    trace = buf.getvalue()
    if verbose:
        print(trace)
    story = narrate(diag, question)
    return story, parsed, diag

# ----------------------------------------------------------------------
#  quick live test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    questions = [
        "Why did MRR fall from Q2 to Q3 2025?",
        "What happened to churn between Q1 and Q2 2025?",
        "Was Q4 2025 revenue really flat?",
    ]
    for q in questions:
        print("\n" + "=" * 70)
        print("Q:", q)
        print("=" * 70)
        story, parsed, diag = ask(q, verbose=True)
        print("\n--- NARRATION ---")
        print(story)