"""
detect_02_timeline_reconstruction.py
─────────────────────────────────────────────────────────────────────────────
WHAT THIS DETECTS:
  Guardrail traces carry timestamps (startTime / endTime).
  The GAP between guardrail checkpoints tells us exactly how much time
  the routing classifier and the orchestration engine consumed.

  Gap Layout (4 guardrails per normal interaction):
  ┌─────────────────────────────────────────────────────────────────┐
  │ guardrail-pre-0  (supervisor entry)                             │
  │   ↕  ROUTING GAP  =  routing classifier LLM call time          │
  │ guardrail-pre-1  (sub-agent entry)                              │
  │   ↕  ORCHESTRATION GAP  =  all LLM + all AG calls combined     │
  │ guardrail-post-0 (sub-agent exit)                               │
  │   ↕  tiny                                                       │
  │ guardrail-post-1 (supervisor exit)                              │
  └─────────────────────────────────────────────────────────────────┘

HOW TO RUN:
  python detect_02_timeline_reconstruction.py

FINDING FROM AVA DATA:
  troubleshooting i2  → routing gap 12.6s  | orch gap 127.6s  ← worst
  defects_search  i1  → routing gap  4.2s  | orch gap  74.0s
  case_update     i1  → routing gap  2.4s  | orch gap  31.9s
"""
import json
from pathlib import Path
from datetime import datetime

TRACE_DIR = Path("/mnt/user-data/uploads")


def parse_dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def get_guardrails(trace_output: list) -> list[dict]:
    """Extract all guardrail steps with their timestamps."""
    grs = []
    for item in trace_output:
        if item.get("type") != "guardrail_trace":
            continue
        trace = item.get("trace", {})
        meta  = trace.get("metadata", {})
        if "startTime" not in meta:
            continue
        grs.append({
            "trace_id":  trace.get("traceId", ""),
            "start":     parse_dt(meta["startTime"]),
            "end":       parse_dt(meta["endTime"]) if "endTime" in meta else parse_dt(meta["startTime"]),
            "duration_ms": meta.get("totalTimeMs", 0),
        })
    return sorted(grs, key=lambda x: x["start"])


def analyze():
    print("\n" + "="*70)
    print("STEP 2 — TIMELINE RECONSTRUCTION FROM GUARDRAIL TIMESTAMPS")
    print("="*70)
    print("\nGap types:")
    print("  routing_gap   = time between guardrail[0].end and guardrail[1].start")
    print("                  → this is the routing classifier LLM call")
    print("  orch_gap      = time between guardrail[1].end and guardrail[2].start")
    print("                  → this is ALL orchestration: every LLM + every AG call")
    print()
    print(f"{'USE CASE':<28} {'INT':>4}  {'TOTAL':>7}  "
          f"{'ROUTING GAP':>12}  {'ORCH GAP':>11}  DIAGNOSIS")
    print("-"*100)

    rows = []
    for json_file in sorted(TRACE_DIR.glob("*.json")):
        with open(json_file) as f:
            traces = json.load(f)
        for i, trace in enumerate(traces, 1):
            out = trace.get("output", [])
            if not isinstance(out, list):
                continue
            grs = get_guardrails(out)
            if len(grs) < 3:
                routing_gap = orch_gap = 0.0
            else:
                routing_gap = max(0, (grs[1]["start"] - grs[0]["end"]).total_seconds())
                orch_gap    = max(0, (grs[2]["start"] - grs[1]["end"]).total_seconds())

            rows.append({
                "use_case":     json_file.stem,
                "i":            i,
                "total":        trace["latency"],
                "routing_gap":  routing_gap,
                "orch_gap":     orch_gap,
                "guardrail_ms": sum(g["duration_ms"] for g in grs),
            })

    rows.sort(key=lambda x: x["orch_gap"], reverse=True)

    for r in rows:
        # Diagnosis flag
        if r["orch_gap"] > 60:
            diag = "🔴  Very slow external API call or many LLM cycles"
        elif r["orch_gap"] > 20:
            diag = "🟠  Multiple LLM loops or a slow AG"
        elif r["routing_gap"] > 5:
            diag = "🟡  Routing classifier is slow"
        else:
            diag = "🟢  OK"

        print(
            f"{r['use_case']:<28} {r['i']:>4}  "
            f"{r['total']:>6.1f}s  "
            f"{r['routing_gap']:>11.2f}s  "
            f"{r['orch_gap']:>10.2f}s  "
            f"{diag}"
        )

    print()
    # How much of total latency is unexplained by guardrails?
    print("GUARDRAIL TIME vs TOTAL LATENCY (how much guardrails add):")
    by_uc: dict = {}
    for r in rows:
        uc = r["use_case"]
        by_uc.setdefault(uc, []).append(r)
    for uc, rlist in sorted(by_uc.items()):
        gr_pct = (sum(r["guardrail_ms"] for r in rlist) / 1000) / max(sum(r["total"] for r in rlist), 1) * 100
        print(f"  {uc:<28} guardrails = {gr_pct:.1f}% of total latency")


if __name__ == "__main__":
    analyze()
