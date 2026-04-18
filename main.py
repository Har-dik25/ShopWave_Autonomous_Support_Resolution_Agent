"""
ShopWave Autonomous Support Resolution Agent — Main Entry Point (v2)
=====================================================================
Key v2 features:
  - CONCURRENT ticket processing via ThreadPoolExecutor
  - Minimum 3 tool calls per ticket chain (enforced + validated)
  - Tool failure recovery demonstration
  - Rich explainability output
  - Full audit trail to JSON
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_manager import get_all_tickets
from agent import resolve_ticket
from tools import get_audit_log, ENABLE_FAILURE_SIMULATION


# ──────────────────────────────────────────────────────────────
# ANSI Colors
# ──────────────────────────────────────────────────────────────
class C:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"
    WHITE = "\033[97m"
    MAGENTA = "\033[35m"
    BG_GREEN = "\033[42m"
    BG_RED = "\033[41m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"


def print_banner():
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════════════════════════════╗
║        🛒  ShopWave Autonomous Support Resolution Agent v2  🛒          ║
║           Concurrent • Resilient • Explainable • No LLM                 ║
╚══════════════════════════════════════════════════════════════════════════╝{C.RESET}
""")


def print_ticket_result(report, audit_log, idx, total):
    """Print a single ticket's full resolution output."""
    tid = report["ticket_id"]

    res_display = {
        "resolved": f"{C.BG_GREEN}{C.WHITE}{C.BOLD} ✅ RESOLVED {C.RESET}",
        "resolved_declined": f"{C.BG_YELLOW}{C.WHITE}{C.BOLD} ⛔ DECLINED {C.RESET}",
        "escalated": f"{C.BG_BLUE}{C.WHITE}{C.BOLD} ⬆️  ESCALATED {C.RESET}",
        "awaiting_customer_info": f"{C.BG_YELLOW}{C.WHITE}{C.BOLD} ⏳ AWAITING INFO {C.RESET}",
    }.get(report["resolution"], report["resolution"])

    cat_emoji = {
        "refund_return": "💰", "order_cancellation": "🚫", "order_status": "📦",
        "refund_status": "🔍", "wrong_item": "🔄", "damaged_defective": "🔨",
        "replacement_request": "🔁", "general_query": "❓", "ambiguous": "❔",
    }.get(report["category"], "📋")

    urg_color = {"low": C.GREEN, "medium": C.YELLOW, "high": C.RED}.get(report["urgency"], C.RESET)

    # Header
    print(f"\n{C.BOLD}{C.BLUE}{'═' * 76}{C.RESET}")
    print(f"{C.BOLD}{C.WHITE}  📩 [{idx}/{total}] {tid}  →  {res_display}")
    print(f"{C.BLUE}{'─' * 76}{C.RESET}")

    # Classification
    print(f"  {cat_emoji} Category: {C.CYAN}{report['category']}{C.RESET}    "
          f"⚡ Urgency: {urg_color}{report['urgency'].upper()}{C.RESET}    "
          f"🎯 Confidence: {C.BOLD}{report['confidence_score']:.0%}{C.RESET}")

    # Flags
    if report["flags"]:
        print(f"  🚩 Flags: {C.RED}{', '.join(report['flags'])}{C.RESET}")

    # Tool chain validation
    tc = report["tool_call_count"]
    met = report["min_3_tools_met"]
    tc_color = C.GREEN if met else C.RED
    print(f"  🔧 Tools: {tc_color}{tc} calls{C.RESET} ({', '.join(report['tools_used'])})"
          f"  {'✅ ≥3 chain' if met else '❌ <3 chain'}")

    # Reasoning trace
    print(f"\n  {C.BOLD}Reasoning Chain ({report['total_steps']} steps):{C.RESET}")
    for i, s in enumerate(report["reasoning_steps"], 1):
        thought = s.get("thought", "")
        action = s.get("action", "")
        obs = s.get("observation", "")

        print(f"    {C.DIM}[{i}]{C.RESET} {C.YELLOW}💭{C.RESET} {thought[:120]}{'...' if len(thought) > 120 else ''}")
        if action:
            print(f"        {C.CYAN}🔧 {action}{C.RESET}")
        if obs:
            obs_short = obs[:140] + ("..." if len(obs) > 140 else "")
            # Highlight DECISION: annotations
            if obs_short.startswith("DECISION:"):
                print(f"        {C.GREEN}📋 {obs_short}{C.RESET}")
            else:
                print(f"        {C.DIM}👁 {obs_short}{C.RESET}")

    # Decisions summary (explainability)
    decisions = report.get("explainability", {}).get("decisions", [])
    if decisions:
        print(f"\n  {C.BOLD}📋 Key Decisions:{C.RESET}")
        for d in decisions:
            print(f"    {C.GREEN}→{C.RESET} {d}")

    # Reply sent
    for entry in reversed(audit_log):
        if entry.get("ticket_id") == tid and entry.get("tool") == "send_reply":
            msg = entry["output"].get("message_sent", "")
            if msg:
                print(f"\n  {C.BOLD}📨 Customer Reply:{C.RESET}")
                lines = msg.split("\n")
                for line in lines[:12]:
                    print(f"    {C.DIM}│{C.RESET} {line}")
                if len(lines) > 12:
                    print(f"    {C.DIM}│ ... ({len(lines) - 12} more lines){C.RESET}")
                break

    # Tool failures recovered
    failures = [e for e in audit_log if e.get("ticket_id") == tid and "failure" in e.get("tool", "")]
    recoveries = [e for e in audit_log if e.get("ticket_id") == tid and e.get("recovered_from_failure")]
    if failures:
        print(f"\n  {C.RED}⚠️  Tool Failures: {len(failures)}{C.RESET}  |  "
              f"{C.GREEN}🔄 Recovered: {len(recoveries)}{C.RESET}")
        for f in failures:
            print(f"    {C.RED}✗{C.RESET} {f['tool']}: {f['output'].get('error', '')[:80]}")
        for r in recoveries:
            print(f"    {C.GREEN}✓{C.RESET} Recovered on attempt #{r.get('attempt', '?')}")


def print_summary(reports, elapsed, num_workers):
    from collections import Counter

    print(f"\n\n{C.CYAN}{C.BOLD}{'═' * 76}{C.RESET}")
    print(f"{C.BOLD}{C.WHITE}                        📊  SUMMARY DASHBOARD  📊{C.RESET}")
    print(f"{C.CYAN}{'═' * 76}{C.RESET}\n")

    total = len(reports)
    resolved = sum(1 for r in reports if r["resolution"] in ("resolved", "resolved_declined"))
    escalated = sum(1 for r in reports if r["resolution"] == "escalated")
    awaiting = sum(1 for r in reports if r["resolution"] == "awaiting_customer_info")

    print(f"  {C.BOLD}Processing:{C.RESET}")
    print(f"    Mode:               {C.CYAN}CONCURRENT{C.RESET} ({num_workers} workers)")
    print(f"    Total Time:         {C.BOLD}{elapsed:.2f}s{C.RESET}")
    print(f"    Avg per Ticket:     {C.BOLD}{elapsed/total*1000:.0f}ms{C.RESET}")
    print()

    print(f"  {C.BOLD}Resolution:{C.RESET}")
    print(f"    Total Processed:    {C.BOLD}{total}{C.RESET}")
    print(f"    ✅ Resolved:        {C.GREEN}{resolved}{C.RESET}")
    print(f"    ⬆️  Escalated:       {C.BLUE}{escalated}{C.RESET}")
    print(f"    ⏳ Awaiting Info:    {C.YELLOW}{awaiting}{C.RESET}")
    print(f"    🤖 Autonomous Rate: {C.BOLD}{resolved/total*100:.1f}%{C.RESET}")
    print()

    # Minimum 3 tool calls check
    met_3 = sum(1 for r in reports if r["min_3_tools_met"])
    print(f"  {C.BOLD}Chain Constraint (≥3 tool calls):{C.RESET}")
    print(f"    Met:    {C.GREEN}{met_3}/{total}{C.RESET}")
    not_met = [r["ticket_id"] for r in reports if not r["min_3_tools_met"]]
    if not_met:
        print(f"    Failed: {C.RED}{', '.join(not_met)}{C.RESET}")
    print()

    # Tool failure recovery
    audit = get_audit_log()
    failures = [e for e in audit if "failure" in e.get("tool", "")]
    recoveries = [e for e in audit if e.get("recovered_from_failure")]
    print(f"  {C.BOLD}Failure Recovery:{C.RESET}")
    print(f"    Simulated Failures: {C.RED}{len(failures)}{C.RESET}")
    print(f"    Recovered:          {C.GREEN}{len(recoveries)}{C.RESET}")
    if failures:
        for f in failures:
            print(f"    {C.RED}✗{C.RESET} [{f.get('ticket_id')}] {f['tool']}: {f['output'].get('error', '')[:60]}")
        for r in recoveries:
            print(f"    {C.GREEN}✓{C.RESET} [{r.get('ticket_id')}] Recovered → attempt #{r.get('attempt')}")
    print()

    # Category breakdown
    cats = Counter(r["category"] for r in reports)
    print(f"  {C.BOLD}Categories:{C.RESET}")
    for c, n in cats.most_common():
        print(f"    {c:25s} {C.CYAN}{'█' * n}{C.RESET} {n}")
    print()

    # Urgency breakdown
    urgs = Counter(r["urgency"] for r in reports)
    print(f"  {C.BOLD}Urgency:{C.RESET}")
    for u, n in urgs.most_common():
        color = {"low": C.GREEN, "medium": C.YELLOW, "high": C.RED}.get(u, C.RESET)
        print(f"    {u:10s} {color}{'█' * n}{C.RESET} {n}")
    print()

    # Confidence distribution
    confs = [r["confidence_score"] for r in reports]
    avg_conf = sum(confs) / len(confs)
    print(f"  {C.BOLD}Confidence:{C.RESET}")
    print(f"    Average: {C.BOLD}{avg_conf:.0%}{C.RESET}  |  "
          f"Min: {min(confs):.0%}  |  Max: {max(confs):.0%}")

    print(f"\n{C.CYAN}{'═' * 76}{C.RESET}")


def main():
    print_banner()

    tickets = get_all_tickets()
    total = len(tickets)
    NUM_WORKERS = min(8, total)  # concurrent workers

    print(f"  {C.BOLD}Loading {total} tickets...{C.RESET}")
    print(f"  {C.BOLD}Processing mode: CONCURRENT ({NUM_WORKERS} workers){C.RESET}")
    print(f"  {C.BOLD}Failure simulation: {'ENABLED' if ENABLE_FAILURE_SIMULATION else 'DISABLED'}{C.RESET}")
    print(f"  {C.DIM}Simulated failures on: TKT-004 (get_order timeout), TKT-008 (get_product malformed), TKT-013 (eligibility timeout){C.RESET}")
    print()

    # ── CONCURRENT PROCESSING ──
    reports = [None] * total  # preserve order
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        future_to_idx = {
            executor.submit(resolve_ticket, ticket): idx
            for idx, ticket in enumerate(tickets)
        }
        completed = 0
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                report = future.result()
                reports[idx] = report
                completed += 1
                # Progress indicator
                tid = report["ticket_id"]
                res = report["resolution"]
                emoji = {"resolved": "✅", "resolved_declined": "⛔", "escalated": "⬆️", "awaiting_customer_info": "⏳"}.get(res, "❓")
                tc = report["tool_call_count"]
                print(f"  {C.DIM}[{completed}/{total}]{C.RESET} {emoji} {tid} → {res} ({tc} tools)")
            except Exception as e:
                reports[idx] = {
                    "ticket_id": tickets[idx]["ticket_id"],
                    "resolution": "error",
                    "category": "unknown",
                    "urgency": "high",
                    "flags": ["processing_error"],
                    "confidence_score": 0.0,
                    "tools_used": [],
                    "tool_call_count": 0,
                    "reasoning_steps": [{"thought": f"FATAL ERROR: {str(e)}"}],
                    "total_steps": 1,
                    "min_3_tools_met": False,
                    "explainability": {"decisions": []},
                    "error": str(e),
                }
                completed += 1
                print(f"  {C.RED}[{completed}/{total}] ❌ {tickets[idx]['ticket_id']} → ERROR: {e}{C.RESET}")

    elapsed = time.time() - start_time

    # ── DISPLAY DETAILED RESULTS (in original order) ──
    audit_log = get_audit_log()
    print(f"\n{C.BOLD}{C.CYAN}  ──── DETAILED RESULTS ────{C.RESET}")

    for idx, report in enumerate(reports):
        print_ticket_result(report, audit_log, idx + 1, total)

    # ── SUMMARY ──
    print_summary(reports, elapsed, NUM_WORKERS)

    # ── SELF-EVALUATION ──
    from evaluator import evaluate_results, print_evaluation
    evaluation = evaluate_results(tickets, reports)
    print_evaluation(evaluation)

    # ── SAVE ARTIFACTS ──
    out_dir = os.path.dirname(os.path.abspath(__file__))

    audit_path = os.path.join(out_dir, "audit_log.json")
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(audit_log, f, indent=2, default=str)
    print(f"\n  {C.GREEN}✅ Audit log saved:{C.RESET} {audit_path} ({len(audit_log)} entries)")

    report_path = os.path.join(out_dir, "resolution_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(reports, f, indent=2, default=str)
    print(f"  {C.GREEN}✅ Resolution report saved:{C.RESET} {report_path}")

    eval_path = os.path.join(out_dir, "evaluation_report.json")
    with open(eval_path, "w", encoding="utf-8") as f:
        json.dump(evaluation, f, indent=2, default=str)
    print(f"  {C.GREEN}✅ Evaluation report saved:{C.RESET} {eval_path}")

    print(f"\n  {C.BOLD}{C.CYAN}💡 For live web dashboard: python dashboard.py → http://localhost:8080{C.RESET}")
    print(f"\n{C.BOLD}{C.CYAN}  🎉 All {total} tickets processed concurrently in {elapsed:.2f}s. Run complete.{C.RESET}\n")


if __name__ == "__main__":
    main()
