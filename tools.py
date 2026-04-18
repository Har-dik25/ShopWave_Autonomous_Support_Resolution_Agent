"""
ShopWave Agent — Tools (v3: Production-Grade Mocks)
=====================================================
Realistic failure simulation across 7 failure types:
  1. TIMEOUT       — tool takes too long, raises TimeoutError
  2. MALFORMED     — response has wrong types / corrupt values
  3. PARTIAL       — response missing required fields
  4. RATE_LIMIT    — 429-style "too many requests"
  5. NETWORK_ERROR — ConnectionError (service unreachable)
  6. VALIDATION    — bad input rejected
  7. STALE_DATA    — returns outdated/cached info

Safety features:
  - issue_refund enforces eligibility pre-check (IRREVERSIBLE guard)
  - Thread-safe audit log with per-call metadata
  - @resilient_tool decorator with exponential backoff + jitter
"""

import threading
import random
import time
import functools
import copy
from datetime import datetime, timedelta
from data_manager import (
    get_customer_by_email,
    get_order_by_id,
    get_orders_by_customer_id,
    get_product_by_id,
    search_knowledge_base_text,
    update_order_status,
)

# ─────────────────────────────────────────────────────────────
# Thread-safe audit log
# ─────────────────────────────────────────────────────────────
_audit_lock = threading.Lock()
AUDIT_LOG = []

_SIMULATED_NOW_MAP = {}  # ticket_id -> datetime
_now_lock = threading.Lock()

# Track which orders have had eligibility checked (per-ticket)
_eligibility_checked = {}  # (ticket_id, order_id) -> bool
_elig_lock = threading.Lock()


def set_simulated_now(dt: datetime, ticket_id: str = "__global__"):
    with _now_lock:
        _SIMULATED_NOW_MAP[ticket_id] = dt


def _now(ticket_id: str = "__global__"):
    with _now_lock:
        return _SIMULATED_NOW_MAP.get(ticket_id, datetime.utcnow())


def _log(tool_name: str, inputs: dict, output: dict, ticket_id: str = "",
         attempt: int = 1, recovered: bool = False, error_msg: str = None,
         latency_ms: float = 0):
    entry = {
        "timestamp": _now(ticket_id).isoformat() + "Z",
        "ticket_id": ticket_id,
        "tool": tool_name,
        "inputs": {k: (v if len(str(v)) < 500 else str(v)[:500] + "...") for k, v in inputs.items()},
        "output": output,
        "attempt": attempt,
        "latency_ms": round(latency_ms, 1),
        "recovered_from_failure": recovered,
    }
    if error_msg:
        entry["error_on_previous_attempt"] = error_msg
    with _audit_lock:
        AUDIT_LOG.append(entry)
    return entry


def get_audit_log():
    with _audit_lock:
        return list(AUDIT_LOG)


def reset_audit_log():
    """Reset the audit log and all simulated state for a fresh run."""
    with _audit_lock:
        AUDIT_LOG.clear()
    with _now_lock:
        _SIMULATED_NOW_MAP.clear()
    with _elig_lock:
        _eligibility_checked.clear()
    with _failure_lock:
        _failure_tracker.clear()


# Whether failure simulation is enabled
ENABLE_FAILURE_SIMULATION = True


# ─────────────────────────────────────────────────────────────
# FAILURE SIMULATION ENGINE
# ─────────────────────────────────────────────────────────────
# Each entry: (tool_name, ticket_id) -> failure_type
# These represent realistic production failures:
#   - API gateway timeout on a slow database query
#   - Corrupted JSON from a flaky microservice
#   - Rate limiter kicking in during batch processing
#   - A network blip to an external payment service
#   - Stale cache returning outdated order status

FAILURE_SCHEDULE = {
    # Timeout: order service is slow under load (realistic for DB joins)
    ("get_order", "TKT-004"): {
        "type": "TIMEOUT",
        "message": "upstream connect error: connection timed out after 5000ms to order-service.internal:8080",
        "delay": 0.05,  # simulate brief delay
    },
    # Malformed: product service returns corrupt warranty field
    ("get_product", "TKT-008"): {
        "type": "MALFORMED",
        "message": "JSON decode error: unexpected token at position 847 in response body from product-catalog-v2",
        "corrupt_data": {"product_id": "P006", "name": None, "warranty_months": "INVALID", "category": ""},
    },
    # Rate limit: eligibility checker under heavy batch load
    ("check_refund_eligibility", "TKT-013"): {
        "type": "RATE_LIMIT",
        "message": "429 Too Many Requests: rate limit exceeded (60 req/min). Retry-After: 2s",
        "delay": 0.02,
    },
    # Network error: email/reply service briefly unreachable
    ("send_reply", "TKT-011"): {
        "type": "NETWORK_ERROR",
        "message": "ConnectionError: [Errno 111] Connection refused — notification-service.internal:443 is unreachable",
    },
    # Partial response: customer service drops fields under load
    ("get_customer", "TKT-007"): {
        "type": "PARTIAL",
        "message": "Partial response from customer-service: field 'notes' missing from payload (possible schema mismatch)",
        "partial_data": {"customer_id": "C007", "name": "Grace Nguyen", "email": "grace.n@email.com",
                         "tier": "standard"},  # missing 'notes' field
    },
    # Stale data: cached order status is outdated
    ("get_order", "TKT-009"): {
        "type": "STALE_DATA",
        "message": "Cache-Status: HIT, Age: 3600s — serving stale data from CDN edge (order-svc cache invalidation lag)",
        "stale_data": {"order_id": "ORD-1009", "status": "processing",  # actually 'delivered' + 'refunded'
                       "customer_id": "C004", "product_id": "P009", "amount": 79.99,
                       "notes": "STALE CACHE — status may be outdated"},
    },
}

_failure_tracker = {}
_failure_lock = threading.Lock()


def _check_failure(tool_name: str, ticket_id: str):
    """
    Check if this (tool, ticket) should fail.
    Each failure fires ONLY on the first attempt — retries succeed.
    Returns the failure spec dict or None.
    """
    if not ENABLE_FAILURE_SIMULATION:
        return None
    key = (tool_name, ticket_id)
    with _failure_lock:
        if key in FAILURE_SCHEDULE and key not in _failure_tracker:
            _failure_tracker[key] = True
            return FAILURE_SCHEDULE[key]
    return None


def _raise_failure(failure_spec: dict):
    """Raise the appropriate exception for a failure type."""
    ftype = failure_spec["type"]
    msg = failure_spec["message"]

    if failure_spec.get("delay"):
        time.sleep(failure_spec["delay"])

    if ftype == "TIMEOUT":
        raise TimeoutError(msg)
    elif ftype == "MALFORMED":
        raise ValueError(msg)
    elif ftype == "RATE_LIMIT":
        raise ConnectionError(msg)
    elif ftype == "NETWORK_ERROR":
        raise ConnectionError(msg)
    elif ftype == "PARTIAL":
        raise KeyError(msg)
    elif ftype == "STALE_DATA":
        raise ValueError(msg)
    else:
        raise RuntimeError(msg)


# ─────────────────────────────────────────────────────────────
# @resilient_tool — Retry decorator with recovery & audit
# ─────────────────────────────────────────────────────────────
def resilient_tool(max_retries=3, base_delay=0.05):
    """
    Production-grade retry decorator.

    Behaviour:
    1. On each call, checks if a simulated failure should fire
    2. On failure: logs it, waits (exponential backoff + jitter), retries
    3. On success after failure: logs recovery event
    4. After all retries exhausted: returns graceful error dict (never crashes)

    Handles: TimeoutError, ValueError, ConnectionError, KeyError, TypeError, RuntimeError
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tool_name = func.__name__
            ticket_id = kwargs.get("ticket_id", "")
            if not ticket_id:
                # Try to find ticket_id from positional args
                for arg in args:
                    if isinstance(arg, str) and arg.startswith("TKT"):
                        ticket_id = arg
                        break

            last_error = None
            last_error_type = None

            for attempt in range(1, max_retries + 1):
                t0 = time.time()
                try:
                    # ── Failure injection point ──
                    failure_spec = _check_failure(tool_name, ticket_id)
                    if failure_spec:
                        _raise_failure(failure_spec)

                    # ── Real execution ──
                    result = func(*args, **kwargs)

                    elapsed_ms = (time.time() - t0) * 1000

                    # If recovered from prior attempt, log the recovery
                    if attempt > 1:
                        _log(
                            f"{tool_name}::recovery",
                            {"retry_attempt": attempt, "previous_error_type": last_error_type},
                            {"recovered": True, "previous_error": str(last_error)},
                            ticket_id,
                            attempt=attempt,
                            recovered=True,
                            error_msg=str(last_error),
                            latency_ms=elapsed_ms,
                        )
                    return result

                except (TimeoutError, ValueError, ConnectionError, KeyError, TypeError, RuntimeError) as e:
                    elapsed_ms = (time.time() - t0) * 1000
                    last_error = e
                    last_error_type = type(e).__name__

                    _log(
                        f"{tool_name}::failure",
                        {
                            "attempt": attempt,
                            "error_type": last_error_type,
                            "args_preview": str(args[:2])[:200],
                        },
                        {
                            "success": False,
                            "error": str(e),
                            "error_type": last_error_type,
                            "will_retry": attempt < max_retries,
                            "next_retry_in_ms": round(base_delay * (2 ** (attempt - 1)) * 1000) if attempt < max_retries else None,
                        },
                        ticket_id,
                        attempt=attempt,
                        latency_ms=elapsed_ms,
                    )

                    if attempt < max_retries:
                        # Exponential backoff with jitter
                        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.02)
                        time.sleep(delay)
                    continue

            # ── All retries exhausted ──
            return {
                "success": False,
                "error": f"FATAL: Tool '{tool_name}' failed after {max_retries} attempts.",
                "last_error": str(last_error),
                "last_error_type": last_error_type,
                "retries_exhausted": True,
                "recommendation": "Escalate to engineering — tool is degraded.",
            }
        return wrapper
    return decorator


# ========================================================================
# TOOL 1: get_order — Fetch order details
# ========================================================================
@resilient_tool(max_retries=3)
def get_order(order_id: str, ticket_id: str = ""):
    """
    Fetches order details by order_id.

    Mock behaviour:
    - Validates order_id format (must start with ORD-)
    - Returns full order object on success
    - Returns {success: False} if not found
    - May fail with TIMEOUT or STALE_DATA on scheduled tickets
    """
    # Input validation (realistic API behaviour)
    if not order_id or not order_id.startswith("ORD-"):
        result = {
            "success": False,
            "error": f"VALIDATION_ERROR: Invalid order_id format '{order_id}'. Expected: ORD-XXXX",
            "error_type": "ValidationError",
        }
        _log("get_order", {"order_id": order_id}, result, ticket_id)
        return result

    order = get_order_by_id(order_id)
    if order is None:
        result = {
            "success": False,
            "error": f"NOT_FOUND: Order '{order_id}' does not exist in the system.",
            "error_type": "NotFoundError",
        }
    else:
        result = {"success": True, "data": copy.deepcopy(order), "source": "primary_db"}

    _log("get_order", {"order_id": order_id}, result, ticket_id)
    return result


# ========================================================================
# TOOL 2: get_customer — Fetch customer profile
# ========================================================================
@resilient_tool(max_retries=3)
def get_customer(email: str, ticket_id: str = ""):
    """
    Fetches customer profile by email.

    Mock behaviour:
    - Validates email format
    - Returns full customer object
    - May fail with PARTIAL response (missing fields)
    """
    if not email or "@" not in email:
        result = {
            "success": False,
            "error": f"VALIDATION_ERROR: Invalid email format '{email}'.",
            "error_type": "ValidationError",
        }
        _log("get_customer", {"email": email}, result, ticket_id)
        return result

    customer = get_customer_by_email(email)
    if customer is None:
        result = {
            "success": False,
            "error": f"NOT_FOUND: No customer account found for '{email}'.",
            "error_type": "NotFoundError",
        }
    else:
        result = {"success": True, "data": copy.deepcopy(customer), "source": "customer_db"}

    _log("get_customer", {"email": email}, result, ticket_id)
    return result


# ========================================================================
# TOOL 3: get_product — Fetch product metadata
# ========================================================================
@resilient_tool(max_retries=3)
def get_product(product_id: str, ticket_id: str = ""):
    """
    Fetches product metadata including category, warranty, return policy.

    Mock behaviour:
    - Validates product_id format
    - Returns full product object
    - May fail with MALFORMED response (corrupt warranty field)
    """
    if not product_id or not product_id.startswith("P"):
        result = {
            "success": False,
            "error": f"VALIDATION_ERROR: Invalid product_id format '{product_id}'.",
            "error_type": "ValidationError",
        }
        _log("get_product", {"product_id": product_id}, result, ticket_id)
        return result

    product = get_product_by_id(product_id)
    if product is None:
        result = {
            "success": False,
            "error": f"NOT_FOUND: Product '{product_id}' not in catalog.",
            "error_type": "NotFoundError",
        }
    else:
        result = {"success": True, "data": copy.deepcopy(product), "source": "product_catalog_v2"}

    _log("get_product", {"product_id": product_id}, result, ticket_id)
    return result


# ========================================================================
# TOOL 4: check_refund_eligibility — Deterministic policy engine
# ========================================================================
@resilient_tool(max_retries=3)
def check_refund_eligibility(order_id: str, ticket_id: str = ""):
    """
    Deterministic refund-eligibility check against ShopWave policy.

    Checks: order existence, delivery status, return window, product
    returnability, refund history, warranty status, device registration.

    Side effect: records that eligibility was checked for this (ticket, order)
    so that issue_refund can verify the pre-check.

    May throw: RATE_LIMIT under heavy batch processing.
    """
    if not order_id or not order_id.startswith("ORD-"):
        result = {
            "success": False, "eligible": False,
            "reason": f"VALIDATION_ERROR: Invalid order_id '{order_id}'.",
        }
        _log("check_refund_eligibility", {"order_id": order_id}, result, ticket_id)
        return result

    order = get_order_by_id(order_id)
    if order is None:
        result = {
            "success": False, "eligible": False,
            "reason": f"Order '{order_id}' does not exist.",
        }
        _log("check_refund_eligibility", {"order_id": order_id}, result, ticket_id)
        return result

    product = get_product_by_id(order["product_id"])
    now = _now(ticket_id)

    # ── Already refunded? ──
    if order.get("refund_status") == "refunded":
        result = {
            "success": True, "eligible": False,
            "reason": "Refund has already been processed for this order.",
            "refund_status": "refunded", "order": copy.deepcopy(order),
        }
        _mark_eligibility_checked(ticket_id, order_id)
        _log("check_refund_eligibility", {"order_id": order_id}, result, ticket_id)
        return result

    # ── Not delivered yet? ──
    if order["status"] == "processing":
        result = {
            "success": True, "eligible": False,
            "reason": "Order has not been delivered yet. Consider cancellation instead.",
            "order_status": "processing", "order": copy.deepcopy(order),
        }
        _mark_eligibility_checked(ticket_id, order_id)
        _log("check_refund_eligibility", {"order_id": order_id}, result, ticket_id)
        return result

    if order["status"] == "shipped":
        result = {
            "success": True, "eligible": False,
            "reason": "Order is in transit. Customer must wait for delivery before requesting refund.",
            "order_status": "shipped", "order": copy.deepcopy(order),
        }
        _mark_eligibility_checked(ticket_id, order_id)
        _log("check_refund_eligibility", {"order_id": order_id}, result, ticket_id)
        return result

    # ── Return window check ──
    return_deadline = order.get("return_deadline")
    within_window = False
    if return_deadline:
        deadline_dt = datetime.strptime(return_deadline, "%Y-%m-%d")
        within_window = now <= deadline_dt

    # ── Non-returnable flags ──
    notes_lower = (order.get("notes") or "").lower()
    device_registered = "registered online" in notes_lower
    non_returnable_note = "non-returnable" in notes_lower

    # ── Warranty check ──
    warranty_active = False
    if product and product.get("warranty_months", 0) > 0 and order.get("delivery_date"):
        delivery_dt = datetime.strptime(order["delivery_date"], "%Y-%m-%d")
        warranty_end = delivery_dt + timedelta(days=product["warranty_months"] * 30)
        warranty_active = now <= warranty_end

    # ── Build result ──
    if within_window and not device_registered and not non_returnable_note:
        result = {
            "success": True, "eligible": True,
            "reason": "Order is within the return window and eligible for refund.",
            "return_deadline": return_deadline,
            "amount": order["amount"],
            "order": copy.deepcopy(order),
        }
    elif device_registered or non_returnable_note:
        result = {
            "success": True, "eligible": False,
            "reason": "Item is non-returnable (device registered online or policy restriction).",
            "device_registered": device_registered,
            "return_deadline": return_deadline,
            "within_window": within_window,
            "warranty_active": warranty_active,
            "order": copy.deepcopy(order),
        }
    elif not within_window and warranty_active:
        result = {
            "success": True, "eligible": False,
            "reason": "Return window has expired but warranty is still active. This should be handled as a warranty claim.",
            "return_deadline": return_deadline,
            "warranty_active": True,
            "order": copy.deepcopy(order),
        }
    else:
        result = {
            "success": True, "eligible": False,
            "reason": f"Return window expired on {return_deadline}. Not eligible for refund.",
            "return_deadline": return_deadline,
            "within_window": False,
            "warranty_active": warranty_active,
            "order": copy.deepcopy(order),
        }

    _mark_eligibility_checked(ticket_id, order_id)
    _log("check_refund_eligibility", {"order_id": order_id}, result, ticket_id)
    return result


def _mark_eligibility_checked(ticket_id: str, order_id: str):
    """Record that eligibility was verified for this (ticket, order)."""
    with _elig_lock:
        _eligibility_checked[(ticket_id, order_id)] = True


def _was_eligibility_checked(ticket_id: str, order_id: str) -> bool:
    """Check if eligibility was verified before attempting refund."""
    with _elig_lock:
        return _eligibility_checked.get((ticket_id, order_id), False)


# ========================================================================
# TOOL 5: issue_refund — IRREVERSIBLE action with safety guard
# ========================================================================
@resilient_tool(max_retries=3)
def issue_refund(order_id: str, amount: float, ticket_id: str = ""):
    """
    Issues a refund for an order. THIS IS IRREVERSIBLE.

    Safety guards:
    1. REQUIRES check_refund_eligibility to have been called first
       (tracked via _eligibility_checked). Refuses to proceed otherwise.
    2. Validates amount > 0 and <= order amount
    3. Prevents double-refund
    4. Logs full audit trail with IRREVERSIBLE marker

    This simulates a real payment gateway call that cannot be undone.
    """
    # ── GUARD 1: Eligibility pre-check ──
    if not _was_eligibility_checked(ticket_id, order_id):
        result = {
            "success": False,
            "error": "SAFETY_VIOLATION: check_refund_eligibility() must be called before issue_refund(). "
                     "Refund is IRREVERSIBLE — eligibility verification is mandatory.",
            "error_type": "SafetyViolation",
            "guard": "eligibility_precheck",
        }
        _log("issue_refund", {"order_id": order_id, "amount": amount, "guard": "BLOCKED"},
             result, ticket_id)
        return result

    # ── GUARD 2: Input validation ──
    if amount is None or amount <= 0:
        result = {
            "success": False,
            "error": f"VALIDATION_ERROR: Refund amount must be > 0. Got: {amount}",
            "error_type": "ValidationError",
        }
        _log("issue_refund", {"order_id": order_id, "amount": amount}, result, ticket_id)
        return result

    order = get_order_by_id(order_id)
    if order is None:
        result = {
            "success": False,
            "error": f"NOT_FOUND: Order '{order_id}' not found.",
            "error_type": "NotFoundError",
        }
        _log("issue_refund", {"order_id": order_id, "amount": amount}, result, ticket_id)
        return result

    # ── GUARD 3: Double-refund prevention ──
    if order.get("refund_status") == "refunded":
        result = {
            "success": False,
            "error": f"DUPLICATE_REFUND: Order {order_id} was already refunded. Cannot refund twice.",
            "error_type": "DuplicateRefundError",
        }
        _log("issue_refund", {"order_id": order_id, "amount": amount}, result, ticket_id)
        return result

    # ── GUARD 4: Amount validation ──
    if amount > order["amount"]:
        result = {
            "success": False,
            "error": f"AMOUNT_EXCEEDED: Refund amount ${amount:.2f} exceeds order total ${order['amount']:.2f}.",
            "error_type": "AmountExceededError",
        }
        _log("issue_refund", {"order_id": order_id, "amount": amount}, result, ticket_id)
        return result

    # ── EXECUTE REFUND (IRREVERSIBLE) ──
    update_order_status(
        order_id, new_status="delivered", refund_status="refunded",
        note_append=f"REFUND ISSUED: ${amount:.2f} on {_now(ticket_id).strftime('%Y-%m-%d')} by automated agent. [IRREVERSIBLE]",
    )

    result = {
        "success": True,
        "message": f"Refund of ${amount:.2f} issued for order {order_id}. Payment gateway confirmation pending (5-7 business days).",
        "refund_amount": amount,
        "order_id": order_id,
        "irreversible": True,
        "payment_gateway": "stripe_mock",
        "transaction_id": f"TXN-{hash(order_id + ticket_id) % 100000:05d}",
    }
    _log("issue_refund", {"order_id": order_id, "amount": amount, "IRREVERSIBLE": True},
         result, ticket_id)
    return result


# ========================================================================
# TOOL 6: send_reply — Send response to customer
# ========================================================================
@resilient_tool(max_retries=3)
def send_reply(ticket_id: str, message: str):
    """
    Sends a reply to the customer for a given ticket.

    Mock behaviour:
    - Validates message is non-empty
    - Simulates sending via notification service
    - May fail with NETWORK_ERROR (service unreachable)
    """
    if not message or len(message.strip()) == 0:
        result = {
            "success": False,
            "error": "VALIDATION_ERROR: Reply message cannot be empty.",
            "error_type": "ValidationError",
        }
        _log("send_reply", {"ticket_id": ticket_id, "message_length": 0}, result, ticket_id)
        return result

    if len(message) > 10000:
        result = {
            "success": False,
            "error": "VALIDATION_ERROR: Reply message exceeds 10,000 character limit.",
            "error_type": "ValidationError",
        }
        _log("send_reply", {"ticket_id": ticket_id, "message_length": len(message)}, result, ticket_id)
        return result

    result = {
        "success": True,
        "ticket_id": ticket_id,
        "message_sent": message,
        "channel": "email",
        "delivery_status": "queued",
        "estimated_delivery": "< 30s",
    }
    _log("send_reply", {"ticket_id": ticket_id, "message_length": len(message)}, result, ticket_id)
    return result


# ========================================================================
# TOOL 7: search_knowledge_base — Policy & FAQ search
# ========================================================================
@resilient_tool(max_retries=3)
def search_knowledge_base(query: str, ticket_id: str = ""):
    """
    Keyword-based knowledge base search (no LLM / no embeddings).

    Mock behaviour:
    - Searches knowledge-base.md by keyword relevance
    - Returns matching sections with relevance scores
    - Returns empty results if no match (not an error)
    """
    if not query or len(query.strip()) < 2:
        result = {
            "success": True,
            "results": [],
            "match_count": 0,
            "note": "Query too short — minimum 2 characters required.",
        }
        _log("search_knowledge_base", {"query": query}, result, ticket_id)
        return result

    matches = search_knowledge_base_text(query)
    result = {
        "success": True,
        "results": matches,
        "match_count": len(matches),
        "search_engine": "keyword_v2",
        "index_freshness": "2024-03-15T00:00:00Z",
    }
    if not matches:
        result["note"] = "No articles matched the query. Try broader keywords."

    _log("search_knowledge_base", {"query": query[:100]}, result, ticket_id)
    return result


# ========================================================================
# TOOL 8: escalate — Route to human with structured context
# ========================================================================
@resilient_tool(max_retries=3)
def escalate(ticket_id: str, summary: str, priority: str):
    """
    Escalates a ticket to a human agent with full context summary.

    Mock behaviour:
    - Validates priority level (low, medium, high)
    - Validates summary is non-empty
    - Returns queue position and estimated response time
    """
    valid_priorities = ("low", "medium", "high")
    if priority not in valid_priorities:
        result = {
            "success": False,
            "error": f"VALIDATION_ERROR: Invalid priority '{priority}'. Must be one of: {valid_priorities}",
            "error_type": "ValidationError",
        }
        _log("escalate", {"ticket_id": ticket_id, "priority": priority}, result, ticket_id)
        return result

    if not summary or len(summary.strip()) < 10:
        result = {
            "success": False,
            "error": "VALIDATION_ERROR: Escalation summary must be at least 10 characters.",
            "error_type": "ValidationError",
        }
        _log("escalate", {"ticket_id": ticket_id, "summary": summary}, result, ticket_id)
        return result

    eta_map = {"high": "< 2 hours", "medium": "< 8 hours", "low": "< 24 hours"}
    result = {
        "success": True,
        "ticket_id": ticket_id,
        "escalated": True,
        "summary": summary,
        "priority": priority,
        "queue_position": random.randint(1, 15),
        "estimated_response": eta_map.get(priority, "< 24 hours"),
        "assigned_team": "tier2_support" if priority != "high" else "supervisor_queue",
    }
    _log("escalate",
         {"ticket_id": ticket_id, "summary": summary[:200], "priority": priority},
         result, ticket_id)
    return result


# ========================================================================
# TOOL 9: cancel_order — Cancel a processing order
# ========================================================================
@resilient_tool(max_retries=3)
def cancel_order(order_id: str, ticket_id: str = ""):
    """
    Cancels an order that is still in 'processing' status.

    Mock behaviour:
    - Only cancels orders in 'processing' status
    - Returns error for shipped/delivered/cancelled orders
    - Triggers automatic refund via payment gateway
    """
    if not order_id or not order_id.startswith("ORD-"):
        result = {
            "success": False,
            "error": f"VALIDATION_ERROR: Invalid order_id format '{order_id}'.",
            "error_type": "ValidationError",
        }
        _log("cancel_order", {"order_id": order_id}, result, ticket_id)
        return result

    order = get_order_by_id(order_id)
    if order is None:
        result = {
            "success": False,
            "error": f"NOT_FOUND: Order '{order_id}' not found.",
            "error_type": "NotFoundError",
        }
        _log("cancel_order", {"order_id": order_id}, result, ticket_id)
        return result

    if order["status"] != "processing":
        result = {
            "success": False,
            "error": f"INVALID_STATE: Order is '{order['status']}'. Only 'processing' orders can be cancelled.",
            "error_type": "InvalidStateError",
            "current_status": order["status"],
        }
        _log("cancel_order", {"order_id": order_id}, result, ticket_id)
        return result

    update_order_status(
        order_id, new_status="cancelled", refund_status="refunded",
        note_append=f"CANCELLED by automated agent on {_now(ticket_id).strftime('%Y-%m-%d')}. Auto-refund initiated.",
    )

    result = {
        "success": True,
        "message": f"Order {order_id} cancelled. Refund of ${order['amount']:.2f} will be processed.",
        "order_id": order_id,
        "refund_amount": order["amount"],
        "previous_status": "processing",
        "new_status": "cancelled",
    }
    _log("cancel_order", {"order_id": order_id}, result, ticket_id)
    return result
