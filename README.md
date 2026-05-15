# 🛒 NexusDesk Autonomous Support Resolution Agent

> **Agentic AI that doesn't just classify tickets — it resolves them.**

An autonomous, rule-based support resolution agent for the NexusDesk e-commerce platform. Processes 20 customer support tickets concurrently, resolves them using a 9-tool toolkit, and produces a complete audit trail — all without a single LLM API call.

---

## 🚀 Quick Start

```bash
# No dependencies — uses only Python 3.10+ standard library
cd nexusdesk_agent

# Option 1: CLI (full output with self-evaluation)
python main.py

# Option 2: Live Web Dashboard
python dashboard.py
# → Open http://localhost:8080
```

No `pip install`, no API keys, no `.env` files. Zero external dependencies.

---

## 📊 What It Does

| Step | Description |
|------|-------------|
| **01 Ingest** | Loads 20 tickets from `data/tickets.json` |
| **02 Classify** | Rule-based categorization (8 categories) + urgency (low/medium/high) + flag detection (social engineering, threatening language) |
| **03 Resolve** | Multi-step tool chains (≥3 tools per ticket): fetch data → verify eligibility → take action → reply to customer |
| **04 Escalate** | When uncertain or policy requires it, hands off to human with structured summary |
| **05 Audit** | Every tool call, reasoning step, and decision logged to `audit_log.json` |

### Results (20 Tickets)

```
✅ Resolved:        12 (60%)
⬆️  Escalated:       4 (20%)
⏳ Awaiting Info:    4 (20%)
🎯 Avg Confidence:  88%
⏱️  Processing Time:  0.13s (concurrent, 8 workers)
🔧 Tool Calls:      20/20 meet ≥3 chain constraint
🔄 Failures:        6 injected → 6 recovered
```

---

## 🏗️ Architecture

```
main.py                 → Entry point: concurrent executor + rich console output
  ├── agent.py           → Core agent: classifier + resolver + 8 category handlers
  │     ├── tools.py     → 9 tools with @resilient_tool retry decorator
  │     │     └── data_manager.py  → JSON data loader + indexed lookups
  │     └── ReasoningChain        → Per-ticket step logger (thread-safe)
  └── data/
        ├── tickets.json          → 20 customer support tickets
        ├── customers.json        → 10 customer profiles with tiers
        ├── orders.json           → 20 orders with status + return deadlines
        ├── products.json         → 10 products with warranty + return policies
        └── knowledge-base.md     → NexusDesk support policies & FAQs
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full diagram.

---

## 🔧 Tools

### Read / Lookup
| Tool | Purpose | Failure Modes |
|------|---------|---------------|
| `get_order(order_id)` | Order details, status, timestamps | Timeout, stale cache |
| `get_customer(email)` | Customer profile, tier, history | Partial response |
| `get_product(product_id)` | Product metadata, warranty | Malformed JSON |
| `check_refund_eligibility(order_id)` | Eligibility + reason (may throw) | Rate limiting |
| `search_knowledge_base(query)` | Policy & FAQ keyword search | Empty results |

### Write / Act
| Tool | Purpose | Safety |
|------|---------|--------|
| `issue_refund(order_id, amount)` | **IRREVERSIBLE** — processes refund | Requires `check_refund_eligibility` first. Double-refund protection. Amount validation. |
| `send_reply(ticket_id, message)` | Sends response to customer | Network error recovery |
| `escalate(ticket_id, summary, priority)` | Routes to human with context | Priority validation |
| `cancel_order(order_id)` | Cancels processing orders | State validation |

### Failure Recovery

Every tool is wrapped with `@resilient_tool`:
- **3 retries** with exponential backoff + jitter
- Catches: `TimeoutError`, `ValueError`, `ConnectionError`, `KeyError`, `TypeError`
- Full audit trail of failures and recoveries
- Graceful degradation (never crashes)

See [FAILURE_ANALYSIS.md](FAILURE_ANALYSIS.md) for detailed failure scenarios.

---

## 🧠 Agent Design

### Classification (No LLM)

Pure keyword-based rules across 8 categories:

| Category | Keywords |
|----------|----------|
| `refund_return` | "refund", "return" |
| `order_cancellation` | "cancel", "cancellation" |
| `order_status` | "where is my order", "tracking" |
| `damaged_defective` | "broken", "cracked", "defective" |
| `wrong_item` | "wrong size", "wrong colour" |
| `replacement_request` | "replacement" + "not a refund" |
| `general_query` | "what is your", "return policy" |
| `ambiguous` | Vague text + no order ID |

### Urgency Rules

| Condition | Urgency |
|-----------|---------|
| Threatening language or social engineering | **High** |
| VIP tier (tier ≥ 3) | **High** |
| Premium tier (tier 2) | **Medium** |
| Damaged/defective or wrong item | **Medium** |
| Everything else | **Low** |

### Concurrency

```python
with ThreadPoolExecutor(max_workers=8) as executor:
    futures = {executor.submit(resolve_ticket, t): i for i, t in enumerate(tickets)}
    for future in as_completed(futures):
        report = future.result()
```

Thread safety via:
- `threading.Lock()` on audit log writes
- Per-ticket simulated time (no global mutable state)
- Per-ticket `ReasoningChain` (no shared state)

---

## 📁 Output Files

| File | Description |
|------|-------------|
| `audit_log.json` | Every tool call with inputs, outputs, latency, error recovery |
| `resolution_report.json` | Per-ticket: classification, reasoning trace, confidence, decisions |

---

## 🔑 Key Edge Cases Handled

| Scenario | Ticket | How Agent Handles It |
|----------|--------|---------------------|
| **Social engineering** | TKT-018 | Customer claims "premium" tier but system shows "standard" → declined |
| **Threatening language** | TKT-017 | Professional response, flagged for review |
| **VIP exception** | TKT-005 | VIP with pre-approved extended return → honored |
| **Expired return window** | TKT-002 | 15-day smart watch window expired → declined with explanation |
| **Warranty claim** | TKT-003 | Return expired but warranty active → escalated to warranty team |
| **Device registered** | TKT-013 | Non-returnable (registered online) → declined |
| **Unknown customer** | TKT-016 | Email not in system → asks for identification |
| **Ambiguous ticket** | TKT-020 | "my thing is broken" → asks clarifying questions |
| **High-value refund** | TKT-011 | > $200 → escalated for supervisor approval |
| **Inquiry vs. action** | TKT-014 | "thinking about returning" → informs process, doesn't initiate |

---

## 🧪 Design Constraint Compliance

| Constraint | Status | Evidence |
|------------|--------|----------|
| ≥3 tool calls per chain | ✅ 20/20 | Every handler: classify → customer → (lookup) → reply |
| Concurrent processing | ✅ 8 workers | `ThreadPoolExecutor`, 0.13s total |
| Tool failure recovery | ✅ 6/6 recovered | Timeout, malformed, rate-limit, network, partial, stale |
| Explainable decisions | ✅ DECISION: annotations | Every branch point logged with reasoning |
| Audit trail | ✅ 105 entries | `audit_log.json` with full input/output |
| No LLM | ✅ Pure Python | Standard library only, zero API calls |

---

## 📝 Author

Built as a professional autonomous support resolution platform.
