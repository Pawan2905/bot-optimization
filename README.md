# Bot Flow Optimization: Detection Framework

This document outlines the structure and execution path for the Bot Flow Optimization detection scripts. 

## Primary Execution Script

**`detect_ALL_run_full_report.py`**
* **Role:** The master execution engine.
* **Architecture:** All detection logic is copy-pasted inline inside this file.
* **Dependency Note:** This script is entirely self-contained; it **does NOT** import the other scripts listed below. Use this to generate the comprehensive analysis report.

---

## Reference Scripts (Standalone Logic)

The following scripts serve as standalone references for specific steps in the detection process. They are useful for isolated debugging or focused analysis of a single metric.

### 1. Latency Overview
* **File:** `detect_01_latency_overview.py`
* **Focus:** Provides a high-level view of end-to-end latency across the bot flow.

### 2. Timeline Reconstruction
* **File:** `detect_02_timeline_reconstruction.py`
* **Focus:** Reassembles the chronological sequence of events for a single session.

### 3. Slow Action Groups
* **File:** `detect_03_slow_action_groups.py`
* **Focus:** Identifies specific clusters of actions or nodes that contribute most to delays.

### 4. LLM Cycles and Token Bloat
* **File:** `detect_04_llm_cycles_and_token_bloat.py`
* **Focus:** Analyzes excessive LLM calls and identifies inefficient token usage or "bloated" prompts.

### 5. Guardrail and Routing Overhead
* **File:** `detect_05_guardrail_and_routing_overhead.py`
* **Focus:** Measures the time spent on safety guardrails and decision-tree routing logic.
