"""
detect_05_guardrail_and_routing_overhead.py
─────────────────────────────────────────────────────────────────────────────
WHAT THIS DETECTS:
  1. Guardrail overhead — 4 guardrail checks run per interaction
     (pre+post at supervisor level, pre+post at sub-agent level).
     Each adds ~600–900ms. Combined: 2.5–3.5s constant floor.

  2. Routing classifier cost — separate LLM call to decide which
     sub-agent handles the query. Adds 1.5–13s per interaction.
     On multi-turn sessions this fires every turn — even for follow-ups
     like "yes, proceed" where the sub-agent is already obvious.

  3. Anomalous guardrail spikes — single guardrail that takes >1000ms
     signals Bedrock service load or oversized payload.

HOW TO RUN:
  python detect_05_guardrail_and_routing_overhead.py

FINDING FROM AVA DATA:
  All interactions: 4 guardrails × ~700ms avg = ~2.8s constant overhead
  Routing gap (troubleshooting i2):  12.6s  ← LLM classifier is slow here
  Routing gap (defects_search i1):    4.2s
  Anomaly: firmware_recommendation i1 post-guardrail = 1,570ms (2× normal)
"""
import json
from pathlib import Path
from datetime import datetime

TRACE_DIR = Path("/mnt/user-data/uploads")

GUARDRAIL_SLOW_MS   = 1000   # flag if single guardrail > 1s
ROUTING_SLOW_SEC    = 3.0    # flag routing gap > 3s
ROUTING_CRIT_SEC    = 8.0    # critical routing gap


def parse_dt(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def extract_guardrails(trace_output: list) -> list[dict]:
    grs = []
    for item in trace_output:
        if item.get("type") != "guardrail_trace":
            continue
        tr   = item.get("trace", {})
        meta = tr.get("metadata", {})
        grs.append({
            "trace_id":     tr.get("traceId", ""),
            "subtype":      "pre" if "pre" in tr.get("traceId", "") else "post",
            "duration_ms":  meta.get("totalTimeMs", 0),
            "start":        parse_dt(meta["startTime"]) if "startTime" in meta else None,
            "end":          parse_dt(meta["endTime"])   if "endTime"   in meta else None,
        })
    return sorted(grs, key=lambda x: x["start"] or datetime.min)


def analyze():
    print("\n" + "="*70)
    print("STEP 5 — GUARDRAIL OVERHEAD & ROUTING CLASSIFIER COST")
    print("="*70)

    all_guardrails = []  # flat list across all traces
    routing_gaps   = []  # (use_case, i, routing_gap_sec)

    for json_file in sorted(TRACE_DIR.glob("*.json")):
        with open(json_file) as f:
            traces = json.load(f)
        for i, trace in enumerate(traces, 1):
            out = trace.get("output", [])
            if not isinstance(out, list):
                continue
            grs = extract_guardrails(out)
            for g in grs:
                g["use_case"]    = json_file.stem
                g["interaction"] = i
                all_guardrails.append(g)

            # Routing gap: guardrail[0].end → guardrail[1].start
            valid = [g for g in grs if g["start"] and g["end"]]
            if len(valid) >= 2:
                routing_gap = max(0, (valid[1]["start"] - valid[0]["end"]).total_seconds())
                routing_gaps.append({
                    "use_case":    json_file.stem,
                    "i":           i,
                    "total_lat":   trace["latency"],
                    "routing_gap": routing_gap,
                })

    # ── Guardrail duration stats ───────────────────────────────────────────
    print("\n📊 GUARDRAIL DURATION STATS (all interactions):")
    measured = [g["duration_ms"] for g in all_guardrails if g["duration_ms"] > 0]
    if measured:
        print(f"  Count  : {len(measured)} guardrail checks")
        print(f"  Average: {sum(measured)/len(measured):.0f} ms")
        print(f"  Min    : {min(measured):.0f} ms")
        print(f"  Max    : {max(measured):.0f} ms")
        print(f"  Total guardrail time (all interactions): {sum(measured)/1000:.1f}s")

    # Per-interaction guardrail totals
    print("\n  Per-interaction breakdown:")
    print(f"  {'USE CASE':<28} {'INT':>4}  {'PRE(ms)':>8}  {'POST(ms)':>9}  {'TOTAL(ms)':>10}  NOTE")
    print("  " + "-"*75)

    by_interaction: dict = {}
    for g in all_guardrails:
        key = (g["use_case"], g["interaction"])
        by_interaction.setdefault(key, []).append(g)

    for (uc, i), grs in sorted(by_interaction.items()):
        pre_ms  = sum(g["duration_ms"] for g in grs if g["subtype"] == "pre")
        post_ms = sum(g["duration_ms"] for g in grs if g["subtype"] == "post")
        total   = pre_ms + post_ms
        notes   = []
        for g in grs:
            if g["duration_ms"] > GUARDRAIL_SLOW_MS:
                notes.append(f"⚠️  {g['subtype']} spike={g['duration_ms']:.0f}ms")
        print(f"  {uc:<28} {i:>4}  {pre_ms:>8.0f}  {post_ms:>9.0f}  {total:>10.0f}  "
              f"{'  '.join(notes) or ''}")

    # ── Routing classifier cost ────────────────────────────────────────────
    print("\n\n📊 ROUTING CLASSIFIER GAPS (time LLM spends picking sub-agent):")
    print(f"  {'USE CASE':<28} {'INT':>4}  {'ROUTING GAP':>12}  {'TOTAL LAT':>10}  SEVERITY")
    print("  " + "-"*70)

    routing_gaps.sort(key=lambda x: x["routing_gap"], reverse=True)
    for r in routing_gaps:
        if r["routing_gap"] > ROUTING_CRIT_SEC:
            sev = f"🔴  CRITICAL ({r['routing_gap']:.1f}s) — use Haiku + session cache"
        elif r["routing_gap"] > ROUTING_SLOW_SEC:
            sev = f"🟠  SLOW ({r['routing_gap']:.1f}s) — consider Haiku routing model"
        else:
            sev = f"🟢  OK ({r['routing_gap']:.1f}s)"
        print(f"  {r['use_case']:<28} {r['i']:>4}  {r['routing_gap']:>11.2f}s  "
              f"{r['total_lat']:>9.1f}s  {sev}")

    # ── Anomaly detection ──────────────────────────────────────────────────
    anomalies = [g for g in all_guardrails if g["duration_ms"] > GUARDRAIL_SLOW_MS]
    if anomalies:
        print("\n\n⚠️  GUARDRAIL ANOMALY SPIKES (single guardrail > 1000ms):")
        for g in sorted(anomalies, key=lambda x: x["duration_ms"], reverse=True):
            print(f"  {g['use_case']} i{g['interaction']}: "
                  f"{g['subtype']} guardrail = {g['duration_ms']:.0f}ms — "
                  f"possible: Bedrock load spike or oversized output payload")

    # ── Savings opportunity summary ────────────────────────────────────────
    print("\n\n💡 SAVINGS OPPORTUNITY:")
    total_guardrail_s = sum(g["duration_ms"] for g in all_guardrails) / 1000
    n_interactions    = len(by_interaction)
    avg_gr_per_int    = (total_guardrail_s / n_interactions) if n_interactions else 0

    avg_routing = (sum(r["routing_gap"] for r in routing_gaps) / len(routing_gaps)
                   if routing_gaps else 0)

    print(f"  Avg guardrail overhead per interaction : {avg_gr_per_int:.2f}s")
    print(f"  Avg routing gap per interaction        : {avg_routing:.2f}s")
    print(f"  → Session routing cache could save {avg_routing:.1f}s on follow-up turns")
    print(f"  → Switching to Claude Haiku for routing: est. 3–5× faster routing LLM call")


if __name__ == "__main__":
    analyze()
