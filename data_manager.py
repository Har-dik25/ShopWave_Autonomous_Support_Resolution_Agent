"""
NexusDesk Agent - Data Manager (v2: Persistence + Incremental Audit)
====================================================================
Loads and provides lookup functions for customers, orders, products, and tickets.
All data is loaded from local JSON files — no LLM calls.

v2 improvements:
  - JSON file persistence (state survives restarts)
  - Incremental audit log writing (not just at sweep end)
  - Thread-safe file I/O
  - State reset capability
"""

import json
import os
import threading
import copy
from datetime import datetime

from security import sanitize_input

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Persistence paths
STATE_FILE = os.path.join(DATA_DIR, "state.json")
AUDIT_FILE = os.path.join(BASE_DIR, "audit_log.json")

_file_lock = threading.Lock()
_persistence_enabled = True

try:
    from config import ENABLE_PERSISTENCE
    _persistence_enabled = ENABLE_PERSISTENCE
except ImportError:
    pass


def _load_json(filename):
    """Load a JSON file from the data directory."""
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_knowledge_base():
    """Load the knowledge-base markdown file as a string."""
    filepath = os.path.join(DATA_DIR, "knowledge-base.md")
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# In-memory data stores (loaded once at import time)
# ---------------------------------------------------------------------------
CUSTOMERS = _load_json("customers.json")
ORDERS = _load_json("orders.json")
PRODUCTS = _load_json("products.json")
TICKETS = _load_json("tickets.json")
KNOWLEDGE_BASE = _load_knowledge_base()

# Build fast-lookup indices
_CUSTOMERS_BY_EMAIL = {c["email"]: c for c in CUSTOMERS}
_CUSTOMERS_BY_ID = {c["customer_id"]: c for c in CUSTOMERS}
_ORDERS_BY_ID = {o["order_id"]: o for o in ORDERS}
_ORDERS_BY_CUSTOMER = {}
for o in ORDERS:
    _ORDERS_BY_CUSTOMER.setdefault(o["customer_id"], []).append(o)
_PRODUCTS_BY_ID = {p["product_id"]: p for p in PRODUCTS}
_TICKETS_BY_ID = {t["ticket_id"]: t for t in TICKETS}


# ---------------------------------------------------------------------------
# Persistence Layer — save/load mutable state
# ---------------------------------------------------------------------------
def save_state():
    """
    Persist the current mutable state (orders with refunds/cancellations) to disk.
    Thread-safe. Called after every mutation (refund, cancel).
    """
    if not _persistence_enabled:
        return
    state = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "orders": copy.deepcopy(ORDERS),
    }
    with _file_lock:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=str)
        except (IOError, OSError):
            pass  # Graceful degradation — persistence failure shouldn't crash the agent


def load_state():
    """
    Restore mutable state from disk on startup.
    If state file exists, merges saved order states into in-memory data.
    """
    if not _persistence_enabled:
        return False
    if not os.path.exists(STATE_FILE):
        return False
    try:
        with _file_lock:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
        saved_orders = state.get("orders", [])
        for saved_order in saved_orders:
            oid = saved_order.get("order_id")
            if oid and oid in _ORDERS_BY_ID:
                _ORDERS_BY_ID[oid].update(saved_order)
        return True
    except (json.JSONDecodeError, IOError, OSError, KeyError):
        return False


def clear_state():
    """Remove persisted state file for a fresh run."""
    if os.path.exists(STATE_FILE):
        with _file_lock:
            try:
                os.remove(STATE_FILE)
            except OSError:
                pass


def append_audit_entry(entry: dict):
    """
    Incrementally append a single audit entry to the audit log file.
    Thread-safe. Writes immediately (not buffered until sweep end).
    """
    if not _persistence_enabled:
        return
    with _file_lock:
        try:
            # Read existing
            entries = []
            if os.path.exists(AUDIT_FILE):
                try:
                    with open(AUDIT_FILE, "r", encoding="utf-8") as f:
                        entries = json.load(f)
                except (json.JSONDecodeError, IOError):
                    entries = []
            entries.append(entry)
            with open(AUDIT_FILE, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2, default=str)
        except (IOError, OSError):
            pass  # Graceful degradation


def reset_audit_file():
    """Clear the persistent audit log file."""
    with _file_lock:
        try:
            with open(AUDIT_FILE, "w", encoding="utf-8") as f:
                json.dump([], f)
        except (IOError, OSError):
            pass


# ---------------------------------------------------------------------------
# Conversation History (multi-turn stub)
# ---------------------------------------------------------------------------
_conversation_history = {}  # customer_email -> list of {ticket_id, resolution, timestamp}
_conv_lock = threading.Lock()


def record_conversation(customer_email: str, ticket_id: str, resolution: str):
    """Record a ticket resolution for multi-turn tracking."""
    with _conv_lock:
        if customer_email not in _conversation_history:
            _conversation_history[customer_email] = []
        _conversation_history[customer_email].append({
            "ticket_id": ticket_id,
            "resolution": resolution,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })


def get_conversation_history(customer_email: str) -> list:
    """Get previous ticket resolutions for a customer."""
    with _conv_lock:
        return list(_conversation_history.get(customer_email, []))


def get_pending_conversations(customer_email: str) -> list:
    """Get tickets where we're awaiting customer info."""
    history = get_conversation_history(customer_email)
    return [h for h in history if h["resolution"] == "awaiting_customer_info"]


# ---------------------------------------------------------------------------
# Public lookup helpers
# ---------------------------------------------------------------------------
def get_customer_by_email(email: str):
    """Return customer dict or None."""
    return _CUSTOMERS_BY_EMAIL.get(email)


def get_customer_by_id(customer_id: str):
    """Return customer dict or None."""
    return _CUSTOMERS_BY_ID.get(customer_id)


def get_order_by_id(order_id: str):
    """Return order dict or None."""
    return _ORDERS_BY_ID.get(order_id)


def get_orders_by_customer_id(customer_id: str):
    """Return list of orders for a customer."""
    return _ORDERS_BY_CUSTOMER.get(customer_id, [])


def get_product_by_id(product_id: str):
    """Return product dict or None."""
    return _PRODUCTS_BY_ID.get(product_id)


def get_ticket_by_id(ticket_id: str):
    """Return ticket dict or None."""
    return _TICKETS_BY_ID.get(ticket_id)


def get_all_tickets():
    """Return list of all tickets."""
    return TICKETS


def update_order_status(order_id: str, new_status: str, refund_status=None, note_append=None):
    """
    Update an order in-memory AND persist to disk.
    Used for cancellations and refunds.
    Returns the updated order dict or None.
    """
    order = _ORDERS_BY_ID.get(order_id)
    if order is None:
        return None
    order["status"] = new_status
    if refund_status is not None:
        order["refund_status"] = refund_status
    if note_append:
        order["notes"] = order.get("notes", "") + " " + note_append

    # Persist state after mutation
    save_state()
    return order


def search_knowledge_base_text(query: str):
    """
    Keyword search over the knowledge base markdown.
    Returns a list of matching paragraph blocks.
    No LLM — purely string matching with improved scoring.
    """
    # Sanitize query input
    query = sanitize_input(query)
    query_lower = query.lower()
    keywords = query_lower.split()

    # Split KB into section blocks by '---' or '##' headings
    blocks = []
    current_block = []
    for line in KNOWLEDGE_BASE.splitlines():
        if line.strip() == "---" or line.startswith("## "):
            if current_block:
                blocks.append("\n".join(current_block))
            current_block = [line] if not line.strip() == "---" else []
        else:
            current_block.append(line)
    if current_block:
        blocks.append("\n".join(current_block))

    # Score each block by keyword matches (weighted by match quality)
    results = []
    for block in blocks:
        block_lower = block.lower()
        score = 0
        for kw in keywords:
            if kw in block_lower:
                # Exact word boundary match scores higher
                import re
                if re.search(r'\b' + re.escape(kw) + r'\b', block_lower):
                    score += 2
                else:
                    score += 1
        if score > 0:
            results.append((score, block.strip()))

    results.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in results[:5]]  # top 5 relevant blocks
