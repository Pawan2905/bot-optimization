"""
detect_01_latency_overview.py
─────────────────────────────────────────────────────────────────────────────
WHAT THIS DETECTS:
  Which use cases are slow, which are acceptable.
  Gives you the first ranked view of where to focus.

HOW TO RUN:
  python detect_01_latency_overview.py

FINDING FROM AVA DATA:
  defects_search   → 81.8s  🔴 CRITICAL
  troubleshooting  → 79.7s avg (max 143.6s) 🔴 CRITICAL
  case_update      → 36.7s  🟠 HIGH
  case_create      → 26.5s  🟠 HIGH
  firmware_rec     → 19.4s  🟡 MEDIUM
  license_mgmt     → 13.4s  🟢 ACCEPTABLE
"""
import json
from pathlib import Path
from collections import defaultdict

TRACE_DIR = Path("/mnt/user-data/uploads")

SEVERITY = [
    (60,  "🔴  CRITICAL  — needs immediate fix"),
    (30,  "🟠  HIGH      — significant user impact"),
    (15,  "🟡  MEDIUM    — noticeable but usable"),
    (0,   "🟢  OK        — acceptable"),
]

def get_severity(latency_sec):
    for threshold, label in SEVERITY:
        if latency_sec > threshold:
            return label
    return SEVERITY[-1][1]

def analyze():
    print("\n" + "="*70)
    print("STEP 1 — LATENCY OVERVIEW ACROSS ALL USE CASES")
    print("="*70)

    results = []

    for json_file in sorted(TRACE_DIR.glob("*.json")):
        with open(json_file) as f:
            traces = json.load(f)

        latencies = [t["latency"] for t in traces]
        tokens    = [t["totalTokens"] for t in traces]

        results.append({
            "use_case":   json_file.stem,
            "n":          len(traces),
            "avg_lat":    sum(latencies) / len(latencies),
            "max_lat":    max(latencies),
            "min_lat":    min(latencies),
            "avg_tokens": int(sum(tokens) / len(tokens)),
            "max_tokens": max(tokens),
        })

    # Sort by worst average latency
    results.sort(key=lambda x: x["avg_lat"], reverse=True)

    print(f"\n{'USE CASE':<28} {'N':>3}  {'AVG':>7}  {'MAX':>7}  {'MIN':>7}  {'AVG TOKENS':>11}  SEVERITY")
    print("-"*90)

    for r in results:
        sev = get_severity(r["avg_lat"])
        print(
            f"{r['use_case']:<28} {r['n']:>3}  "
            f"{r['avg_lat']:>6.1f}s  {r['max_lat']:>6.1f}s  {r['min_lat']:>6.1f}s  "
            f"{r['avg_tokens']:>11,}  {sev}"
        )

    print()
    total_interactions = sum(r["n"] for r in results)
    critical = [r for r in results if r["avg_lat"] > 60]
    high     = [r for r in results if 30 < r["avg_lat"] <= 60]

    print(f"  Total interactions analyzed : {total_interactions}")
    print(f"  Critical use cases (>60s)   : {len(critical)} → {[r['use_case'] for r in critical]}")
    print(f"  High severity (30–60s)      : {len(high)} → {[r['use_case'] for r in high]}")

if __name__ == "__main__":
    analyze()
