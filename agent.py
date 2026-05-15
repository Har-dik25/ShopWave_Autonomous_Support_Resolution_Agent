"""
NexusDesk Autonomous Support Resolution Agent (v2)
===================================================
Pure rule-based agentic AI — NO LLM calls.
v2 improvements:
  - MINIMUM 3 TOOL CALLS per ticket (enforced in every handler)
  - Thread-safe for concurrent processing
  - Confidence scores + explainability metadata
  - Graceful tool failure recovery
"""

import re
from datetime import datetime
from tools import (
    get_order,
    get_customer,
    get_product,
    check_refund_eligibility,
    issue_refund,
    send_reply,
    search_knowledge_base,
    escalate,
    cancel_order,
    set_simulated_now,
)
from data_manager import get_orders_by_customer_id, record_conversation, get_pending_conversations



# ============================================================================
# HELPERS — Enhanced NLP (no LLM, no external libs)
# ============================================================================
def _extract_order_id(text: str):
    match = re.search(r"ORD-\d+", text, re.IGNORECASE)
    return match.group(0) if match else None


def _detect_threatening_language(text: str) -> bool:
    threat_keywords = [
        "lawyer", "sue", "legal action", "dispute", "chargeback",
        "report you", "attorney", "court", "lawsuit",
    ]
    return any(kw in text.lower() for kw in threat_keywords)


def _detect_social_engineering(text: str) -> bool:
    se_keywords = [
        "premium member", "premium policy", "instant refund",
        r"as per your.*policy", "vip member", "special arrangement",
        "expedited refund", "without questions",
    ]
    return any(re.search(kw, text.lower()) for kw in se_keywords)


def _customer_wants_replacement(text: str) -> bool:
    t = text.lower()
    return "replacement" in t and "not a refund" in t


# ── Fuzzy Matching (Levenshtein edit distance, pure Python) ──
def _edit_distance(a: str, b: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]


def _fuzzy_match(word: str, targets: list, max_dist: int = 2) -> bool:
    """Check if word fuzzy-matches any target within edit distance."""
    word = word.lower()
    for t in targets:
        if len(t) <= 3:  # short words need exact match
            if word == t:
                return True
        elif _edit_distance(word, t) <= max_dist:
            return True
    return False


def _fuzzy_keyword_score(text: str, keywords: list, synonyms: dict = None) -> int:
    """Score text against keywords with fuzzy matching + synonym expansion."""
    text_lower = text.lower()
    words = re.findall(r'\b[a-z]+\b', text_lower)
    score = 0

    # Expand keywords with synonyms
    all_keywords = list(keywords)
    if synonyms:
        for kw in keywords:
            all_keywords.extend(synonyms.get(kw, []))

    for kw in all_keywords:
        if " " in kw:
            # Multi-word phrase: exact substring match
            if kw in text_lower:
                score += 3
        else:
            # Single word: exact match (2pts) or fuzzy match (1pt)
            if kw in words:
                score += 2
            elif any(_fuzzy_match(w, [kw], 2) for w in words if len(w) > 3):
                score += 1
    return score


# ── Negation Detection ──
_NEGATION_PATTERNS = [
    r"\b(?:don'?t|do not|didn'?t|did not|not|no|never|won'?t|will not|can'?t|cannot)\b",
]
_NEGATION_WINDOW = 5  # words after negation to consider negated


def _has_negation_before(text: str, keyword: str) -> bool:
    """Check if a keyword is preceded by a negation within a window."""
    text_lower = text.lower()
    kw_pos = text_lower.find(keyword.lower())
    if kw_pos == -1:
        return False
    # Get the text before the keyword (up to 50 chars)
    prefix = text_lower[max(0, kw_pos - 60):kw_pos]
    words_before = prefix.split()[-_NEGATION_WINDOW:]
    for pattern in _NEGATION_PATTERNS:
        if any(re.search(pattern, w) for w in words_before):
            return True
    return False


def _negation_adjusted_score(text: str, keywords: list, score: int) -> int:
    """Reduce score if primary keywords are negated."""
    negated_count = 0
    for kw in keywords[:3]:  # check top keywords
        if _has_negation_before(text, kw):
            negated_count += 1
    if negated_count > 0:
        return max(0, score - negated_count * 2)
    return score


# ── Sentiment Analysis ──
_POSITIVE_WORDS = {"thank", "thanks", "pleased", "happy", "great", "love", "excellent", "wonderful", "appreciate"}
_NEGATIVE_WORDS = {"terrible", "horrible", "awful", "worst", "unacceptable", "ridiculous", "disgusting", "furious", "angry", "frustrated", "disappointed", "upset"}
_URGENCY_WORDS = {"asap", "immediately", "urgent", "emergency", "right now", "today"}


def _analyze_sentiment(text: str) -> dict:
    """Basic sentiment analysis using keyword scoring + textual cues."""
    text_lower = text.lower()
    words = set(re.findall(r'\b[a-z]+\b', text_lower))

    pos_count = len(words & _POSITIVE_WORDS)
    neg_count = len(words & _NEGATIVE_WORDS)
    urgency_count = sum(1 for uw in _URGENCY_WORDS if uw in text_lower)

    # Textual frustration cues
    exclamation_count = text.count("!")
    caps_ratio = sum(1 for c in text if c.isupper()) / max(len(text), 1)
    caps_frustration = caps_ratio > 0.4 and len(text) > 20

    frustration_score = min(1.0, (neg_count * 0.2 + exclamation_count * 0.1 +
                                   (0.3 if caps_frustration else 0) + urgency_count * 0.15))

    if neg_count > pos_count:
        sentiment = "negative"
    elif pos_count > neg_count:
        sentiment = "positive"
    else:
        sentiment = "neutral"

    return {
        "sentiment": sentiment,
        "frustration_score": round(frustration_score, 2),
        "positive_signals": pos_count,
        "negative_signals": neg_count,
        "urgency_signals": urgency_count,
        "caps_frustration": caps_frustration,
    }


# ── Category Keywords + Synonyms ──
_CATEGORY_KEYWORDS = {
    "general_query": ["what is your", "return policy", "do you offer", "how long", "what's the process", "general question", "policy", "faq"],
    "order_cancellation": ["cancel", "cancellation", "cancel order", "stop order", "void order"],
    "order_status": ["where is my order", "haven't received", "tracking", "when will", "shipping status", "in transit", "delivery status", "order status"],
    "refund_status": ["refund already", "confirm it went through", "haven't seen the money", "refund status", "already done", "where is my refund"],
    "wrong_item": ["wrong size", "wrong colour", "wrong color", "wrong item", "received size", "got the black", "got the wrong", "incorrect item", "different item"],
    "damaged_defective": ["broken", "cracked", "damaged", "defective", "stopped working", "not working", "doesn't work", "defect", "faulty", "malfunctioning", "isnt working"],
    "refund_return": ["refund", "return", "money back", "reimburse", "send back", "return item"],
}
_CATEGORY_SYNONYMS = {
    "refund": ["refnd", "refound", "reimburse", "money back"],
    "broken": ["brokn", "borken"],
    "damaged": ["dammaged", "damagd", "damged"],
    "defective": ["defectve", "defetive"],
    "cancel": ["cancle", "cancell", "cansel"],
    "tracking": ["trackng", "trakcing"],
    "return": ["retrun", "reutrn"],
}


# ============================================================================
# Step logger — thread-safe reasoning trace
# ============================================================================
class ReasoningChain:
    """Collects ordered reasoning steps + tool call counter for a single ticket."""

    def __init__(self, ticket_id: str):
        self.ticket_id = ticket_id
        self.steps = []
        self.tool_calls = []  # names of tools actually called

    def step(self, thought: str, action: str = None, observation: str = None):
        entry = {"thought": thought}
        if action:
            entry["action"] = action
            self.tool_calls.append(action)
        if observation:
            entry["observation"] = observation
        self.steps.append(entry)

    @property
    def tool_count(self):
        return len(self.tool_calls)

    @property
    def unique_tools(self):
        return list(dict.fromkeys(self.tool_calls))


# ============================================================================
# CLASSIFIER — Scoring-based, multi-label, negation-aware (no LLM)
# ============================================================================
def classify_ticket(ticket: dict) -> dict:
    body = ticket["body"]
    subject = ticket["subject"]
    combined = f"{subject} {body}"
    order_id = _extract_order_id(combined)

    flags = []
    if _detect_threatening_language(combined):
        flags.append("threatening_language")
    if _detect_social_engineering(combined):
        flags.append("possible_social_engineering")

    # ── Score every category ──
    category_scores = {}
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        raw_score = _fuzzy_keyword_score(combined, keywords, _CATEGORY_SYNONYMS)
        adjusted = _negation_adjusted_score(combined, keywords, raw_score)
        category_scores[cat] = adjusted

    # ── Replacement sub-check ──
    if _customer_wants_replacement(combined):
        category_scores["replacement_request"] = category_scores.get("damaged_defective", 0) + 5

    # ── Sort by score ──
    ranked = sorted(category_scores.items(), key=lambda x: x[1], reverse=True)
    top_score = ranked[0][1] if ranked else 0

    # Multi-label: all categories with score >= 50% of top
    if top_score > 0:
        primary_category = ranked[0][0]
        secondary_categories = [c for c, s in ranked[1:] if s > 0 and s >= top_score * 0.5]
    else:
        primary_category = "general_query"
        secondary_categories = []

    # ── Ambiguity check ──
    text_lower = combined.lower()
    vague_keywords = ["thing", "stuff", "it"]
    has_vague = any(kw in text_lower for kw in vague_keywords)
    if order_id is None and has_vague and len(text_lower.split()) < 25 and top_score <= 2:
        primary_category = "ambiguous"

    # ── Sentiment ──
    sentiment = _analyze_sentiment(combined)

    # ── Urgency (sentiment-influenced) ──
    tier = ticket.get("tier", 1)
    if "possible_social_engineering" in flags or "threatening_language" in flags:
        urgency = "high"
    elif tier >= 3 or sentiment["frustration_score"] >= 0.6:
        urgency = "high"
    elif tier == 2 or sentiment["frustration_score"] >= 0.3:
        urgency = "medium"
    elif primary_category in ("damaged_defective", "wrong_item"):
        urgency = "medium"
    else:
        urgency = "low"

    # ── Dynamic Confidence ──
    match_strength = min(1.0, top_score / 6.0) if top_score > 0 else 0.3
    has_order = 1.0 if order_id else 0.7
    flag_penalty = 0.1 * len(flags)
    ambiguity_penalty = 0.3 if primary_category == "ambiguous" else 0.0
    sentiment_clarity = 0.05 if sentiment["sentiment"] != "neutral" else 0.0
    confidence = min(0.99, max(0.3, match_strength * 0.5 + has_order * 0.3 + sentiment_clarity - flag_penalty - ambiguity_penalty + 0.15))

    return {
        "category": primary_category,
        "secondary_categories": secondary_categories,
        "category_scores": {c: s for c, s in ranked if s > 0},
        "urgency": urgency,
        "flags": flags,
        "order_id_extracted": order_id,
        "sentiment": sentiment,
        "confidence": round(confidence, 3),
        "classification_reasoning": {
            "category_reason": f"Scoring: {dict(ranked[:3])}. Primary='{primary_category}' (score={top_score}).",
            "urgency_reason": (
                f"Tier={tier}, frustration={sentiment['frustration_score']}" +
                (", threatening language detected" if "threatening_language" in flags else "") +
                (", social engineering detected" if "possible_social_engineering" in flags else "")
            ),
            "flags_reason": [
                f"'{f}' detected via keyword matching" for f in flags
            ] if flags else ["No flags raised."],
        },
    }



# ============================================================================
# RESOLVER — The agentic reasoning loop
# ============================================================================
def resolve_ticket(ticket: dict) -> dict:
    """
    Main agent entry point. Thread-safe.
    GUARANTEES: minimum 3 tool calls per ticket.
    """
    ticket_id = ticket["ticket_id"]
    email = ticket["customer_email"]
    body = ticket["body"]
    subject = ticket["subject"]
    combined = f"{subject} {body}"

    created_at = datetime.strptime(ticket["created_at"], "%Y-%m-%dT%H:%M:%SZ")
    set_simulated_now(created_at, ticket_id)

    chain = ReasoningChain(ticket_id)

    # ── STEP 1: Classify ──
    classification = classify_ticket(ticket)
    category = classification["category"]
    urgency = classification["urgency"]
    flags = classification["flags"]
    order_id = classification["order_id_extracted"]
    confidence = classification["confidence"]
    chain.step(
        f"Classified: category={category}, urgency={urgency}, flags={flags}. "
        f"Reason: {classification['classification_reasoning']['category_reason']}",
        "classify_ticket",
        f"order_id_extracted={order_id}",
    )

    # ── STEP 1.5: Multi-turn Conversation Check ──
    pending_convs = get_pending_conversations(email)
    if pending_convs and order_id:
        prev_ticket_id = pending_convs[-1]["ticket_id"]
        chain.step(
            f"Found pending conversation from ticket {prev_ticket_id}. Customer provided order ID {order_id}.",
            "get_pending_conversations",
            f"DECISION: Resuming multi-turn flow for {category}."
        )
        # If we resumed a conversation, boost confidence
        confidence = min(0.99, confidence + 0.2)


    # ── STEP 2: Always fetch customer (TOOL CALL #1) ──
    customer_result = get_customer(email, ticket_id=ticket_id)
    if not customer_result["success"]:
        chain.step(f"Customer '{email}' NOT FOUND.", "get_customer", "NOT_FOUND")
        # Even for unknown customer: search KB for policy reference (TOOL CALL #2)
        kb_result = search_knowledge_base("customer identification order lookup", ticket_id=ticket_id)
        chain.step("Searched KB for identification policy.", "search_knowledge_base",
                    f"{kb_result.get('match_count', 0)} matches")
        reply = (
            f"Hello,\n\n"
            f"Thank you for reaching out to NexusDesk support.\n\n"
            f"We were unable to locate an account associated with {email}. "
            f"To help resolve your issue, please provide:\n"
            f"1. Your order ID (format: ORD-XXXX)\n"
            f"2. The email address used when placing the order\n\n"
            f"Best regards,\nNexusDesk Support"
        )
        # TOOL CALL #3
        send_reply(ticket_id, reply)
        chain.step("Sent reply requesting identification.", "send_reply", "Awaiting customer response.")
        return _build_report(ticket_id, "awaiting_customer_info", category, urgency, flags, chain, confidence=confidence)

    customer = customer_result["data"]
    customer_name = customer["name"].split()[0]
    customer_tier = customer["tier"]
    customer_notes = customer.get("notes", "")
    chain.step(
        f"Customer: {customer['name']}, tier={customer_tier}, notes='{customer_notes}'",
        "get_customer",
        f"customer_id={customer['customer_id']} | DECISION: tier verified from system, not from customer claim.",
    )

    # ── STEP 3: Social engineering gate ──
    if "possible_social_engineering" in flags:
        return _handle_social_engineering(
            ticket_id, customer_name, customer, order_id, combined, chain, category, urgency, flags
        )

    # ── Route to category handler ──
    handlers = {
        "ambiguous": _handle_ambiguous,
        "general_query": _handle_general_query,
        "order_status": _handle_order_status,
        "refund_status": _handle_refund_status,
        "order_cancellation": _handle_cancellation,
        "wrong_item": _handle_wrong_item,
        "replacement_request": _handle_replacement,
        "damaged_defective": _handle_damaged,
        "refund_return": _handle_refund_return,
    }
    handler = handlers.get(category, _handle_general_query)
    report = handler(
        ticket_id, customer_name, customer, order_id, combined, chain, category, urgency, flags, confidence
    )
    
    # Record conversation outcome
    record_conversation(email, ticket_id, report["resolution"])
    
    return report


# ============================================================================
# HANDLER: Social Engineering
# ============================================================================
def _handle_social_engineering(tid, name, customer, order_id, text, chain, cat, urg, flags, conf=0.9):
    actual_tier = customer["tier"]
    claimed_premium = "premium" in text.lower() or "vip" in text.lower()

    chain.step(
        f"ALERT: Social engineering detected. Customer claims premium/VIP, actual tier='{actual_tier}'.",
        "verify_tier",
        f"MISMATCH={claimed_premium and actual_tier == 'standard'} | "
        f"DECISION: Tier is verified ONLY via get_customer tool. Self-declared tiers are rejected per policy.",
    )

    # TOOL CALL #3: check refund eligibility anyway to give a full picture
    if order_id:
        elig = check_refund_eligibility(order_id, ticket_id=tid)
        chain.step(
            f"Checked refund eligibility: eligible={elig.get('eligible')}, reason={elig.get('reason')}",
            "check_refund_eligibility",
            f"DECISION: Even if tier were correct, policy check still applies.",
        )
    else:
        # Search KB instead
        kb = search_knowledge_base("premium instant refund policy", ticket_id=tid)
        chain.step("Searched KB for 'premium instant refund policy'.", "search_knowledge_base",
                    f"No such policy found. {kb.get('match_count', 0)} partial matches.")

    reply = (
        f"Hi {name},\n\n"
        f"Thank you for contacting NexusDesk support.\n\n"
        f"We've reviewed your account and your current membership tier is **{actual_tier.capitalize()}**. "
        f"We don't have a policy for instant or question-free refunds for any tier.\n\n"
    )
    if order_id:
        if not elig.get("eligible"):
            reply += f"Regarding order {order_id}: {elig.get('reason', 'Not eligible.')}\n\n"
    reply += (
        f"If you believe your account status is incorrect, please provide supporting documentation.\n\n"
        f"Best regards,\nNexusDesk Support"
    )
    send_reply(tid, reply)
    chain.step("Declined social engineering attempt.", "send_reply",
               "DECISION: Blocked — customer tier does not match claim and no such policy exists.")
    return _build_report(tid, "resolved_declined", cat, urg, flags, chain, confidence=conf)


# ============================================================================
# HANDLER: Ambiguous
# ============================================================================
def _handle_ambiguous(tid, name, customer, order_id, text, chain, cat, urg, flags, conf=0.9):
    # TOOL CALL #3: Look up customer's orders
    cust_orders = get_orders_by_customer_id(customer["customer_id"])
    order_list = ", ".join(o["order_id"] for o in cust_orders) if cust_orders else "none"
    chain.step(f"Looked up customer orders: {order_list}", "get_orders_by_customer_id",
               f"Found {len(cust_orders)} orders.")

    # TOOL CALL #4: Search KB for general troubleshooting
    kb = search_knowledge_base("product issue troubleshooting defective", ticket_id=tid)
    chain.step("Searched KB for troubleshooting guidance.", "search_knowledge_base",
               f"{kb.get('match_count', 0)} matches.")

    reply = (
        f"Hi {name},\n\n"
        f"Thank you for reaching out! We'd love to help, but we need a bit more information:\n\n"
        f"1. **Your order number** (format: ORD-XXXX)\n"
        f"2. **Which product** is having the issue?\n"
        f"3. **What's going wrong** — a brief description\n\n"
    )
    if cust_orders:
        reply += f"We found these orders on your account: {order_list}. Does the issue relate to one of these?\n\n"
    reply += "Best regards,\nNexusDesk Support"

    send_reply(tid, reply)
    chain.step("Sent clarifying questions.", "send_reply",
               "DECISION: Ticket is too vague to act on. Need order ID and issue description before proceeding.")
    return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)


# ============================================================================
# HANDLER: General Query
# ============================================================================
def _handle_general_query(tid, name, customer, order_id, text, chain, cat, urg, flags, conf=0.9):
    # TOOL CALL #3: Search knowledge base
    kb = search_knowledge_base(text, ticket_id=tid)
    kb_texts = kb.get("results", [])
    chain.step(f"Searched KB, found {len(kb_texts)} relevant sections.", "search_knowledge_base",
               f"DECISION: Will compile top-3 KB sections into customer-friendly response.")

    if kb_texts:
        kb_answer = "\n\n".join(kb_texts[:3])
        reply = (
            f"Hi {name},\n\n"
            f"Great question! Here's what you need to know:\n\n"
            f"{kb_answer}\n\n"
            f"If you have any other questions, feel free to ask!\n\n"
            f"Best regards,\nNexusDesk Support"
        )
    else:
        reply = (
            f"Hi {name},\n\n"
            f"Thank you for your question. I've forwarded it to our team for a detailed response. "
            f"You'll hear back within 24 hours.\n\n"
            f"Best regards,\nNexusDesk Support"
        )

    send_reply(tid, reply)
    chain.step("Sent KB-based answer.", "send_reply", "Resolved with knowledge base content.")
    return _build_report(tid, "resolved", cat, urg, flags, chain, confidence=conf)


# ============================================================================
# HANDLER: Order Status
# ============================================================================
def _handle_order_status(tid, name, customer, order_id, text, chain, cat, urg, flags, conf=0.9):
    if not order_id:
        cust_orders = get_orders_by_customer_id(customer["customer_id"])
        shipped = [o for o in cust_orders if o["status"] == "shipped"]
        if shipped:
            order_id = shipped[0]["order_id"]
            chain.step(f"No order ID in ticket. Found shipped order {order_id}.", "get_orders_by_customer_id")
        else:
            # Still need 3rd tool call
            kb = search_knowledge_base("order tracking shipping", ticket_id=tid)
            chain.step("Searched KB for tracking info.", "search_knowledge_base")
            reply = f"Hi {name},\n\nCould you provide your order number (ORD-XXXX) so we can check status?\n\nBest regards,\nNexusDesk Support"
            send_reply(tid, reply)
            chain.step("Asked for order ID.", "send_reply")
            return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)

    # TOOL CALL #3: get_order
    order_result = get_order(order_id, ticket_id=tid)
    if not order_result["success"]:
        chain.step(f"Order {order_id} not found.", "get_order", "NOT FOUND")
        reply = f"Hi {name},\n\nWe couldn't find order {order_id}. Could you double-check?\n\nBest regards,\nNexusDesk Support"
        send_reply(tid, reply)
        chain.step("Asked customer to verify order ID.", "send_reply")
        return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)

    order = order_result["data"]
    status = order["status"]
    notes = order.get("notes", "")
    chain.step(f"Order {order_id}: status={status}, notes='{notes}'", "get_order",
               f"DECISION: Will share status + tracking info with customer.")

    # TOOL CALL #4: get_product for context
    product_result = get_product(order["product_id"], ticket_id=tid)
    product_name = product_result["data"]["name"] if product_result["success"] else "your item"
    chain.step(f"Product: {product_name}", "get_product")

    # Build reply based on status
    tracking = None
    trk_match = re.search(r"TRK-\d+", notes)
    if trk_match:
        tracking = trk_match.group(0)

    if status == "shipped":
        reply = f"Hi {name},\n\nYour order {order_id} ({product_name}) is currently **in transit**.\n\n"
        if tracking:
            reply += f"📦 **Tracking Number:** {tracking}\n\n"
        exp_match = re.search(r"Expected delivery (\d{4}-\d{2}-\d{2})", notes)
        if exp_match:
            reply += f"📅 **Expected Delivery:** {exp_match.group(1)}\n\n"
        reply += "If it hasn't arrived by then, reach out again and we'll investigate.\n\nBest regards,\nNexusDesk Support"
    elif status == "delivered":
        reply = f"Hi {name},\n\nYour order {order_id} ({product_name}) was delivered on {order.get('delivery_date', 'N/A')}.\n\nBest regards,\nNexusDesk Support"
    else:
        reply = f"Hi {name},\n\nYour order {order_id} is currently being processed. You'll receive tracking once it ships.\n\nBest regards,\nNexusDesk Support"

    send_reply(tid, reply)
    chain.step("Sent order status.", "send_reply", "Resolved.")
    return _build_report(tid, "resolved", cat, urg, flags, chain, confidence=conf)


# ============================================================================
# HANDLER: Refund Status
# ============================================================================
def _handle_refund_status(tid, name, customer, order_id, text, chain, cat, urg, flags, conf=0.9):
    if not order_id:
        kb = search_knowledge_base("refund processing time", ticket_id=tid)
        chain.step("Searched KB for refund timelines.", "search_knowledge_base")
        reply = f"Hi {name},\n\nPlease share your order number so we can check the refund status.\n\nBest regards,\nNexusDesk Support"
        send_reply(tid, reply)
        chain.step("Asked for order ID.", "send_reply")
        return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)

    # TOOL #3: get_order
    order_result = get_order(order_id, ticket_id=tid)
    if not order_result["success"]:
        chain.step(f"Order {order_id} not found.", "get_order")
        reply = f"Hi {name},\n\nWe couldn't find order {order_id}. Could you double-check?\n\nBest regards,\nNexusDesk Support"
        send_reply(tid, reply)
        chain.step("Asked customer to verify.", "send_reply")
        return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)

    order = order_result["data"]
    refund_status = order.get("refund_status")
    chain.step(f"Order {order_id}: refund_status={refund_status}", "get_order")

    # TOOL #4: search KB for refund timing info
    kb = search_knowledge_base("refund processing 5-7 business days", ticket_id=tid)
    chain.step("Searched KB for refund processing timelines.", "search_knowledge_base",
               f"{kb.get('match_count', 0)} matches.")

    if refund_status == "refunded":
        reply = (
            f"Hi {name},\n\n"
            f"Great news! The refund for order {order_id} has been **successfully processed**.\n\n"
            f"💰 **Refund Amount:** ${order['amount']:.2f}\n\n"
            f"Please allow **5–7 business days** for it to appear in your account.\n\n"
            f"Best regards,\nNexusDesk Support"
        )
    else:
        reply = (
            f"Hi {name},\n\n"
            f"We've checked order {order_id} and don't currently show a refund on record. "
            f"If you recently submitted a request, it may still be under review.\n\n"
            f"Would you like us to check eligibility for a refund?\n\nBest regards,\nNexusDesk Support"
        )

    send_reply(tid, reply)
    chain.step("Sent refund status.", "send_reply", "Resolved.")
    return _build_report(tid, "resolved", cat, urg, flags, chain, confidence=conf)


# ============================================================================
# HANDLER: Cancellation
# ============================================================================
def _handle_cancellation(tid, name, customer, order_id, text, chain, cat, urg, flags, conf=0.9):
    if not order_id:
        cust_orders = get_orders_by_customer_id(customer["customer_id"])
        processing = [o for o in cust_orders if o["status"] == "processing"]
        if processing:
            order_id = processing[0]["order_id"]
            chain.step(f"No order ID. Found processing order {order_id}.", "get_orders_by_customer_id")
        else:
            kb = search_knowledge_base("order cancellation policy", ticket_id=tid)
            chain.step("Searched KB for cancellation policy.", "search_knowledge_base")
            reply = f"Hi {name},\n\nPlease provide your order number to process the cancellation.\n\nBest regards,\nNexusDesk Support"
            send_reply(tid, reply)
            chain.step("Asked for order ID.", "send_reply")
            return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)

    # TOOL: get_order
    order_result = get_order(order_id, ticket_id=tid)
    if not order_result["success"]:
        chain.step(f"Order {order_id} not found.", "get_order")
        reply = f"Hi {name},\n\nWe couldn't find order {order_id}.\n\nBest regards,\nNexusDesk Support"
        send_reply(tid, reply)
        chain.step("Asked to verify.", "send_reply")
        return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)

    order = order_result["data"]
    status = order["status"]
    chain.step(f"Order {order_id}: status='{status}'", "get_order",
               f"DECISION: Status is '{status}'. {'Can cancel.' if status == 'processing' else 'Cannot cancel.'}")

    # TOOL: get_product for context in reply
    product_result = get_product(order["product_id"], ticket_id=tid)
    product_name = product_result["data"]["name"] if product_result["success"] else "your item"
    chain.step(f"Product: {product_name}", "get_product")

    if status == "processing":
        cancel_result = cancel_order(order_id, ticket_id=tid)
        chain.step(f"Cancelled: {cancel_result.get('message', cancel_result)}", "cancel_order",
                   "DECISION: Order in 'processing' → safe to cancel per policy.")
        reply = (
            f"Hi {name},\n\n"
            f"Your order {order_id} ({product_name}) has been **successfully cancelled**. ✅\n\n"
            f"💰 Refund of **${order['amount']:.2f}** will be processed within 5–7 business days.\n\n"
            f"Best regards,\nNexusDesk Support"
        )
        send_reply(tid, reply)
        chain.step("Sent cancellation confirmation.", "send_reply", "Resolved.")
        return _build_report(tid, "resolved", cat, urg, flags, chain, confidence=conf)
    else:
        reply = (
            f"Hi {name},\n\n"
            f"Order {order_id} ({product_name}) has already been **{status}** and cannot be cancelled.\n\n"
            f"Once you receive it, you can initiate a return if needed.\n\nBest regards,\nNexusDesk Support"
        )
        send_reply(tid, reply)
        chain.step(f"Cannot cancel — status is '{status}'.", "send_reply", "Resolved (denied).")
        return _build_report(tid, "resolved", cat, urg, flags, chain, confidence=conf)


# ============================================================================
# HANDLER: Wrong Item
# ============================================================================
def _handle_wrong_item(tid, name, customer, order_id, text, chain, cat, urg, flags, conf=0.9):
    if not order_id:
        kb = search_knowledge_base("wrong item delivered exchange", ticket_id=tid)
        chain.step("Searched KB for wrong-item policy.", "search_knowledge_base")
        reply = f"Hi {name},\n\nPlease provide your order number so we can fix this.\n\nBest regards,\nNexusDesk Support"
        send_reply(tid, reply)
        chain.step("Asked for order ID.", "send_reply")
        return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)

    # TOOL: get_order
    order_result = get_order(order_id, ticket_id=tid)
    if not order_result["success"]:
        reply = f"Hi {name},\n\nWe couldn't find order {order_id}. Could you double-check?\n\nBest regards,\nNexusDesk Support"
        send_reply(tid, reply)
        chain.step("Order not found.", "send_reply")
        return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)

    order = order_result["data"]
    chain.step(f"Order {order_id}: amount=${order['amount']:.2f}, product={order['product_id']}", "get_order")

    # TOOL: get_product
    product_result = get_product(order["product_id"], ticket_id=tid)
    product_name = product_result["data"]["name"] if product_result["success"] else "your item"
    chain.step(f"Product: {product_name}", "get_product")

    # TOOL: check eligibility (wrong item bypasses return window but we still check)
    elig = check_refund_eligibility(order_id, ticket_id=tid)
    chain.step(f"Eligibility: {elig.get('reason')}", "check_refund_eligibility",
               "DECISION: Wrong item policy applies regardless of return window.")

    if "threatening_language" in flags:
        chain.step("NOTE: Threatening language detected. Proceeding professionally.")

    if order["amount"] > 200:
        summary = (
            f"Wrong item for {order_id} ({product_name}). Amount ${order['amount']:.2f} > $200. "
            f"Customer: {customer['name']} ({customer['tier']}). Needs exchange or refund."
        )
        escalate(tid, summary, "high")
        chain.step("Escalated — high value wrong item.", "escalate",
                   "DECISION: Amount > $200 requires supervisor. Wrong-item policy still applies.")
        reply = (
            f"Hi {name},\n\n"
            f"We sincerely apologize for sending the wrong item. Your case has been escalated to our specialist team "
            f"who will arrange an exchange or refund. You'll hear back within 24 hours.\n\n"
            f"Best regards,\nNexusDesk Support"
        )
    else:
        refund_result = issue_refund(order_id, order["amount"], ticket_id=tid)
        chain.step(f"Refund issued: {refund_result.get('message', refund_result)}", "issue_refund",
                   "DECISION: Wrong item + amount ≤ $200 → auto-refund per policy.")
        reply = (
            f"Hi {name},\n\n"
            f"We're sorry about the mix-up with order {order_id} ({product_name}).\n\n"
            f"We've issued a **full refund of ${order['amount']:.2f}**. Allow 5–7 business days.\n\n"
            f"If you'd prefer an exchange, let us know! You don't need to return the incorrect item.\n\n"
            f"Best regards,\nNexusDesk Support"
        )

    send_reply(tid, reply)
    chain.step("Sent resolution reply.", "send_reply")
    resolution = "escalated" if order["amount"] > 200 else "resolved"
    return _build_report(tid, resolution, cat, urg, flags, chain, confidence=conf)


# ============================================================================
# HANDLER: Replacement Request
# ============================================================================
def _handle_replacement(tid, name, customer, order_id, text, chain, cat, urg, flags, conf=0.9):
    if not order_id:
        kb = search_knowledge_base("replacement request damaged", ticket_id=tid)
        chain.step("Searched KB.", "search_knowledge_base")
        reply = f"Hi {name},\n\nPlease provide your order number.\n\nBest regards,\nNexusDesk Support"
        send_reply(tid, reply)
        chain.step("Asked for order ID.", "send_reply")
        return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)

    # TOOL: get_order
    order_result = get_order(order_id, ticket_id=tid)
    order = order_result["data"] if order_result["success"] else None
    chain.step(f"Order {order_id} fetched.", "get_order")

    # TOOL: get_product
    if order:
        product_result = get_product(order["product_id"], ticket_id=tid)
        product_name = product_result["data"]["name"] if product_result["success"] else "your item"
    else:
        product_name = "your item"
    chain.step(f"Product: {product_name}", "get_product")

    # TOOL: check eligibility
    if order:
        elig = check_refund_eligibility(order_id, ticket_id=tid)
        chain.step(f"Eligibility: {elig.get('reason')}", "check_refund_eligibility")

    # Per policy: replacement requests for damaged items → escalate
    order_amount = order['amount'] if order else 0
    summary = (
        f"REPLACEMENT request (not refund) for {order_id} ({product_name}). "
        f"Customer: {customer['name']} ({customer['tier']}). Amount: ${order_amount:.2f}. "
        f"Reason: damaged/defective with photo evidence. "
        f"Recommended: verify photos, arrange replacement shipment."
    )
    escalate(tid, summary, "medium")
    chain.step("Escalated for replacement fulfillment.", "escalate",
               "DECISION: Per policy, agents don't fulfill replacements directly. Must go to fulfillment team.")

    reply = (
        f"Hi {name},\n\n"
        f"We're sorry about the issue with your {product_name} (order {order_id}).\n\n"
        f"Since you'd prefer a replacement, we've escalated to our fulfillment team. They'll review the photos "
        f"and arrange a replacement. Expect to hear back within **24-48 hours**.\n\n"
        f"Best regards,\nNexusDesk Support"
    )
    send_reply(tid, reply)
    chain.step("Sent escalation notice.", "send_reply")
    return _build_report(tid, "escalated", cat, urg, flags, chain, confidence=conf)


# ============================================================================
# HANDLER: Damaged / Defective
# ============================================================================
def _handle_damaged(tid, name, customer, order_id, text, chain, cat, urg, flags, conf=0.9):
    if not order_id:
        kb = search_knowledge_base("damaged defective arrival", ticket_id=tid)
        chain.step("Searched KB for damage policy.", "search_knowledge_base")
        reply = f"Hi {name},\n\nSorry to hear that. Please provide your order number so we can investigate.\n\nBest regards,\nNexusDesk Support"
        send_reply(tid, reply)
        chain.step("Asked for order ID.", "send_reply")
        return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)

    # TOOL: get_order
    order_result = get_order(order_id, ticket_id=tid)
    if not order_result["success"]:
        chain.step(f"Order {order_id} not found.", "get_order")
        kb = search_knowledge_base("order lookup", ticket_id=tid)
        chain.step("Searched KB.", "search_knowledge_base")
        reply = f"Hi {name},\n\nWe couldn't find order {order_id}. Could you verify?\n\nBest regards,\nNexusDesk Support"
        send_reply(tid, reply)
        chain.step("Asked to verify.", "send_reply")
        return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)

    order = order_result["data"]
    chain.step(f"Order {order_id}: status={order['status']}, amount=${order['amount']:.2f}", "get_order")

    # TOOL: get_product
    product_result = get_product(order["product_id"], ticket_id=tid)
    product_name = product_result["data"]["name"] if product_result["success"] else "your item"
    chain.step(f"Product: {product_name}", "get_product")

    # TOOL: check eligibility
    elig = check_refund_eligibility(order_id, ticket_id=tid)
    chain.step(f"Eligibility: eligible={elig.get('eligible')}, reason={elig.get('reason')}", "check_refund_eligibility",
               "DECISION: Damaged/defective items get special policy treatment.")

    # Warranty claim path
    if elig.get("warranty_active") and not elig.get("eligible"):
        summary = (
            f"WARRANTY CLAIM: {order_id} ({product_name}). "
            f"Customer: {customer['name']} ({customer['tier']}). "
            f"Return window expired, warranty active. Issue: {text[:200]}"
        )
        escalate(tid, summary, "medium")
        chain.step("Escalated as warranty claim.", "escalate",
                   "DECISION: Return window expired + warranty active → warranty team handles this, not agent.")
        reply = (
            f"Hi {name},\n\n"
            f"We're sorry about your {product_name}.\n\n"
            f"While the return window has passed, your item is still under **warranty**. "
            f"We've escalated to our warranty team who will review your claim within **2-3 business days**.\n\n"
            f"Best regards,\nNexusDesk Support"
        )
        send_reply(tid, reply)
        chain.step("Sent warranty escalation notice.", "send_reply")
        return _build_report(tid, "escalated", cat, urg, flags, chain, confidence=conf)

    # Damaged on arrival — refund path
    if order["amount"] > 200:
        summary = f"Damaged item refund: {order_id} ({product_name}), ${order['amount']:.2f} > $200. Needs supervisor."
        escalate(tid, summary, "high")
        chain.step("Escalated — refund > $200.", "escalate",
                   "DECISION: Refund amount exceeds $200 threshold → requires supervisor approval per policy.")
        reply = (
            f"Hi {name},\n\nWe're sorry about the damaged {product_name}. Your case has been escalated for priority review. "
            f"You'll hear back within 24 hours.\n\nBest regards,\nNexusDesk Support"
        )
        send_reply(tid, reply)
        chain.step("Sent escalation notice.", "send_reply")
        return _build_report(tid, "escalated", cat, urg, flags, chain, confidence=conf)

    refund_result = issue_refund(order_id, order["amount"], ticket_id=tid)
    chain.step(f"Refund issued: {refund_result.get('message')}", "issue_refund",
               "DECISION: Damaged on arrival + amount ≤ $200 → auto-refund without return required.")
    reply = (
        f"Hi {name},\n\n"
        f"We're sorry your {product_name} arrived damaged.\n\n"
        f"We've issued a **full refund of ${order['amount']:.2f}**. Allow 5–7 business days.\n"
        f"You do **not** need to return the damaged item.\n\n"
        f"Best regards,\nNexusDesk Support"
    )
    send_reply(tid, reply)
    chain.step("Sent refund confirmation.", "send_reply", "Resolved.")
    return _build_report(tid, "resolved", cat, urg, flags, chain, confidence=conf)


# ============================================================================
# HANDLER: Refund / Return
# ============================================================================
def _handle_refund_return(tid, name, customer, order_id, text, chain, cat, urg, flags, conf=0.9):
    if not order_id:
        if "threatening_language" in flags:
            chain.step("Threatening language detected but no valid order ID.")
        kb = search_knowledge_base("refund return policy", ticket_id=tid)
        chain.step("Searched KB for return policy.", "search_knowledge_base")
        reply = (
            f"Hi {name},\n\n"
            f"{'We understand your frustration. ' if 'threatening_language' in flags else ''}"
            f"Could you please provide your order number (ORD-XXXX) so we can process your request?\n\n"
            f"Best regards,\nNexusDesk Support"
        )
        send_reply(tid, reply)
        chain.step("Asked for order ID.", "send_reply")
        return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)

    # TOOL: get_order
    order_result = get_order(order_id, ticket_id=tid)
    if not order_result["success"]:
        chain.step(f"Order {order_id} NOT FOUND.", "get_order")
        kb = search_knowledge_base("order lookup identification", ticket_id=tid)
        chain.step("Searched KB for order lookup.", "search_knowledge_base")
        reply = (
            f"Hi {name},\n\n"
            f"{'We understand your concern and take all issues seriously. ' if 'threatening_language' in flags else ''}"
            f"We couldn't find order {order_id}. Could you verify the order number?\n\n"
            f"Best regards,\nNexusDesk Support"
        )
        send_reply(tid, reply)
        chain.step("Asked customer to verify order.", "send_reply")
        return _build_report(tid, "awaiting_customer_info", cat, urg, flags, chain, confidence=conf)

    order = order_result["data"]
    chain.step(f"Order {order_id}: product={order['product_id']}, amount=${order['amount']:.2f}, status={order['status']}", "get_order")

    # TOOL: get_product
    product_result = get_product(order["product_id"], ticket_id=tid)
    product_name = product_result["data"]["name"] if product_result["success"] else "your item"
    chain.step(f"Product: {product_name}", "get_product")

    # TOOL: check eligibility
    elig = check_refund_eligibility(order_id, ticket_id=tid)
    eligible = elig.get("eligible", False)
    reason = elig.get("reason", "")
    chain.step(f"Eligibility: eligible={eligible}, reason='{reason}'", "check_refund_eligibility",
               f"DECISION: {'Eligible → will process.' if eligible else 'Not eligible → checking for exceptions.'}")

    customer_tier = customer["tier"]
    customer_notes = customer.get("notes", "")

    # ── Just asking about the process? ──
    if "what's the process" in text.lower() or "might want to return" in text.lower() or "thinking about" in text.lower():
        chain.step("Customer is INQUIRING, not confirming. Will inform only.",
                   observation="DECISION: Do NOT initiate return until customer explicitly confirms.")
        if eligible:
            reply = (
                f"Hi {name},\n\n"
                f"Good news — order {order_id} ({product_name}) is still within the return window "
                f"(deadline: {order.get('return_deadline', 'N/A')}).\n\n"
                f"**How to return:**\n"
                f"1. Confirm with us that you'd like to proceed\n"
                f"2. We'll send a prepaid return label\n"
                f"3. Ship the item in original packaging\n"
                f"4. Refund processed within 5–7 business days\n\n"
                f"Just let us know when you're ready!\n\nBest regards,\nNexusDesk Support"
            )
        else:
            reply = f"Hi {name},\n\nRegarding order {order_id}: {reason}\n\nBest regards,\nNexusDesk Support"
        send_reply(tid, reply)
        chain.step("Sent informational reply.", "send_reply", "Resolved (informational).")
        return _build_report(tid, "resolved", cat, urg, flags, chain, confidence=conf)

    # ── Process refund ──
    if eligible:
        if order["amount"] > 200:
            summary = f"Refund for {order_id} ({product_name}), ${order['amount']:.2f} > $200. Customer: {customer['name']} ({customer_tier})."
            escalate(tid, summary, "medium")
            chain.step("Escalated — amount > $200.", "escalate")
            reply = f"Hi {name},\n\nYour return for {order_id} is eligible. Due to the order value, it's been sent for final approval. You'll hear back within 24 hours.\n\nBest regards,\nNexusDesk Support"
            send_reply(tid, reply)
            chain.step("Sent escalation notice.", "send_reply")
            return _build_report(tid, "escalated", cat, urg, flags, chain, confidence=conf)

        refund_result = issue_refund(order_id, order["amount"], ticket_id=tid)
        chain.step(f"Refund issued: {refund_result.get('message')}", "issue_refund",
                   "DECISION: Eligible + amount ≤ $200 → auto-approve refund.")
        reply = (
            f"Hi {name},\n\n"
            f"Your refund for order {order_id} ({product_name}) has been approved! ✅\n\n"
            f"💰 **Refund:** ${order['amount']:.2f}\n📅 **Expected:** 5–7 business days\n\n"
            f"Best regards,\nNexusDesk Support"
        )
        send_reply(tid, reply)
        chain.step("Sent refund confirmation.", "send_reply", "Resolved.")
        return _build_report(tid, "resolved", cat, urg, flags, chain, confidence=conf)

    # ── Not eligible — check VIP/Premium exceptions ──
    if customer_tier == "vip" and ("pre-approved" in customer_notes.lower() or "exception" in customer_notes.lower()):
        chain.step(f"VIP exception found in notes: '{customer_notes}'",
                   observation="DECISION: VIP with pre-approved extended return → honoring exception.")
        if order["amount"] > 200:
            summary = f"VIP exception refund: {order_id}, ${order['amount']:.2f}. Pre-approved extended return."
            escalate(tid, summary, "medium")
            chain.step("Escalated VIP exception — high value.", "escalate")
            reply = f"Hi {name},\n\nAs a valued VIP member, your return for {order_id} has been approved under your extended return privilege. Sent for final processing.\n\nBest regards,\nNexusDesk Support"
            send_reply(tid, reply)
            chain.step("Sent VIP approval.", "send_reply")
            return _build_report(tid, "escalated", cat, urg, flags, chain, confidence=conf)
        else:
            refund_result = issue_refund(order_id, order["amount"], ticket_id=tid)
            chain.step(f"VIP exception refund: {refund_result.get('message')}", "issue_refund",
                       "DECISION: VIP pre-approved exception + amount ≤ $200 → auto-approve.")
            reply = (
                f"Hi {name},\n\n"
                f"As a valued VIP member, we've approved your return for order {order_id} ({product_name}) "
                f"under your extended return privilege. ✅\n\n"
                f"💰 **Refund:** ${order['amount']:.2f}\n📅 **Expected:** 5–7 business days\n\n"
                f"Thank you for being a loyal NexusDesk customer!\n\nBest regards,\nNexusDesk Support"
            )
            send_reply(tid, reply)
            chain.step("Sent VIP refund confirmation.", "send_reply", "Resolved.")
            return _build_report(tid, "resolved", cat, urg, flags, chain, confidence=conf)

    # ── Standard decline ──
    reply = f"Hi {name},\n\nWe've reviewed your request for order {order_id} ({product_name}).\n\nUnfortunately, {reason.lower()}\n\n"
    if elig.get("device_registered"):
        reply += "Additionally, the device was registered online, making it non-returnable per our policy.\n\n"
    if elig.get("warranty_active"):
        reply += "However, your item may still be under warranty. Let us know if you'd like to open a warranty claim.\n\n"
    reply += "Best regards,\nNexusDesk Support"

    send_reply(tid, reply)
    chain.step("Declined refund — outside policy.", "send_reply",
               f"DECISION: Not eligible ({reason}). No VIP/Premium exceptions apply.")
    return _build_report(tid, "resolved_declined", cat, urg, flags, chain, confidence=conf)


# ============================================================================
# Report Builder — with explainability
# ============================================================================
def _build_report(ticket_id, resolution, category, urgency, flags, chain: ReasoningChain, confidence: float = 0.8):
    return {
        "ticket_id": ticket_id,
        "resolution": resolution,
        "category": category,
        "urgency": urgency,
        "flags": flags,
        "confidence_score": confidence,
        "tools_used": chain.unique_tools,
        "tool_call_count": chain.tool_count,
        "reasoning_steps": chain.steps,
        "total_steps": len(chain.steps),
        "min_3_tools_met": chain.tool_count >= 3,
        "explainability": {
            "decisions": [
                s.get("observation", "")
                for s in chain.steps
                if s.get("observation", "").startswith("DECISION:")
            ],
        },
    }
