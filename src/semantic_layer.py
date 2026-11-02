"""Load and validate the semantic layer. Fail loudly if a metric references
something that doesn't exist — this file is the single source of metric truth.
"""
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def load_semantic_layer(path=None):
    path = path or (ROOT / "metrics.yaml")
    with open(path) as f:
        spec = yaml.safe_load(f)

    metrics = spec["metrics"]
    dims = spec["dimensions"]
    names = set(metrics)

    # --- validation: every reference must resolve ---
    problems = []
    for mname, m in metrics.items():
        kind = m.get("kind")
        if kind == "ratio":
            for k in ("numerator", "denominator"):
                if m[k] not in names:
                    problems.append(f"{mname}: {k} '{m[k]}' is not a defined metric")
        elif kind == "derived":
            for inp in m.get("inputs", []):
                if inp not in names:
                    problems.append(f"{mname}: input '{inp}' is not a defined metric")
        for d in m.get("dimensions", []):
            if d not in dims:
                problems.append(f"{mname}: dimension '{d}' is not in the dimension vocabulary")

    if problems:
        raise ValueError("Semantic layer validation failed:\n  - " + "\n  - ".join(problems))

    return spec

if __name__ == "__main__":
    spec = load_semantic_layer()
    print(f"Loaded {len(spec['metrics'])} metrics, {len(spec['dimensions'])} dimensions. All references resolve. \u2713\n")
    for name, m in spec["metrics"].items():
        kind = m["kind"]
        extra = {"ratio": lambda: f"{m['numerator']}/{m['denominator']}",
                 "derived": lambda: m["formula"],
                 "special": lambda: f"routine:{m['routine']}",
                 "simple": lambda: f"{m['agg']}({m['column']})"}[kind]()
        print(f"  {name:18s} [{kind:8s}] {extra}")