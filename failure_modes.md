# 🔴 Failure Mode Analysis

This document describes **6 realistic failure scenarios** simulated in the NexusDesk agent, how each is triggered, what the system observes, and how it recovers.

---

## Failure 1: TIMEOUT — Order Service Under Load

| Property | Value |
|----------|-------|
| **Ticket** | TKT-004 |
| **Tool** | `get_order("ORD-1004")` |
| **Failure Type** | `TimeoutError` |
| **Root Cause** | Upstream order-service database is slow (complex JOINs on large order table) |
| **Error Message** | `upstream connect error: connection timed out after 5000ms to order-service.internal:8080` |

### What Happens

```
Attempt #1:  get_order("ORD-1004")
             └─► TimeoutError after 5000ms
             └─► Logged: { tool: "get_order::failure", error_type: "TimeoutError", will_retry: true }
             └─► Backoff: 50ms + jitter

Attempt #2:  get_order("ORD-1004")
             └─► SUCCESS: returns full order data
             └─► Logged: { tool: "get_order::recovery", recovered: true, previous_error: "TimeoutError" }
```

### Why This Is Realistic
Database timeouts are the #1 failure mode in microservice architectures. The order service joins `orders`, `order_items`, `shipments`, and `payments` tables — under batch load (20 concurrent ticket lookups), connection pool exhaustion or slow queries are common.

### How The Agent Handles It
1. `@resilient_tool` catches `TimeoutError`
2. Logs the failure with full context
3. Waits 50ms (exponential backoff)
4. Retries — succeeds on attempt #2
5. Logs recovery event
6. Continues processing the ticket as if nothing happened

---

## Failure 2: MALFORMED — Product Catalog Returns Corrupt JSON

| Property | Value |
|----------|-------|
| **Ticket** | TKT-008 |
| **Tool** | `get_product("P006")` |
| **Failure Type** | `ValueError` |
| **Root Cause** | Product catalog v2 migration left some records with corrupt warranty fields |
| **Error Message** | `JSON decode error: unexpected token at position 847 in response body from product-catalog-v2` |

### What Happens

```
Attempt #1:  get_product("P006")
             └─► ValueError: corrupt JSON response
             └─► Logged: { error_type: "ValueError", will_retry: true }
             └─► Backoff: 50ms

Attempt #2:  get_product("P006")
             └─► SUCCESS: returns valid product data
             └─► Logged: { recovered: true }
```

### Why This Is Realistic
Schema migrations in product catalogs are notoriously fragile. A half-migrated record where `warranty_months` is `"INVALID"` instead of `12` is a real-world scenario that crashes naive JSON parsers.

### How The Agent Handles It
The `@resilient_tool` decorator treats `ValueError` as a transient failure — the assumption is the service may return valid data on retry (e.g., if the corrupt response was cached and the cache expires). If all 3 retries fail, the agent gets a graceful error dict and can still proceed (e.g., using a fallback product name like "your item").

---

## Failure 3: RATE_LIMIT — Eligibility Service Throttled

| Property | Value |
|----------|-------|
| **Ticket** | TKT-013 |
| **Tool** | `check_refund_eligibility("ORD-1013")` |
| **Failure Type** | `ConnectionError` (HTTP 429) |
| **Root Cause** | Batch processing 20 tickets concurrently overwhelms the eligibility checker's rate limit (60 req/min) |
| **Error Message** | `429 Too Many Requests: rate limit exceeded (60 req/min). Retry-After: 2s` |

### What Happens

```
Attempt #1:  check_refund_eligibility("ORD-1013")
             └─► ConnectionError: 429 Too Many Requests
             └─► Logged: { error_type: "ConnectionError", next_retry_in_ms: 50 }
             └─► Backoff: 50ms (respects Retry-After in production)

Attempt #2:  check_refund_eligibility("ORD-1013")
             └─► SUCCESS: { eligible: false, reason: "non-returnable" }
             └─► Logged: { recovered: true }
```

### Why This Is Realistic
Rate limiting is a fundamental safety mechanism in payment/refund systems. When the agent processes 20 tickets concurrently (8 threads), multiple `check_refund_eligibility` calls can hit the rate limiter simultaneously. Real payment gateways (Stripe, PayPal) have strict rate limits.

### How The Agent Handles It
The exponential backoff naturally handles rate limits — each retry waits longer, allowing the rate limiter to reset. In production, we'd parse the `Retry-After` header and sleep that exact duration.

---

## Failure 4: NETWORK_ERROR — Notification Service Unreachable

| Property | Value |
|----------|-------|
| **Ticket** | TKT-011 |
| **Tool** | `send_reply(ticket_id, message)` |
| **Failure Type** | `ConnectionError` |
| **Root Cause** | The notification/email microservice crashed during a deployment |
| **Error Message** | `ConnectionError: [Errno 111] Connection refused — notification-service.internal:443 is unreachable` |

### What Happens

```
Attempt #1:  send_reply("TKT-011", "<escalation message>")
             └─► ConnectionError: service unreachable
             └─► Logged: { error_type: "ConnectionError", will_retry: true }
             └─► Backoff: 50ms

Attempt #2:  send_reply("TKT-011", "<escalation message>")
             └─► SUCCESS: message queued for delivery
             └─► Logged: { recovered: true, delivery_status: "queued" }
```

### Why This Is Realistic
Notification services (email, SMS, push) are the most fragile link in support systems. A rolling deployment can cause brief connection refusals. This is especially common with Kubernetes pod restarts.

---

## Failure 5: PARTIAL — Customer Service Drops Fields

| Property | Value |
|----------|-------|
| **Ticket** | TKT-007 |
| **Tool** | `get_customer("grace.n@email.com")` |
| **Failure Type** | `KeyError` |
| **Root Cause** | Schema mismatch between customer-service v1 and v2 — `notes` field not included in some responses |
| **Error Message** | `Partial response from customer-service: field 'notes' missing from payload (possible schema mismatch)` |

### What Happens

```
Attempt #1:  get_customer("grace.n@email.com")
             └─► KeyError: missing 'notes' field
             └─► Logged: { error_type: "KeyError", will_retry: true }
             └─► Backoff: 50ms

Attempt #2:  get_customer("grace.n@email.com")
             └─► SUCCESS: full customer object with all fields
             └─► Logged: { recovered: true }
```

### Why This Is Realistic
Partial responses happen during gradual API migrations. When service v2 is rolling out alongside v1, some pod instances return the new schema (without deprecated fields) while others return the old schema. This creates intermittent field-missing errors that are hard to reproduce.

---

## Failure 6: STALE_DATA — CDN Cache Returns Outdated Order

| Property | Value |
|----------|-------|
| **Ticket** | TKT-009 |
| **Tool** | `get_order("ORD-1009")` |
| **Failure Type** | `ValueError` |
| **Root Cause** | CDN edge cache hasn't invalidated since the order status changed from "processing" to "delivered + refunded" |
| **Error Message** | `Cache-Status: HIT, Age: 3600s — serving stale data from CDN edge (order-svc cache invalidation lag)` |

### What Happens

```
Attempt #1:  get_order("ORD-1009")
             └─► ValueError: stale cache detected (status shows "processing" but order is actually "delivered")
             └─► Logged: { error_type: "ValueError", stale_data: {...} }
             └─► Backoff: 50ms (forces cache bypass on retry)

Attempt #2:  get_order("ORD-1009")
             └─► SUCCESS: fresh data with correct status "delivered" + refund_status "refunded"
             └─► Logged: { recovered: true, source: "primary_db" }
```

### Why This Is Realistic
Cache invalidation is one of the two hard problems in computer science. CDN edge caches (CloudFront, Fastly) can serve stale data for minutes or hours if invalidation messages are delayed. For financial operations like refund processing, acting on stale status data could lead to duplicate refunds.

---

## Safety Guard: Irreversible Action Protection

In addition to transient failures, the agent also guards against **logical errors**:

### `issue_refund()` Safety Guards

| Guard | What It Prevents | Error If Triggered |
|-------|------------------|--------------------|
| **Eligibility Pre-Check** | Refund without policy verification | `SAFETY_VIOLATION: check_refund_eligibility() must be called first` |
| **Double-Refund** | Refunding the same order twice | `DUPLICATE_REFUND: Order already refunded` |
| **Amount Validation** | Refunding more than order total | `AMOUNT_EXCEEDED: Refund exceeds order total` |
| **Input Validation** | Negative or zero refund amounts | `VALIDATION_ERROR: Amount must be > 0` |

These guards ensure that even if the agent's decision logic has a bug, the tool layer prevents irreversible financial damage.

---

## Summary Table

| # | Type | Ticket | Tool | Recovery |
|---|------|--------|------|----------|
| 1 | TIMEOUT | TKT-004 | get_order | ✅ Retry #2 |
| 2 | MALFORMED | TKT-008 | get_product | ✅ Retry #2 |
| 3 | RATE_LIMIT | TKT-013 | check_refund_eligibility | ✅ Retry #2 |
| 4 | NETWORK_ERROR | TKT-011 | send_reply | ✅ Retry #2 |
| 5 | PARTIAL | TKT-007 | get_customer | ✅ Retry #2 |
| 6 | STALE_DATA | TKT-009 | get_order | ✅ Retry #2 |

**Recovery rate: 6/6 (100%)** — zero ticket processing failures despite 6 injected tool failures.
