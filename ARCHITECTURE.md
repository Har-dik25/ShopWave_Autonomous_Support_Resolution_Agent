# 🏗️ Architecture Diagram — ShopWave Autonomous Support Agent

## System Overview (1-Page)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           ENTRY POINT                                    │
│                          main.py                                         │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │              ThreadPoolExecutor (8 workers)                        │  │
│  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ │  │
│  │  │TKT-01│ │TKT-02│ │TKT-03│ │TKT-04│ │TKT-05│ │...   │ │TKT-20│ │  │
│  │  └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘ │  │
│  └─────┼────────┼────────┼────────┼────────┼────────┼────────┼──────┘  │
│        └────────┴────────┴────┬───┴────────┴────────┴────────┘         │
└───────────────────────────────┼──────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         AGENT CORE                                       │
│                        agent.py                                          │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │                    STEP 1: CLASSIFY                              │     │
│  │                                                                  │     │
│  │  ticket.subject + ticket.body                                    │     │
│  │        │                                                         │     │
│  │        ├─ Keyword Matching ──► Category (8 types)                │     │
│  │        ├─ Tier Check ────────► Urgency (low/med/high)            │     │
│  │        ├─ Threat Detection ──► Flag: threatening_language        │     │
│  │        └─ SE Detection ──────► Flag: possible_social_engineering │     │
│  └─────────────────────────────────────────────────────────────────┘     │
│        │                                                                 │
│        ▼                                                                 │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │                    STEP 2: FETCH CUSTOMER                        │     │
│  │                                                                  │     │
│  │  get_customer(email) ──► Verify tier from SYSTEM (not claim)     │     │
│  │        │                                                         │     │
│  │        ├─ NOT FOUND ──► Ask for identification ──► STOP          │     │
│  │        └─ FOUND ──────► Continue with verified tier + notes      │     │
│  └─────────────────────────────────────────────────────────────────┘     │
│        │                                                                 │
│        ▼                                                                 │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │                STEP 3: SOCIAL ENGINEERING GATE                   │     │
│  │                                                                  │     │
│  │  IF flag == 'possible_social_engineering':                       │     │
│  │    ├─ Compare claimed tier vs. system tier                       │     │
│  │    ├─ Check eligibility anyway (for completeness)                │     │
│  │    └─ DECLINE with explanation ──► STOP                          │     │
│  └─────────────────────────────────────────────────────────────────┘     │
│        │                                                                 │
│        ▼                                                                 │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │                  STEP 4: CATEGORY ROUTER                         │     │
│  │                                                                  │     │
│  │  category ──┬─► _handle_refund_return ──────► Eligibility check  │     │
│  │             ├─► _handle_cancellation ───────► Cancel if processing│     │
│  │             ├─► _handle_order_status ───────► Share tracking info │     │
│  │             ├─► _handle_refund_status ──────► Confirm refund     │     │
│  │             ├─► _handle_wrong_item ─────────► Auto-refund / esc  │     │
│  │             ├─► _handle_damaged ────────────► Refund / warranty   │     │
│  │             ├─► _handle_replacement ────────► Escalate           │     │
│  │             ├─► _handle_general_query ──────► KB search          │     │
│  │             └─► _handle_ambiguous ──────────► Ask clarification  │     │
│  └─────────────────────────────────────────────────────────────────┘     │
│        │                                                                 │
│        ▼                                                                 │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │               REASONING CHAIN (per ticket)                       │     │
│  │                                                                  │     │
│  │  ReasoningChain {                                                │     │
│  │    steps: [                                                      │     │
│  │      { thought: "...", action: "tool_name", observation: "..." } │     │
│  │      { thought: "...", action: "tool_name", observation: "..." } │     │
│  │      ...                                                         │     │
│  │    ]                                                             │     │
│  │    tool_calls: ["classify", "get_customer", "get_order", ...]    │     │
│  │  }                                                               │     │
│  └─────────────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          TOOL LAYER                                      │
│                         tools.py                                         │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │                  @resilient_tool DECORATOR                       │    │
│  │                                                                  │    │
│  │   ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐   │    │
│  │   │  Attempt #1  │───►│ Failure?     │───►│ Log + Backoff    │   │    │
│  │   └─────────────┘    │ (injected or │    │ (exponential     │   │    │
│  │         ▲             │  real)       │    │  + jitter)       │   │    │
│  │         │             └──────────────┘    └────────┬─────────┘   │    │
│  │         │                                          │             │    │
│  │         └──────────────────────────────────────────┘             │    │
│  │                    Retry up to 3x                                │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌────────────────────┐  ┌────────────────────┐  ┌──────────────────┐   │
│  │ READ TOOLS         │  │ WRITE TOOLS        │  │ SAFETY GUARDS    │   │
│  │                    │  │                    │  │                  │   │
│  │ • get_order        │  │ • issue_refund ⚠️  │  │ • Eligibility    │   │
│  │ • get_customer     │  │ • send_reply       │  │   pre-check      │   │
│  │ • get_product      │  │ • escalate         │  │ • Double-refund  │   │
│  │ • check_eligibility│  │ • cancel_order     │  │   prevention     │   │
│  │ • search_kb        │  │                    │  │ • Amount cap     │   │
│  └────────────────────┘  └────────────────────┘  │ • Input validate │   │
│                                                   └──────────────────┘   │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │                 FAILURE INJECTION ENGINE                          │    │
│  │                                                                  │    │
│  │  FAILURE_SCHEDULE = {                                            │    │
│  │    (get_order, TKT-004)     → TIMEOUT      (DB connection)       │    │
│  │    (get_product, TKT-008)   → MALFORMED    (corrupt JSON)        │    │
│  │    (check_elig, TKT-013)    → RATE_LIMIT   (429 too many)        │    │
│  │    (send_reply, TKT-011)    → NETWORK_ERR  (service down)        │    │
│  │    (get_customer, TKT-007)  → PARTIAL      (missing fields)      │    │
│  │    (get_order, TKT-009)     → STALE_DATA   (CDN cache hit)       │    │
│  │  }                                                               │    │
│  └──────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         DATA LAYER                                       │
│                       data_manager.py                                    │
│                                                                          │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────────┐  │
│  │ tickets    │  │ customers  │  │ orders     │  │ products         │  │
│  │ .json      │  │ .json      │  │ .json      │  │ .json            │  │
│  │            │  │            │  │            │  │                  │  │
│  │ 20 tickets │  │ 10 profiles│  │ 20 orders  │  │ 10 products      │  │
│  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └────────┬─────────┘  │
│        │               │               │                   │            │
│        ▼               ▼               ▼                   ▼            │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    INDEXED LOOKUPS                               │    │
│  │                                                                  │    │
│  │  _order_index     = { "ORD-1001": {...}, ... }                   │    │
│  │  _customer_email  = { "alice@email.com": {...}, ... }            │    │
│  │  _product_index   = { "P001": {...}, ... }                       │    │
│  │  _cust_orders     = { "C001": [order1, order2], ... }            │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                 knowledge-base.md                                │    │
│  │                                                                  │    │
│  │  Keyword search across 8 policy sections:                        │    │
│  │  Return windows, refund rules, tier exceptions,                  │    │
│  │  warranty claims, exchange policy, escalation triggers, FAQs     │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                       OUTPUT / STATE                                     │
│                                                                          │
│  ┌────────────────────┐  ┌─────────────────────────┐                    │
│  │  audit_log.json    │  │  resolution_report.json  │                    │
│  │                    │  │                          │                    │
│  │  105 entries       │  │  20 reports              │                    │
│  │  Per entry:        │  │  Per report:             │                    │
│  │  • timestamp       │  │  • ticket_id             │                    │
│  │  • tool name       │  │  • category + urgency    │                    │
│  │  • inputs          │  │  • resolution            │                    │
│  │  • outputs         │  │  • confidence score      │                    │
│  │  • attempt #       │  │  • reasoning steps       │                    │
│  │  • latency_ms      │  │  • DECISION annotations  │                    │
│  │  • recovered?      │  │  • tools used (≥3)       │                    │
│  │  • error details   │  │  • flags                 │                    │
│  └────────────────────┘  └─────────────────────────┘                    │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                  STATE MANAGEMENT                                │    │
│  │                                                                  │    │
│  │  Thread-Safe:                                                    │    │
│  │  • AUDIT_LOG ──────────── threading.Lock()                       │    │
│  │  • _SIMULATED_NOW_MAP ── threading.Lock() (per-ticket time)      │    │
│  │  • _eligibility_checked ─ threading.Lock() (refund safety)       │    │
│  │  • _failure_tracker ───── threading.Lock() (one-shot failures)   │    │
│  │                                                                  │    │
│  │  Per-Ticket (no shared mutation):                                │    │
│  │  • ReasoningChain instance                                       │    │
│  │  • Classification result                                         │    │
│  │  • Resolution report                                             │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
```

## Data Flow (Single Ticket)

```
ticket.json ──► classify_ticket() ──► get_customer() ──► [GATE: Social Engineering?]
                     │                      │                       │
                     ▼                      ▼                       ▼
              category: refund       customer: Alice          NO ──► Route to handler
              urgency: medium        tier: premium                      │
              flags: []              notes: "..."                       ▼
                                                               _handle_refund_return()
                                                                       │
                                                            ┌──────────┼──────────┐
                                                            ▼          ▼          ▼
                                                       get_order  get_product  check_elig
                                                            │          │          │
                                                            ▼          ▼          ▼
                                                       ORD-1001   "Headphones"  eligible=True
                                                            │
                                                            ▼
                                                    [DECISION POINT]
                                                     amount > $200?
                                                    /              \
                                                  YES              NO
                                                   │                │
                                                   ▼                ▼
                                              escalate()     issue_refund()
                                                   │                │
                                                   ▼                ▼
                                             send_reply()    send_reply()
                                                   │                │
                                                   ▼                ▼
                                             ESCALATED         RESOLVED
```
