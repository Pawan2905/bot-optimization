"""
detect_ALL_run_full_report.py
─────────────────────────────────────────────────────────────────────────────
MASTER SCRIPT — runs all 5 detectors and outputs a single ranked report.

Usage:
  python detect_ALL_run_full_report.py [--trace-dir /path/to/jsons]

Output sections:
  1. Latency Overview          (which use cases need attention)
  2. Timeline Reconstruction   (routing gap + orchestration gap per interaction)
  3. Slow Action Groups        (external API bottlenecks)
  4. LLM Cycles & Token Bloat  (reasoning loop counts + context size)
  5. Guardrail & Routing Cost  (constant overhead per interaction)
  6. Ranked Recommendations    (prioritized fix list)
"""
import json
import sys
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict


TRACE_DIR = Path("/mnt/user-data/uploads")

# ─── Shared helpers ───────────────────────────────────────────────────────────
def parse_dt(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load_all_traces(trace_dir: Path) -> list[dict]:
    """Load every JSON trace file into a flat list of dicts."""
    rows = []
    for jf in sorted(trace_dir.glob("*.json")):
        with open(jf) as f:
            traces = json.load(f)
        for i, trace in enumerate(traces, 1):
            rows.append({"use_case": jf.stem, "interaction": i, "trace": trace})
    return rows


def get_guardrails(trace_output):
    grs = []
    for item in trace_output:
        if item.get("type") != "guardrail_trace":
            continue
        tr   = item.get("trace", {})
        meta = tr.get("metadata", {})
        if "startTime" not in meta:
            continue
        grs.append({
            "subtype":     "pre" if "pre" in tr.get("traceId","") else "post",
            "duration_ms": meta.get("totalTimeMs", 0),
            "start":       parse_dt(meta["startTime"]),
            "end":         parse_dt(meta["endTime"]) if "endTime" in meta else parse_dt(meta["startTime"]),
        })
    return sorted(grs, key=lambda x: x["start"])


def get_timeline_gaps(grs):
    if len(grs) < 3:
        return 0.0, 0.0
    routing_gap = max(0, (grs[1]["start"] - grs[0]["end"]).total_seconds())
    orch_gap    = max(0, (grs[2]["start"] - grs[1]["end"]).total_seconds())
    return routing_gap, orch_gap


def count_llm_and_ag(trace_output):
    llm = ag = 0
    for item in trace_output:
        if item.get("type") != "orchestration_trace":
            continue
        tr  = item.get("trace", {})
        inv = tr.get("invocationInput", {})
        if "modelInvocationInput" in tr:
            llm += 1
        if isinstance(inv, dict) and "actionGroupInvocationInput" in inv:
            ag += 1
    return llm, ag


def get_ag_calls(trace_output):
    calls, pending = [], None
    for item in trace_output:
        if item.get("type") != "orchestration_trace":
            continue
        tr  = item.get("trace", {})
        inv = tr.get("invocationInput", {})
        ag_in = inv.get("actionGroupInvocationInput") if isinstance(inv, dict) else None
        if ag_in:
            pending = {
                "ag_name":  ag_in.get("actionGroupName", ""),
                "api_path": ag_in.get("apiPath", ""),
                "function": ag_in.get("function", ""),
            }
        obs = tr.get("observation", {})
        if isinstance(obs, dict) and obs.get("type") == "ACTION_GROUP" and pending:
            resp    = str(obs.get("actionGroupInvocationOutput", {}).get("text", ""))
            pending["response_size"] = len(resp)
            pending["is_zero_rows"]  = ('"row_count": 0' in resp or '"results": []' in resp)
            pending["is_error"]      = any(k in resp.lower() for k in ["error","exception","failed"])
            calls.append(pending)
            pending = None
    return calls


SLOW_APIS = {
    "/knowledge-base/query":        ("CRITICAL", "~74s",   "Cache with 1h TTL + OpenSearch ANN index"),
    "/rma/execute-query-sddm":      ("CRITICAL", "~40-50s","Circuit-breaker on 0 rows + SQL optimization"),
    "/rma/get-schema-sddm":         ("HIGH",     "~5-10s", "Move static schema into system prompt"),
    "/central/get_recommendation":  ("HIGH",     "~20s",   "Cache with 24h TTL — firmware rarely changes"),
}

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


# ─── Main analysis ────────────────────────────────────────────────────────────
def run_full_analysis(trace_dir: Path):
    rows = load_all_traces(trace_dir)
    issues = []   # [{severity, category, context, fix}]
    stats  = []   # per-interaction stats dict

    for row in rows:
        uc    = row["use_case"]
        i     = row["interaction"]
        trace = row["trace"]
        out   = trace.get("output", [])
        if not isinstance(out, list):
            continue

        grs            = get_guardrails(out)
        routing_gap, orch_gap = get_timeline_gaps(grs)
        llm_count, ag_count   = count_llm_and_ag(out)
        ag_calls               = get_ag_calls(out)
        guardrail_ms           = sum(g["duration_ms"] for g in grs)

        s = {
            "use_case":      uc,
            "i":             i,
            "latency":       trace["latency"],
            "tokens":        trace["totalTokens"],
            "routing_gap":   routing_gap,
            "orch_gap":      orch_gap,
            "guardrail_ms":  guardrail_ms,
            "llm_count":     llm_count,
            "ag_count":      ag_count,
            "ag_calls":      ag_calls,
        }
        stats.append(s)

        label = f"{uc} — interaction {i}"

        # Issue detection
        if trace["latency"] > 60:
            issues.append({"severity": "CRITICAL", "category": "Total latency",
                "context": f"{label}: {trace['latency']:.1f}s",
                "fix": "Investigate orchestration gap — likely slow AG call or excessive LLM loops."})

        elif trace["latency"] > 30:
            issues.append({"severity": "HIGH", "category": "Total latency",
                "context": f"{label}: {trace['latency']:.1f}s",
                "fix": "Review LLM cycle count and AG response sizes."})

        for ag in ag_calls:
            api = ag["api_path"] or ag["function"] or ""
            for pattern, (sev, est, fix) in SLOW_APIS.items():
                if pattern in api:
                    issues.append({"severity": sev, "category": "Slow Action Group",
                        "context": f"{label}: {api} (est. {est})",
                        "fix": fix})
                    if ag.get("is_zero_rows"):
                        issues.append({"severity": "HIGH", "category": "Wasted API call",
                            "context": f"{label}: {api} returned 0 rows",
                            "fix": "Add circuit-breaker: skip KB fallback when SQL returns 0 rows."})
                    break

        if llm_count > 3:
            issues.append({"severity": "HIGH", "category": "Excessive LLM cycles",
                "context": f"{label}: {llm_count} LLM calls",
                "fix": "Add forced action-sequence in system prompt to reduce reasoning iterations."})

        if trace["totalTokens"] > 30_000:
            issues.append({"severity": "MEDIUM", "category": "Token bloat",
                "context": f"{label}: {trace['totalTokens']:,} tokens",
                "fix": "Prune AG response fields before re-injecting into context."})

        if routing_gap > 8:
            issues.append({"severity": "CRITICAL", "category": "Slow routing classifier",
                "context": f"{label}: routing gap = {routing_gap:.1f}s",
                "fix": "Switch to Claude Haiku for routing + add session routing cache."})
        elif routing_gap > 3:
            issues.append({"severity": "MEDIUM", "category": "Slow routing classifier",
                "context": f"{label}: routing gap = {routing_gap:.1f}s",
                "fix": "Consider Claude Haiku for routing model."})

        anomalies = [g for g in grs if g["duration_ms"] > 1000]
        for g in anomalies:
            issues.append({"severity": "MEDIUM", "category": "Guardrail spike",
                "context": f"{label}: {g['subtype']} guardrail = {g['duration_ms']:.0f}ms",
                "fix": "Monitor for Bedrock service load spikes. Consider disabling output guardrail for read-only use cases."})

    # ─── Print report ─────────────────────────────────────────────────────
    print("\n" + "█"*70)
    print("  AVA BOT — FULL LATENCY ISSUE REPORT")
    print("█"*70)

    # Section 1: Overview
    print("\n━━━ 1. LATENCY OVERVIEW ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    by_uc: dict = defaultdict(list)
    for s in stats:
        by_uc[s["use_case"]].append(s)

    print(f"{'USE CASE':<28} {'N':>3}  {'AVG LAT':>8}  {'MAX LAT':>8}  {'AVG TOKENS':>11}  STATUS")
    print("-"*75)
    for uc in sorted(by_uc, key=lambda u: -sum(s["latency"] for s in by_uc[u])/len(by_uc[u])):
        sl = by_uc[uc]
        avg = sum(s["latency"] for s in sl) / len(sl)
        mx  = max(s["latency"] for s in sl)
        tok = int(sum(s["tokens"] for s in sl) / len(sl))
        badge = "🔴 CRITICAL" if avg>60 else "🟠 HIGH" if avg>30 else "🟡 MEDIUM" if avg>15 else "🟢 OK"
        print(f"{uc:<28} {len(sl):>3}  {avg:>7.1f}s  {mx:>7.1f}s  {tok:>11,}  {badge}")

    # Section 2: Timeline gaps
    print("\n\n━━━ 2. TIMELINE GAPS (Routing vs Orchestration) ━━━━━━━━━━━━━━━━━\n")
    print(f"{'USE CASE':<28} {'INT':>4}  {'ROUTING GAP':>12}  {'ORCH GAP':>11}  {'GUARDRAIL(ms)':>14}")
    print("-"*78)
    for s in sorted(stats, key=lambda x: -x["orch_gap"]):
        print(f"{s['use_case']:<28} {s['i']:>4}  {s['routing_gap']:>11.2f}s  "
              f"{s['orch_gap']:>10.2f}s  {s['guardrail_ms']:>14.0f}")

    # Section 3: AG calls
    print("\n\n━━━ 3. ACTION GROUP CALLS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    for s in stats:
        for ag in s["ag_calls"]:
            api = ag["api_path"] or ag["function"] or ag["ag_name"]
            flags = []
            if ag.get("is_zero_rows"): flags.append("ZERO ROWS ⚠️")
            if ag.get("is_error"):     flags.append("ERROR ⚠️")
            for pat in SLOW_APIS:
                if pat in api:
                    flags.append(f"KNOWN SLOW")
                    break
            print(f"  {s['use_case']} i{s['i']}: {api:<45} resp={ag['response_size']:>6}B  {' | '.join(flags)}")

    # Section 4: LLM cycles
    print("\n\n━━━ 4. LLM CYCLES & TOKENS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    print(f"{'USE CASE':<28} {'INT':>4}  {'LLM CALLS':>10}  {'AG CALLS':>9}  {'TOKENS':>9}")
    print("-"*68)
    for s in sorted(stats, key=lambda x: -(x["llm_count"]*10000 + x["tokens"])):
        token_flag = " 🔴" if s["tokens"]>35000 else " 🟡" if s["tokens"]>20000 else ""
        llm_flag   = " 🔴" if s["llm_count"]>4 else " 🟠" if s["llm_count"]>3 else ""
        print(f"{s['use_case']:<28} {s['i']:>4}  {s['llm_count']:>10}{llm_flag:<3}  "
              f"{s['ag_count']:>9}  {s['tokens']:>9,}{token_flag}")

    # Section 5: Ranked issues
    print("\n\n━━━ 5. RANKED ISSUES & FIXES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    issues_sorted = sorted(issues, key=lambda x: SEVERITY_ORDER.get(x["severity"], 9))
    for idx, issue in enumerate(issues_sorted, 1):
        icon = {"CRITICAL":"🔴","HIGH":"🟠","MEDIUM":"🟡","LOW":"🟢"}.get(issue["severity"],"⚪")
        print(f"#{idx:02d} {icon} [{issue['severity']:8}] {issue['category']}")
        print(f"     Context : {issue['context']}")
        print(f"     Fix     : {issue['fix']}")
        print()

    # Section 6: Optimization priority
    print("\n━━━ 6. OPTIMIZATION PRIORITY CHECKLIST ━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    priorities = [
        ("✅", "Cache /knowledge-base/query — 1h TTL Redis",                "defects_search, troubleshooting", "Saves ~74s"),
        ("✅", "Cache /central/get_recommendation — 24h TTL",               "firmware_recommendation",         "Saves ~20s"),
        ("✅", "Move /rma/get-schema-sddm to system prompt",                "troubleshooting",                 "Saves ~5-10s"),
        ("✅", "Circuit-breaker: skip KB when SQL = 0 rows",                "troubleshooting",                 "Saves ~74s on worst path"),
        ("✅", "Session routing cache + intent keyword bypass",             "ALL",                             "Saves 1.5–13s per turn"),
        ("✅", "Force action-sequence in case_update system prompt",        "case_update",                     "Saves ~10-15s"),
        ("✅", "Switch routing classifier to Claude Haiku",                 "ALL",                             "Saves 1-8s"),
        ("✅", "Enable streaming responses for progressive rendering",      "ALL",                             "Reduces perceived latency"),
        ("✅", "Prune AG response payloads to allowed fields only",         "case_update, case_create",        "Reduces token bloat"),
    ]
    for icon, action, use_cases, saving in priorities:
        print(f"  {icon} {action}")
        print(f"     Use cases : {use_cases}")
        print(f"     Expected  : {saving}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", type=Path, default=TRACE_DIR)
    args = parser.parse_args()
    run_full_analysis(args.trace_dir)


if __name__ == "__main__":
    main()
