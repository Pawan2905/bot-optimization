"""
detect_04_llm_cycles_and_token_bloat.py
─────────────────────────────────────────────────────────────────────────────
WHAT THIS DETECTS:
  1. How many LLM reasoning cycles (model invocations) happen per interaction
     — every extra cycle adds 3–10s of latency
  2. How many tokens are in the context window
     — large context = slow inference on every subsequent LLM call

  In a Bedrock agent ReAct loop:
    [LLM call] → decide action → [AG call] → observe result → [LLM call] ...

  Each LLM-call step shows up as an orchestration_trace with
  "modelInvocationInput" present. We count these.

HOW TO RUN:
  python detect_04_llm_cycles_and_token_bloat.py

FINDING FROM AVA DATA:
  case_update  i1  → 5 LLM calls, 35,720 tokens
  case_update  i2  → 4 LLM calls, 35,720 tokens
  case_create  i3  → 3 LLM calls, 44,687 tokens  ← peak token count
  troubleshoot i2  → 2 LLM calls, 21,774 tokens (but 3 AG calls = huge latency)
"""
import json
from pathlib import Path

TRACE_DIR = Path("/mnt/user-data/uploads")

# Thresholds
LLM_CYCLE_WARN  = 3   # more than this = HIGH
LLM_CYCLE_CRIT  = 4   # more than this = CRITICAL
TOKEN_WARN       = 20_000
TOKEN_CRIT       = 35_000


def count_llm_cycles(trace_output: list) -> tuple[int, int]:
    """
    Returns (llm_invocation_count, ag_call_count) from a trace output list.
    """
    llm_count = 0
    ag_count  = 0
    for item in trace_output:
        if item.get("type") != "orchestration_trace":
            continue
        tr = item.get("trace", {})
        if "modelInvocationInput" in tr:
            llm_count += 1
        inv = tr.get("invocationInput", {})
        if isinstance(inv, dict) and "actionGroupInvocationInput" in inv:
            ag_count += 1
    return llm_count, ag_count


def analyze():
    print("\n" + "="*70)
    print("STEP 4 — LLM REASONING CYCLES & TOKEN BLOAT")
    print("="*70)
    print("\nEvery LLM reasoning cycle adds 3–10s.")
    print("Large context (>30k tokens) slows each LLM call further.\n")

    print(f"{'USE CASE':<28} {'INT':>4}  {'LATENCY':>8}  {'TOKENS':>9}  "
          f"{'LLM CALLS':>10}  {'AG CALLS':>9}  VERDICT")
    print("-"*95)

    rows = []
    for json_file in sorted(TRACE_DIR.glob("*.json")):
        with open(json_file) as f:
            traces = json.load(f)
        for i, trace in enumerate(traces, 1):
            out = trace.get("output", [])
            if not isinstance(out, list):
                continue
            llm, ag = count_llm_cycles(out)
            rows.append({
                "use_case": json_file.stem,
                "i":        i,
                "latency":  trace["latency"],
                "tokens":   trace["totalTokens"],
                "llm":      llm,
                "ag":       ag,
            })

    rows.sort(key=lambda x: (x["llm"], x["tokens"]), reverse=True)

    for r in rows:
        flags = []
        if r["llm"] > LLM_CYCLE_CRIT:
            flags.append("🔴 Excessive LLM loops")
        elif r["llm"] > LLM_CYCLE_WARN:
            flags.append("🟠 Many LLM loops")

        if r["tokens"] > TOKEN_CRIT:
            flags.append("🔴 Token bloat")
        elif r["tokens"] > TOKEN_WARN:
            flags.append("🟡 High tokens")

        verdict = " | ".join(flags) if flags else "🟢 OK"

        print(
            f"{r['use_case']:<28} {r['i']:>4}  {r['latency']:>7.1f}s  "
            f"{r['tokens']:>9,}  {r['llm']:>10}  {r['ag']:>9}  {verdict}"
        )

    # ── Cost model ────────────────────────────────────────────────────────
    print("\n\n💡 COST MODEL — Estimated time spent in LLM inference alone:")
    print("   (assuming avg 5s per LLM call for Claude Sonnet):")
    by_uc: dict = {}
    for r in rows:
        by_uc.setdefault(r["use_case"], []).append(r)

    for uc, rlist in sorted(by_uc.items()):
        avg_llm = sum(r["llm"] for r in rlist) / len(rlist)
        avg_lat = sum(r["latency"] for r in rlist) / len(rlist)
        est_llm_time = avg_llm * 5
        pct = (est_llm_time / avg_lat * 100) if avg_lat else 0
        print(f"  {uc:<28} avg {avg_llm:.1f} LLM calls × 5s = ~{est_llm_time:.0f}s "
              f"({pct:.0f}% of {avg_lat:.1f}s total)")

    # ── Fix recommendation ────────────────────────────────────────────────
    high_llm = [r for r in rows if r["llm"] > LLM_CYCLE_WARN]
    if high_llm:
        print("\n\n🔧 RECOMMENDED FIX FOR EXCESSIVE LLM CYCLES:")
        print("   Add this to the sub-agent system prompt:")
        print("""
   \"\"\"
   IMPORTANT — Follow this EXACT sequence with no extra reasoning steps:
   1. First call verify-case with the case number provided.
   2. If valid, call update-case immediately with the requested changes.
   3. Call get-case-details-page-url to retrieve the URL.
   4. Respond to the user with the result and URL.
   Do NOT re-verify or re-check between steps. Complete all steps in order.
   \"\"\"
   """)

    high_tokens = [r for r in rows if r["tokens"] > TOKEN_WARN]
    if high_tokens:
        print("🔧 RECOMMENDED FIX FOR TOKEN BLOAT:")
        print("   After each AG call, prune its response to relevant fields only.")
        print("   Example: /case-mgmt/update-case response → keep only {case_details_page_url}")
        print("   See context_pruner.py for implementation.\n")


if __name__ == "__main__":
    analyze()
