# Insight Diagnostic Engine

Ask "why did this metric move?" and get a verified answer. A deterministic engine computes the driver decomposition; a thin LLM layer only translates the question and narrates the result. The LLM never does the math, so the numbers are always the engine's, not the model's guess.

This is a showcase project. The goal is to demonstrate how an analyst actually diagnoses a metric change: not just "revenue fell," but which lever moved it, which slice drove the lever, and when to stop digging.

## The problem

Every BI tool shows you *what* changed. Almost none of them tell you *why*. A dashboard says revenue dropped 8 percent, and then an analyst spends an hour slicing it by segment, region, channel, and cohort to find the cause. That manual investigation is repetitive, it happens every reporting cycle, and it is the same decomposition logic every time.

A general LLM looks like it could help, but it hallucinates numbers and writes subtly wrong SQL, then explains the wrong figure with total confidence. That is the worst failure mode for anything people make decisions from.

This project separates the two concerns cleanly:

- The **engine** does all computation. It is deterministic, it is tested against known answers, and it is the only thing that produces a number.
- The **LLM** only picks what to investigate from a trusted list of metrics, and narrates a result the engine already verified.

## How it works

Five layers, built bottom up:

1. **Data** - two reconciled SaaS tables (revenue snapshots and acquisition costs), 24 months, synthetic but with deliberately planted stories so the engine's answers can be checked against known ground truth.
2. **Semantic layer** - every metric defined once, correctly, in a single YAML file (formula, source table, grain, valid slice dimensions). The LLM can only pick metrics that exist here. It cannot invent one.
3. **Decomposition engine** - the analytical core. Computes any metric, decomposes a change two different ways, and recurses into the dominant driver under a set of guardrails.
4. **LLM layer** - two thin functions. `parse` turns a plain-English question into a metric and a time window. `narrate` turns the engine's verified findings into plain English.
5. **UI** - a Streamlit dashboard. A KPI board that responds to the selected window, plus two ways to diagnose: Ask (natural language) and Guided (dropdowns, works with no API key).

## The engine

The interesting part. It decomposes a change two ways, because they answer different questions.

**Factor decomposition - "which lever?"** Some metrics are products of others. MRR is customer count times ARPU. When MRR moves, the engine attributes how much came from the volume lever (count) versus the price lever (ARPU), using log decomposition so the two contributions add up exactly with no leftover interaction term. This is what catches a mix shift: customers can grow while revenue falls, because ARPU collapsed. A naive read would conclude the business is shrinking. It is not.

**Dimension decomposition - "which slice?"** The engine slices the change across each valid dimension (segment, region, channel) and measures where it concentrated, using contribution to change. Rate metrics like churn are not additive, so they cannot be sliced this way directly. For those, the engine decomposes the additive count underneath the rate (churned customers), which does sum across slices, and reports each slice's own rate for context.

**Knowing when to stop.** A decomposer that always points a finger is a liar half the time. Three guardrails decide whether a finding is real:

- **Materiality**: ignore changes below 2.5 percent of the base. No diagnosing rounding noise.
- **Drift**: compare the change against the metric's own historical monthly volatility (a z-score), so a normal wobble is not reported as an event.
- **Concentration**: the recursion is gated by the normalized Herfindahl-Hirschman Index of the contribution distribution. High HHI means one slice dominates, so drill in. Low HHI means the change is broad based, so say so and stop rather than inventing a single cause. HHI is computed on absolute contributions so that slices moving against the trend are counted by size, not cancelled out.

When the net change is immaterial but sub-slices are moving hard in opposite directions, the engine reports that too: a flat headline hiding a tug of war, which is exactly the kind of thing a dashboard watcher misses.

## The LLM layer, and a bug worth describing

The LLM does two jobs. It parses a question into structured intent, grounded on the semantic layer and constrained to valid JSON, then validated again in code so it cannot pass an unknown metric to the engine. And it narrates the engine's result.

Early on, the narrator fabricated a number. Asked to explain an MRR drop, it confidently cited a regional figure the engine had explicitly reported as broad based with no driver. The engine never produced that number. The model pattern matched "there should be a regional breakdown here" and made one up.

The fix was structural, not a polite request to behave. The narrator no longer receives the raw diagnosis tree. It receives a flat list of only the findings the engine actually stood behind, with the broad based and dead end tails removed before the model ever sees them. There is nothing left to embellish from. This is the whole point of the project in miniature: do not ask the model not to hallucinate, do not give it the material to hallucinate from.

## The test stories

The synthetic data has four planted patterns of increasing subtlety. They are the engine's test cases.

1. **Mix shift** - MRR falls while customer count rises, because expensive customers churned and cheap ones flooded in. Tests the volume versus price split.
2. **Single channel leak** - churn spikes, but it is concentrated in one acquisition channel while every other channel is stable. Tests whether the engine finds concentration instead of reporting a vague company wide problem.
3. **Hidden war** - total revenue looks flat, hiding one segment growing while another collapses. Tests whether the engine looks past a calm headline.
4. **Efficiency trap** - unit economics deteriorate because spend on a channel ballooned while the customers it bought were low value. Spans both tables. (See limitations.)

## What is not built yet

Being honest about scope:

- **Cross table metrics** (LTV to CAC ratio, Story 4) are defined in the semantic layer but the engine's table resolver does not yet handle a single metric that spans two tables with different grains. The components work; the composite diagnosis does not run end to end yet.
- **Cohort decomposition** (decomposing by acquisition vintage) is designed but not built. It is a different shape of analysis (a grid, not a point in time comparison) and deserves its own focused pass.
- **The data is synthetic.** The engine is validated against known ground truth, which is the right way to prove it works, but it has not been run against a real company's data.
- **Time savings are illustrative, not measured.** This automates the repetitive first pass of an investigation that an analyst would otherwise run by hand each cycle. Real savings would depend on deployment and are untested here.


## Tech stack

Python, pandas, DuckDB, PyYAML, Streamlit, Plotly, Pydantic, and Groq for the LLM (OpenAI compatible, model is swappable in one line). No fine tuning anywhere; the model is grounded on the semantic layer at run time, not retrained.

## Running it

You need Python 3.11 and a Groq API key (free tier) for the Ask mode. Guided mode works without a key.

```bash
# 1. create an environment and install
conda create -n insight-engine python=3.11 -y
conda activate insight-engine
pip install streamlit pandas numpy duckdb pyyaml plotly pydantic python-dotenv groq pytest

# 2. generate the synthetic data (the parquet files are not committed)
python src/generate_data.py
python src/generate_costs.py

# 3. add your Groq key for Ask mode
#    copy .env.example to .env and paste your key in
#    (Guided mode works without this step)

# 4. run
streamlit run app.py
```

Copy `.env.example` to `.env` and add your key:

```
GROQ_API_KEY=your_groq_api_key_here
```

## Author

Prithivan R