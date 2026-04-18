"""
ShopWave Agent - Data Manager
Loads and provides lookup functions for customers, orders, products, and tickets.
All data is loaded from local JSON files — no LLM calls.
"""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


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
    Update an order in-memory. Used for cancellations and refunds.
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
    return order


def search_knowledge_base_text(query: str):
    """
    Simple keyword search over the knowledge base markdown.
    Returns a list of matching paragraph blocks.
    No LLM — purely string matching.
    """
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

    # Score each block by keyword matches
    results = []
    for block in blocks:
        block_lower = block.lower()
        score = sum(1 for kw in keywords if kw in block_lower)
        if score > 0:
            results.append((score, block.strip()))

    results.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in results[:5]]  # top 5 relevant blocks
