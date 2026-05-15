"""
NexusDesk Agent — Tools (v4: Security + Random Failures)
========================================================
Production-grade mocks with:
  - Input sanitization via security module
  - 6 scheduled failures + random/cascading failure modes
  - @resilient_tool with exponential backoff
  - Thread-safe audit with incremental persistence
"""

import threading
import random
import time
import functools
import copy
from datetime import datetime, timedelta
from data_manager import (
    get_customer_by_email, get_order_by_id, get_orders_by_customer_id,
    get_product_by_id, search_knowledge_base_text, update_order_status,
    append_audit_entry,
)
from security import sanitize_input, escape_html

# Thread-safe audit log
_audit_lock = threading.Lock()
AUDIT_LOG = []
_SIMULATED_NOW_MAP = {}
_now_lock = threading.Lock()
_eligibility_checked = {}
_elig_lock = threading.Lock()

# Config
try:
    from config import (ENABLE_FAILURE_SIMULATION, ENABLE_RANDOM_FAILURES,
                        RANDOM_FAILURE_PROBABILITY, TOOL_MAX_RETRIES, TOOL_BASE_DELAY_S)
except ImportError:
    ENABLE_FAILURE_SIMULATION = True
    ENABLE_RANDOM_FAILURES = True
    RANDOM_FAILURE_PROBABILITY = 0.05
    TOOL_MAX_RETRIES = 3
    TOOL_BASE_DELAY_S = 0.05


def set_simulated_now(dt: datetime, ticket_id: str = "__global__"):
    with _now_lock:
        _SIMULATED_NOW_MAP[ticket_id] = dt

def _now(ticket_id: str = "__global__"):
    with _now_lock:
        return _SIMULATED_NOW_MAP.get(ticket_id, datetime.utcnow())

def _log(tool_name, inputs, output, ticket_id="", attempt=1, recovered=False, error_msg=None, latency_ms=0):
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
    # Incremental persistence
    append_audit_entry(entry)
    return entry

def get_audit_log():
    with _audit_lock:
        return list(AUDIT_LOG)

def reset_audit_log():
    with _audit_lock:
        AUDIT_LOG.clear()
    with _now_lock:
        _SIMULATED_NOW_MAP.clear()
    with _elig_lock:
        _eligibility_checked.clear()
    with _failure_lock:
        _failure_tracker.clear()
        _failure_counts.clear()

# ─── FAILURE SIMULATION ENGINE ────────────────────────────
FAILURE_SCHEDULE = {
    ("get_order", "TKT-004"): {
        "type": "TIMEOUT",
        "message": "upstream connect error: connection timed out after 5000ms to order-service.internal:8080",
        "delay": 0.05,
    },
    ("get_product", "TKT-008"): {
        "type": "MALFORMED",
        "message": "JSON decode error: unexpected token at position 847 in response body from product-catalog-v2",
    },
    ("check_refund_eligibility", "TKT-013"): {
        "type": "RATE_LIMIT",
        "message": "429 Too Many Requests: rate limit exceeded (60 req/min). Retry-After: 2s",
        "delay": 0.02,
    },
    ("send_reply", "TKT-011"): {
        "type": "NETWORK_ERROR",
        "message": "ConnectionError: [Errno 111] Connection refused — notification-service.internal:443 is unreachable",
    },
    ("get_customer", "TKT-007"): {
        "type": "PARTIAL",
        "message": "Partial response from customer-service: field 'notes' missing from payload (possible schema mismatch)",
    },
    ("get_order", "TKT-009"): {
        "type": "STALE_DATA",
        "message": "Cache-Status: HIT, Age: 3600s — serving stale data from CDN edge",
    },
}

# Random failure types for unpredictable failures
RANDOM_FAILURE_TYPES = [
    {"type": "TIMEOUT", "message": "Random timeout: upstream service took too long to respond"},
    {"type": "NETWORK_ERROR", "message": "Random network error: transient connection reset by peer"},
    {"type": "RATE_LIMIT", "message": "Random rate limit: too many concurrent requests"},
    {"type": "MALFORMED", "message": "Random malformed response: unexpected content-type in payload"},
]

_failure_tracker = {}
_failure_counts = {}  # tool_name -> consecutive failure count (for cascading)
_failure_lock = threading.Lock()


def _check_failure(tool_name: str, ticket_id: str):
    if not ENABLE_FAILURE_SIMULATION:
        return None
    key = (tool_name, ticket_id)
    with _failure_lock:
        # Scheduled failures (first attempt only)
        if key in FAILURE_SCHEDULE and key not in _failure_tracker:
            _failure_tracker[key] = True
            return FAILURE_SCHEDULE[key]
        # Random failures
        if ENABLE_RANDOM_FAILURES and key not in _failure_tracker:
            # Cascading: increase probability if this tool recently failed
            cascade_boost = _failure_counts.get(tool_name, 0) * 0.02
            prob = RANDOM_FAILURE_PROBABILITY + cascade_boost
            if random.random() < prob:
                _failure_tracker[key] = True
                _failure_counts[tool_name] = _failure_counts.get(tool_name, 0) + 1
                return random.choice(RANDOM_FAILURE_TYPES)
    return None


def _raise_failure(spec):
    ftype = spec["type"]
    msg = spec["message"]
    if spec.get("delay"):
        time.sleep(spec["delay"])
    if ftype == "TIMEOUT": raise TimeoutError(msg)
    elif ftype == "MALFORMED": raise ValueError(msg)
    elif ftype == "RATE_LIMIT": raise ConnectionError(msg)
    elif ftype == "NETWORK_ERROR": raise ConnectionError(msg)
    elif ftype == "PARTIAL": raise KeyError(msg)
    elif ftype == "STALE_DATA": raise ValueError(msg)
    else: raise RuntimeError(msg)


# ─── @resilient_tool ──────────────────────────────────────
def resilient_tool(max_retries=None, base_delay=None):
    if max_retries is None: max_retries = TOOL_MAX_RETRIES
    if base_delay is None: base_delay = TOOL_BASE_DELAY_S

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tool_name = func.__name__
            ticket_id = kwargs.get("ticket_id", "")
            if not ticket_id:
                for arg in args:
                    if isinstance(arg, str) and arg.startswith("TKT"):
                        ticket_id = arg
                        break
            last_error = None
            last_error_type = None

            for attempt in range(1, max_retries + 1):
                t0 = time.time()
                try:
                    failure_spec = _check_failure(tool_name, ticket_id)
                    if failure_spec:
                        _raise_failure(failure_spec)
                    result = func(*args, **kwargs)
                    elapsed_ms = (time.time() - t0) * 1000
                    if attempt > 1:
                        _log(f"{tool_name}::recovery",
                             {"retry_attempt": attempt, "previous_error_type": last_error_type},
                             {"recovered": True, "previous_error": str(last_error)},
                             ticket_id, attempt=attempt, recovered=True,
                             error_msg=str(last_error), latency_ms=elapsed_ms)
                        # Reset cascading counter on recovery
                        with _failure_lock:
                            _failure_counts[tool_name] = max(0, _failure_counts.get(tool_name, 0) - 1)
                    return result
                except (TimeoutError, ValueError, ConnectionError, KeyError, TypeError, RuntimeError) as e:
                    elapsed_ms = (time.time() - t0) * 1000
                    last_error = e
                    last_error_type = type(e).__name__
                    _log(f"{tool_name}::failure",
                         {"attempt": attempt, "error_type": last_error_type, "args_preview": str(args[:2])[:200]},
                         {"success": False, "error": str(e), "error_type": last_error_type,
                          "will_retry": attempt < max_retries,
                          "next_retry_in_ms": round(base_delay * (2 ** (attempt - 1)) * 1000) if attempt < max_retries else None},
                         ticket_id, attempt=attempt, latency_ms=elapsed_ms)
                    if attempt < max_retries:
                        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.02)
                        time.sleep(delay)
                    continue

            return {"success": False, "error": f"FATAL: Tool '{tool_name}' failed after {max_retries} attempts.",
                    "last_error": str(last_error), "last_error_type": last_error_type,
                    "retries_exhausted": True, "recommendation": "Escalate to engineering — tool is degraded."}
        return wrapper
    return decorator


# ════════════════════════════════════════════════════════════
# TOOL 1: get_order
# ════════════════════════════════════════════════════════════
@resilient_tool()
def get_order(order_id: str, ticket_id: str = ""):
    order_id = sanitize_input(order_id)
    if not order_id or not order_id.startswith("ORD-"):
        result = {"success": False, "error": f"VALIDATION_ERROR: Invalid order_id format '{order_id}'.", "error_type": "ValidationError"}
        _log("get_order", {"order_id": order_id}, result, ticket_id)
        return result
    order = get_order_by_id(order_id)
    if order is None:
        result = {"success": False, "error": f"NOT_FOUND: Order '{order_id}' does not exist.", "error_type": "NotFoundError"}
    else:
        result = {"success": True, "data": copy.deepcopy(order), "source": "primary_db"}
    _log("get_order", {"order_id": order_id}, result, ticket_id)
    return result


# ════════════════════════════════════════════════════════════
# TOOL 2: get_customer
# ════════════════════════════════════════════════════════════
@resilient_tool()
def get_customer(email: str, ticket_id: str = ""):
    email = sanitize_input(email)
    if not email or "@" not in email:
        result = {"success": False, "error": f"VALIDATION_ERROR: Invalid email format '{email}'.", "error_type": "ValidationError"}
        _log("get_customer", {"email": email}, result, ticket_id)
        return result
    customer = get_customer_by_email(email)
    if customer is None:
        result = {"success": False, "error": f"NOT_FOUND: No customer account found for '{email}'.", "error_type": "NotFoundError"}
    else:
        result = {"success": True, "data": copy.deepcopy(customer), "source": "customer_db"}
    _log("get_customer", {"email": email}, result, ticket_id)
    return result


# ════════════════════════════════════════════════════════════
# TOOL 3: get_product
# ════════════════════════════════════════════════════════════
@resilient_tool()
def get_product(product_id: str, ticket_id: str = ""):
    product_id = sanitize_input(product_id)
    if not product_id or not product_id.startswith("P"):
        result = {"success": False, "error": f"VALIDATION_ERROR: Invalid product_id format '{product_id}'.", "error_type": "ValidationError"}
        _log("get_product", {"product_id": product_id}, result, ticket_id)
        return result
    product = get_product_by_id(product_id)
    if product is None:
        result = {"success": False, "error": f"NOT_FOUND: Product '{product_id}' not in catalog.", "error_type": "NotFoundError"}
    else:
        result = {"success": True, "data": copy.deepcopy(product), "source": "product_catalog_v2"}
    _log("get_product", {"product_id": product_id}, result, ticket_id)
    return result


# ════════════════════════════════════════════════════════════
# TOOL 4: check_refund_eligibility
# ════════════════════════════════════════════════════════════
@resilient_tool()
def check_refund_eligibility(order_id: str, ticket_id: str = ""):
    order_id = sanitize_input(order_id)
    if not order_id or not order_id.startswith("ORD-"):
        result = {"success": False, "eligible": False, "reason": f"VALIDATION_ERROR: Invalid order_id '{order_id}'."}
        _log("check_refund_eligibility", {"order_id": order_id}, result, ticket_id)
        return result

    order = get_order_by_id(order_id)
    if order is None:
        result = {"success": False, "eligible": False, "reason": f"Order '{order_id}' does not exist."}
        _log("check_refund_eligibility", {"order_id": order_id}, result, ticket_id)
        return result

    product = get_product_by_id(order["product_id"])
    now = _now(ticket_id)

    if order.get("refund_status") == "refunded":
        result = {"success": True, "eligible": False, "reason": "Refund has already been processed for this order.",
                  "refund_status": "refunded", "order": copy.deepcopy(order)}
        _mark_eligibility_checked(ticket_id, order_id)
        _log("check_refund_eligibility", {"order_id": order_id}, result, ticket_id)
        return result

    if order["status"] == "processing":
        result = {"success": True, "eligible": False, "reason": "Order has not been delivered yet. Consider cancellation instead.",
                  "order_status": "processing", "order": copy.deepcopy(order)}
        _mark_eligibility_checked(ticket_id, order_id)
        _log("check_refund_eligibility", {"order_id": order_id}, result, ticket_id)
        return result

    if order["status"] == "shipped":
        result = {"success": True, "eligible": False, "reason": "Order is in transit. Customer must wait for delivery.",
                  "order_status": "shipped", "order": copy.deepcopy(order)}
        _mark_eligibility_checked(ticket_id, order_id)
        _log("check_refund_eligibility", {"order_id": order_id}, result, ticket_id)
        return result

    # Return window check
    return_deadline = order.get("return_deadline")
    within_window = False
    if return_deadline:
        deadline_dt = datetime.strptime(return_deadline, "%Y-%m-%d")
        within_window = now <= deadline_dt

    notes_lower = (order.get("notes") or "").lower()
    device_registered = "registered online" in notes_lower
    non_returnable_note = "non-returnable" in notes_lower

    warranty_active = False
    if product and product.get("warranty_months", 0) > 0 and order.get("delivery_date"):
        delivery_dt = datetime.strptime(order["delivery_date"], "%Y-%m-%d")
        warranty_end = delivery_dt + timedelta(days=product["warranty_months"] * 30)
        warranty_active = now <= warranty_end

    if within_window and not device_registered and not non_returnable_note:
        result = {"success": True, "eligible": True, "reason": "Order is within the return window and eligible for refund.",
                  "return_deadline": return_deadline, "amount": order["amount"], "order": copy.deepcopy(order)}
    elif device_registered or non_returnable_note:
        result = {"success": True, "eligible": False, "reason": "Item is non-returnable (device registered online or policy restriction).",
                  "device_registered": device_registered, "return_deadline": return_deadline,
                  "within_window": within_window, "warranty_active": warranty_active, "order": copy.deepcopy(order)}
    elif not within_window and warranty_active:
        result = {"success": True, "eligible": False, "reason": "Return window has expired but warranty is still active. Handle as warranty claim.",
                  "return_deadline": return_deadline, "warranty_active": True, "order": copy.deepcopy(order)}
    else:
        result = {"success": True, "eligible": False, "reason": f"Return window expired on {return_deadline}. Not eligible for refund.",
                  "return_deadline": return_deadline, "within_window": False, "warranty_active": warranty_active, "order": copy.deepcopy(order)}

    _mark_eligibility_checked(ticket_id, order_id)
    _log("check_refund_eligibility", {"order_id": order_id}, result, ticket_id)
    return result


def _mark_eligibility_checked(ticket_id, order_id):
    with _elig_lock:
        _eligibility_checked[(ticket_id, order_id)] = True

def _was_eligibility_checked(ticket_id, order_id):
    with _elig_lock:
        return _eligibility_checked.get((ticket_id, order_id), False)


# ════════════════════════════════════════════════════════════
# TOOL 5: issue_refund — IRREVERSIBLE
# ════════════════════════════════════════════════════════════
@resilient_tool()
def issue_refund(order_id: str, amount: float, ticket_id: str = ""):
    order_id = sanitize_input(order_id)
    if not _was_eligibility_checked(ticket_id, order_id):
        result = {"success": False, "error": "SAFETY_VIOLATION: check_refund_eligibility() must be called before issue_refund().",
                  "error_type": "SafetyViolation", "guard": "eligibility_precheck"}
        _log("issue_refund", {"order_id": order_id, "amount": amount, "guard": "BLOCKED"}, result, ticket_id)
        return result

    if amount is None or amount <= 0:
        result = {"success": False, "error": f"VALIDATION_ERROR: Refund amount must be > 0. Got: {amount}", "error_type": "ValidationError"}
        _log("issue_refund", {"order_id": order_id, "amount": amount}, result, ticket_id)
        return result

    order = get_order_by_id(order_id)
    if order is None:
        result = {"success": False, "error": f"NOT_FOUND: Order '{order_id}' not found.", "error_type": "NotFoundError"}
        _log("issue_refund", {"order_id": order_id, "amount": amount}, result, ticket_id)
        return result

    if order.get("refund_status") == "refunded":
        result = {"success": False, "error": f"DUPLICATE_REFUND: Order {order_id} was already refunded.", "error_type": "DuplicateRefundError"}
        _log("issue_refund", {"order_id": order_id, "amount": amount}, result, ticket_id)
        return result

    if amount > order["amount"]:
        result = {"success": False, "error": f"AMOUNT_EXCEEDED: Refund ${amount:.2f} exceeds order total ${order['amount']:.2f}.", "error_type": "AmountExceededError"}
        _log("issue_refund", {"order_id": order_id, "amount": amount}, result, ticket_id)
        return result

    update_order_status(order_id, new_status="delivered", refund_status="refunded",
                        note_append=f"REFUND ISSUED: ${amount:.2f} on {_now(ticket_id).strftime('%Y-%m-%d')} by automated agent. [IRREVERSIBLE]")

    result = {"success": True, "message": f"Refund of ${amount:.2f} issued for order {order_id}.",
              "refund_amount": amount, "order_id": order_id, "irreversible": True,
              "payment_gateway": "stripe_mock", "transaction_id": f"TXN-{hash(order_id + ticket_id) % 100000:05d}"}
    _log("issue_refund", {"order_id": order_id, "amount": amount, "IRREVERSIBLE": True}, result, ticket_id)
    return result


# ════════════════════════════════════════════════════════════
# TOOL 6: send_reply — with HTML escaping
# ════════════════════════════════════════════════════════════
@resilient_tool()
def send_reply(ticket_id: str, message: str):
    # Sanitize message to prevent injection
    if not message or len(message.strip()) == 0:
        result = {"success": False, "error": "VALIDATION_ERROR: Reply message cannot be empty.", "error_type": "ValidationError"}
        _log("send_reply", {"ticket_id": ticket_id, "message_length": 0}, result, ticket_id)
        return result
    if len(message) > 10000:
        result = {"success": False, "error": "VALIDATION_ERROR: Reply exceeds 10,000 character limit.", "error_type": "ValidationError"}
        _log("send_reply", {"ticket_id": ticket_id, "message_length": len(message)}, result, ticket_id)
        return result

    # Escape any HTML in the message for safe rendering
    safe_message = escape_html(message) if "<" in message else message

    result = {"success": True, "ticket_id": ticket_id, "message_sent": message,
              "safe_message": safe_message, "channel": "email",
              "delivery_status": "queued", "estimated_delivery": "< 30s"}
    _log("send_reply", {"ticket_id": ticket_id, "message_length": len(message)}, result, ticket_id)
    return result


# ════════════════════════════════════════════════════════════
# TOOL 7: search_knowledge_base
# ════════════════════════════════════════════════════════════
@resilient_tool()
def search_knowledge_base(query: str, ticket_id: str = ""):
    query = sanitize_input(query)
    if not query or len(query.strip()) < 2:
        result = {"success": True, "results": [], "match_count": 0, "note": "Query too short."}
        _log("search_knowledge_base", {"query": query}, result, ticket_id)
        return result

    matches = search_knowledge_base_text(query)
    result = {"success": True, "results": matches, "match_count": len(matches),
              "search_engine": "keyword_v2", "index_freshness": "2024-03-15T00:00:00Z"}
    if not matches:
        result["note"] = "No articles matched the query."
    _log("search_knowledge_base", {"query": query[:100]}, result, ticket_id)
    return result


# ════════════════════════════════════════════════════════════
# TOOL 8: escalate
# ════════════════════════════════════════════════════════════
@resilient_tool()
def escalate(ticket_id: str, summary: str, priority: str):
    summary = sanitize_input(summary)
    valid_priorities = ("low", "medium", "high")
    if priority not in valid_priorities:
        result = {"success": False, "error": f"VALIDATION_ERROR: Invalid priority '{priority}'.", "error_type": "ValidationError"}
        _log("escalate", {"ticket_id": ticket_id, "priority": priority}, result, ticket_id)
        return result
    if not summary or len(summary.strip()) < 10:
        result = {"success": False, "error": "VALIDATION_ERROR: Summary must be at least 10 characters.", "error_type": "ValidationError"}
        _log("escalate", {"ticket_id": ticket_id, "summary": summary}, result, ticket_id)
        return result

    eta_map = {"high": "< 2 hours", "medium": "< 8 hours", "low": "< 24 hours"}
    result = {"success": True, "ticket_id": ticket_id, "escalated": True, "summary": summary,
              "priority": priority, "queue_position": random.randint(1, 15),
              "estimated_response": eta_map.get(priority, "< 24 hours"),
              "assigned_team": "tier2_support" if priority != "high" else "supervisor_queue"}
    _log("escalate", {"ticket_id": ticket_id, "summary": summary[:200], "priority": priority}, result, ticket_id)
    return result


# ════════════════════════════════════════════════════════════
# TOOL 9: cancel_order
# ════════════════════════════════════════════════════════════
@resilient_tool()
def cancel_order(order_id: str, ticket_id: str = ""):
    order_id = sanitize_input(order_id)
    if not order_id or not order_id.startswith("ORD-"):
        result = {"success": False, "error": f"VALIDATION_ERROR: Invalid order_id format '{order_id}'.", "error_type": "ValidationError"}
        _log("cancel_order", {"order_id": order_id}, result, ticket_id)
        return result

    order = get_order_by_id(order_id)
    if order is None:
        result = {"success": False, "error": f"NOT_FOUND: Order '{order_id}' not found.", "error_type": "NotFoundError"}
        _log("cancel_order", {"order_id": order_id}, result, ticket_id)
        return result

    if order["status"] != "processing":
        result = {"success": False, "error": f"INVALID_STATE: Order is '{order['status']}'. Only 'processing' orders can be cancelled.",
                  "error_type": "InvalidStateError", "current_status": order["status"]}
        _log("cancel_order", {"order_id": order_id}, result, ticket_id)
        return result

    update_order_status(order_id, new_status="cancelled", refund_status="refunded",
                        note_append=f"CANCELLED by automated agent on {_now(ticket_id).strftime('%Y-%m-%d')}. Auto-refund initiated.")

    result = {"success": True, "message": f"Order {order_id} cancelled. Refund of ${order['amount']:.2f} will be processed.",
              "order_id": order_id, "refund_amount": order["amount"],
              "previous_status": "processing", "new_status": "cancelled"}
    _log("cancel_order", {"order_id": order_id}, result, ticket_id)
    return result
