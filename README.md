Bot flow optimization


detect_ALL_run_full_report.py   ← ONLY THIS ONE. Run this.
        │
        │  All detection logic is copy-pasted inline inside this file.
        │  It does NOT import the other scripts.
        │
detect_01_latency_overview.py          ← Reference only (standalone view of step 1)
detect_02_timeline_reconstruction.py   ← Reference only (standalone view of step 2)
detect_03_slow_action_groups.py        ← Reference only (standalone view of step 3)
detect_04_llm_cycles_and_token_bloat.py ← Reference only (standalone view of step 4)
detect_05_guardrail_and_routing_overhead.py ← Reference only (standalone view of step 5)
