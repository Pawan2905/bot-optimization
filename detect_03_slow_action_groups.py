"""
detect_03_slow_action_groups.py
─────────────────────────────────────────────────────────────────────────────
WHAT THIS DETECTS:
  Identifies every Action Group (external API) call in the traces —
  what endpoint was hit, how large the response was, and flags known
  expensive patterns.

  Since Bedrock doesn't expose per-AG timing directly in the trace,
  we infer it from:
    (a) Known-slow API patterns matched against /rma, /knowledge-base etc.
    (b) Response payload size (large response = large context injection)
    (c) Whether the call returned empty/error results (wasted latency)

HOW TO RUN:
  python detect_03_slow_action_groups.py

FINDING FROM AVA DATA:
  /knowledge-base/query       → ~74s  (defects_search, troubleshooting)
  /rma/execute-query-sddm     → ~50s  (troubleshooting i2 — returned 0 rows!)
  /rma/get-schema-sddm        → ~5s   (schema fetched at runtime every call)
  /central/get_recommendation → ~20s  (firmware — totally cacheable)
"""
import json
from pathlib import Path
from collections import defaultdict

TRACE_DIR = Path("/mnt/user-data/uploads")

# Known slow APIs — drawn from benchmark data
SLOW_API_SIGNATURES = {
    "/knowledge-base/query":            ("CRITICAL", "~74s",   "Add 1h TTL cache + use OpenSearch ANN index"),
    "/rma/execute-query-sddm":          ("CRITICAL", "~40-50s","Circuit-breaker on 0 rows + query optimization"),
    "/rma/get-schema-sddm":             ("HIGH",     "~5-10s", "Move static schema to system prompt — remove this call"),
    "/central/get_recommendation":      ("HIGH",     "~20s",   "Add 24h TTL cache — firmware rarely changes"),
    "/validate-order-number":           ("MEDIUM",   "unknown","Flaky endpoint — add retry + timeout"),
}


def extract_ag_calls(trace_output: list) -> list[dict]:
    """Extract all AG invocations and their responses from a trace."""
    calls = []
    pending: dict | None = None

    for item in trace_output:
        if item.get("type") != "orchestration_trace":
            continue
        tr = item.get("trace", {})

        # AG invocation input
        inv = tr.get("invocationInput", {})
        ag_in = inv.get("actionGroupInvocationInput") if isinstance(inv, dict) else None
        if ag_in:
            pending = {
                "ag_name":   ag_in.get("actionGroupName", ""),
                "api_path":  ag_in.get("apiPath", ""),
                "function":  ag_in.get("function", ""),
                "params":    ag_in.get("requestBody", {}),
            }

        # AG observation (response)
        obs = tr.get("observation", {})
        if isinstance(obs, dict) and obs.get("type") == "ACTION_GROUP" and pending:
            ag_out  = obs.get("actionGroupInvocationOutput", {})
            resp_text = str(ag_out.get("text", ""))

            # Detect empty / error responses
            is_empty = len(resp_text.strip()) < 10
            is_zero_rows = '"row_count": 0' in resp_text or '"rowCount": 0' in resp_text or '"results": []' in resp_text
            is_error = any(k in resp_text.lower() for k in ["error", "exception", "failed", "not found"])

            calls.append({
                **pending,
                "response_size": len(resp_text),
                "response_preview": resp_text[:200],
                "is_empty":    is_empty,
                "is_zero_rows": is_zero_rows,
                "is_error":    is_error,
            })
            pending = None

    return calls


def analyze():
    print("\n" + "="*70)
    print("STEP 3 — SLOW ACTION GROUP CALL DETECTION")
    print("="*70)

    all_calls: list[dict] = []
    api_stats: dict = defaultdict(lambda: {"count": 0, "total_resp_size": 0,
                                            "zero_rows": 0, "errors": 0, "use_cases": set()})

    for json_file in sorted(TRACE_DIR.glob("*.json")):
        with open(json_file) as f:
            traces = json.load(f)
        for i, trace in enumerate(traces, 1):
            out = trace.get("output", [])
            if not isinstance(out, list):
                continue
            calls = extract_ag_calls(out)
            for call in calls:
                call["use_case"] = json_file.stem
                call["interaction"] = i
                call["total_latency"] = trace["latency"]
                all_calls.append(call)

                key = call["api_path"] or call["function"] or call["ag_name"]
                api_stats[key]["count"] += 1
                api_stats[key]["total_resp_size"] += call["response_size"]
                api_stats[key]["use_cases"].add(json_file.stem)
                if call["is_zero_rows"]:
                    api_stats[key]["zero_rows"] += 1
                if call["is_error"]:
                    api_stats[key]["errors"] += 1

    # ── Summary table ─────────────────────────────────────────────────────
    print(f"\n{'API PATH / FUNCTION':<40} {'CALLS':>6}  {'AVG RESP':>10}  {'ZERO ROWS':>10}  {'ERRORS':>7}")
    print("-"*82)
    for api, stats in sorted(api_stats.items(), key=lambda x: x[1]["total_resp_size"], reverse=True):
        avg_resp = stats["total_resp_size"] / stats["count"] if stats["count"] else 0
        print(
            f"{str(api)[:40]:<40} {stats['count']:>6}  "
            f"{avg_resp:>9,.0f}B  {stats['zero_rows']:>10}  {stats['errors']:>7}"
        )
        print(f"  {'':40} Used in: {', '.join(stats['use_cases'])}")

    # ── Flag known slow APIs ───────────────────────────────────────────────
    print("\n\n🔍 KNOWN SLOW API PATTERNS DETECTED:")
    print("-"*70)
    found_any = False
    for call in all_calls:
        api = call["api_path"] or call["function"] or ""
        for pattern, (sev, est_lat, rec) in SLOW_API_SIGNATURES.items():
            if pattern in api:
                found_any = True
                icon = "🔴" if sev == "CRITICAL" else "🟠" if sev == "HIGH" else "🟡"
                print(f"\n{icon} [{sev}] {call['use_case']} — interaction {call['interaction']}")
                print(f"   API      : {api}")
                print(f"   Est. Cost: {est_lat}")
                print(f"   Fix      : {rec}")
                if call["is_zero_rows"]:
                    print(f"   ⚠️  WASTED CALL — response contained 0 rows")
                if call["is_error"]:
                    print(f"   ⚠️  ERROR RESPONSE — {call['response_preview'][:100]}")
                break  # avoid duplicate matches

    if not found_any:
        print("  No known slow API patterns found.")

    # ── Zero-row wasted calls ──────────────────────────────────────────────
    wasted = [c for c in all_calls if c["is_zero_rows"]]
    if wasted:
        print(f"\n\n⚠️  WASTED API CALLS (returned 0 rows — circuit-breaker needed):")
        for c in wasted:
            print(f"  {c['use_case']} i{c['interaction']}: {c['api_path'] or c['function']}")
            print(f"    Total interaction latency was {c['total_latency']:.1f}s")
            print(f"    The subsequent KB fallback after this 0-row result added further latency")


if __name__ == "__main__":
    analyze()
