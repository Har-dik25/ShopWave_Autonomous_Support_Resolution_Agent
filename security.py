"""
NexusDesk Agent — Security Module
==================================
Pure-stdlib security layer:
  - Input sanitization (HTML/script injection, SQL-like patterns)
  - PII masking (emails, names, phone numbers)
  - Rate limiting (in-memory sliding window)
  - API key validation
  - Content-Security-Policy helpers

Zero external dependencies.
"""

import re
import os
import time
import threading
import hashlib
from typing import Any, Dict, Optional


# ─────────────────────────────────────────────────────────────
# INPUT SANITIZATION
# ─────────────────────────────────────────────────────────────
_HTML_TAG_RE = re.compile(r"<[^>]+>", re.IGNORECASE)
_SCRIPT_RE = re.compile(
    r"(javascript\s*:|on\w+\s*=|<\s*script|<\s*/\s*script|eval\s*\(|document\.|window\.|alert\s*\()",
    re.IGNORECASE,
)
_SQL_INJECT_RE = re.compile(
    r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|UNION|ALTER|EXEC|EXECUTE)\b\s+"
    r"|(--|;)\s*(DROP|DELETE|UPDATE|SELECT|INSERT))",
    re.IGNORECASE,
)


def sanitize_input(text: str) -> str:
    """
    Sanitize user-supplied text for safe processing.

    Removes:
    - HTML tags
    - JavaScript/event handler injections
    - SQL injection patterns
    - Null bytes

    Preserves:
    - Normal text content, punctuation, unicode
    """
    if not text:
        return ""

    # Remove null bytes
    text = text.replace("\x00", "")

    # Strip HTML tags
    text = _HTML_TAG_RE.sub("", text)

    # Remove script/event handler patterns
    text = _SCRIPT_RE.sub("[SANITIZED]", text)

    # Remove SQL injection patterns
    text = _SQL_INJECT_RE.sub("[SANITIZED]", text)

    # Limit length to prevent DOS
    if len(text) > 50000:
        text = text[:50000] + "... [TRUNCATED]"

    return text.strip()


def escape_html(text: str) -> str:
    """Escape HTML special characters for safe rendering."""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


# ─────────────────────────────────────────────────────────────
# PII MASKING
# ─────────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})")
_PHONE_RE = re.compile(r"\b(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")


def mask_email(email: str) -> str:
    """Mask an email address: john.doe@email.com → j***e@e***l.com"""
    if not email or "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "***"
    else:
        masked_local = local[0] + "***" + local[-1]

    domain_parts = domain.rsplit(".", 1)
    if len(domain_parts) == 2:
        d_name, d_ext = domain_parts
        if len(d_name) <= 2:
            masked_domain = d_name[0] + "***." + d_ext
        else:
            masked_domain = d_name[0] + "***" + d_name[-1] + "." + d_ext
    else:
        masked_domain = domain

    return f"{masked_local}@{masked_domain}"


def mask_name(name: str) -> str:
    """Mask a person's name: John Doe → J*** D***"""
    if not name:
        return name
    parts = name.split()
    masked_parts = []
    for part in parts:
        if len(part) <= 1:
            masked_parts.append(part + "***")
        else:
            masked_parts.append(part[0] + "***")
    return " ".join(masked_parts)


def mask_pii_in_text(text: str) -> str:
    """Mask emails and phone numbers found within free-form text."""
    if not text:
        return text
    # Mask emails
    def _mask_email_match(m):
        full = m.group(0)
        return mask_email(full)
    text = _EMAIL_RE.sub(_mask_email_match, text)
    # Mask phone numbers
    text = _PHONE_RE.sub("[PHONE-MASKED]", text)
    return text


def mask_pii_in_dict(data: Any, mask_keys: set = None) -> Any:
    """
    Recursively mask PII in a dictionary/list structure.

    Masks:
    - Keys containing 'email' → email masking
    - Keys containing 'name' (but not 'product_name', 'tool_name') → name masking
    - String values containing email patterns → email masking
    """
    if mask_keys is None:
        mask_keys = {"email", "customer_email", "name", "customer_name"}

    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            key_lower = k.lower()
            if key_lower in ("email", "customer_email") and isinstance(v, str):
                result[k] = mask_email(v)
            elif key_lower in ("name", "customer_name") and isinstance(v, str):
                # Don't mask product names, tool names, etc.
                if not any(prefix in key_lower for prefix in ("product", "tool", "file", "server")):
                    result[k] = mask_name(v)
                else:
                    result[k] = v
            elif key_lower == "message_sent" and isinstance(v, str):
                result[k] = mask_pii_in_text(v)
            else:
                result[k] = mask_pii_in_dict(v, mask_keys)
        return result
    elif isinstance(data, list):
        return [mask_pii_in_dict(item, mask_keys) for item in data]
    elif isinstance(data, str):
        # Mask emails in any string value
        if _EMAIL_RE.search(data):
            return mask_pii_in_text(data)
        return data
    else:
        return data


# ─────────────────────────────────────────────────────────────
# RATE LIMITING (in-memory sliding window)
# ─────────────────────────────────────────────────────────────
class RateLimiter:
    """
    Thread-safe sliding window rate limiter.

    Usage:
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        if limiter.is_allowed("client_ip"):
            # process request
        else:
            # return 429
    """

    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: Dict[str, list] = {}
        self._lock = threading.Lock()

    def is_allowed(self, client_id: str) -> bool:
        """Check if the client is allowed to make a request."""
        now = time.time()
        with self._lock:
            if client_id not in self._requests:
                self._requests[client_id] = []

            # Remove expired timestamps
            window_start = now - self.window_seconds
            self._requests[client_id] = [
                ts for ts in self._requests[client_id] if ts > window_start
            ]

            if len(self._requests[client_id]) >= self.max_requests:
                return False

            self._requests[client_id].append(now)
            return True

    def remaining(self, client_id: str) -> int:
        """Get remaining requests for a client."""
        now = time.time()
        with self._lock:
            if client_id not in self._requests:
                return self.max_requests
            window_start = now - self.window_seconds
            active = [ts for ts in self._requests[client_id] if ts > window_start]
            return max(0, self.max_requests - len(active))

    def reset(self):
        """Reset all rate limit tracking."""
        with self._lock:
            self._requests.clear()


# ─────────────────────────────────────────────────────────────
# API KEY VALIDATION
# ─────────────────────────────────────────────────────────────
_API_KEY = os.environ.get("NEXUSDESK_API_KEY", "")


def validate_api_key(provided_key: str) -> bool:
    """
    Validate an API key against the configured key.

    If NEXUSDESK_API_KEY is not set, authentication is disabled (open access).
    Uses constant-time comparison to prevent timing attacks.
    """
    if not _API_KEY:
        # No key configured → open access (dev mode)
        return True

    if not provided_key:
        return False

    # Constant-time comparison
    return hashlib.sha256(provided_key.encode()).hexdigest() == hashlib.sha256(
        _API_KEY.encode()
    ).hexdigest()


def is_auth_enabled() -> bool:
    """Check if API key authentication is configured."""
    return bool(_API_KEY)


# ─────────────────────────────────────────────────────────────
# SOCIAL ENGINEERING DETECTION (Enhanced)
# ─────────────────────────────────────────────────────────────
def detect_social_engineering_advanced(text: str, actual_tier: str = None) -> Dict:
    """
    Enhanced social engineering detection beyond simple keyword matching.

    Checks:
    1. Keyword-based claim detection
    2. Authority impersonation patterns
    3. Urgency pressure tactics
    4. Tier mismatch (if actual_tier provided)
    5. Policy fabrication attempts

    Returns dict with detected patterns and risk score.
    """
    text_lower = text.lower()
    patterns_found = []
    risk_score = 0.0

    # 1. Self-declared privilege claims
    privilege_claims = [
        (r"(?:i am|i'm|as a)\s+(?:premium|vip|gold|platinum|elite)\s+(?:member|customer|user)", "self_declared_tier"),
        (r"premium\s+(?:policy|plan|service|benefit)", "policy_fabrication"),
        (r"instant\s+(?:refund|replacement|credit)", "instant_action_claim"),
        (r"(?:without|no)\s+(?:questions?|verification|return)", "no_verification_claim"),
        (r"special\s+(?:arrangement|deal|agreement|offer)", "special_arrangement"),
        (r"expedited\s+(?:refund|return|process)", "expedited_claim"),
    ]
    for pattern, label in privilege_claims:
        if re.search(pattern, text_lower):
            patterns_found.append(label)
            risk_score += 0.25

    # 2. Authority impersonation
    authority_patterns = [
        (r"(?:speak|talk)\s+(?:to|with)\s+(?:your|a)\s+(?:manager|supervisor|boss)", "authority_demand"),
        (r"(?:i know|friends? with)\s+(?:your|the)\s+(?:ceo|manager|owner)", "name_dropping"),
        (r"(?:i work|i'm from|employee at)\s+(?:your|this)\s+company", "insider_claim"),
    ]
    for pattern, label in authority_patterns:
        if re.search(pattern, text_lower):
            patterns_found.append(label)
            risk_score += 0.2

    # 3. Urgency/pressure tactics
    urgency_patterns = [
        (r"(?:right now|immediately|asap|urgent|emergency)", "urgency_pressure"),
        (r"(?:last chance|final warning|deadline)", "deadline_pressure"),
        (r"(?:or else|otherwise|if you don't)", "threat_pressure"),
    ]
    for pattern, label in urgency_patterns:
        if re.search(pattern, text_lower):
            patterns_found.append(label)
            risk_score += 0.15

    # 4. Tier mismatch
    if actual_tier:
        claimed_tiers = re.findall(r"\b(premium|vip|gold|platinum|elite)\b", text_lower)
        if claimed_tiers and actual_tier.lower() not in [t.lower() for t in claimed_tiers]:
            patterns_found.append("tier_mismatch")
            risk_score += 0.35

    # 5. Policy fabrication
    fabrication_patterns = [
        (r"(?:as per|according to)\s+(?:your|the)\s+(?:policy|website|terms)", "policy_reference"),
        (r"(?:promised|guaranteed|assured)\s+(?:me|by|that)", "promise_claim"),
    ]
    for pattern, label in fabrication_patterns:
        if re.search(pattern, text_lower):
            patterns_found.append(label)
            risk_score += 0.1

    return {
        "is_suspicious": risk_score >= 0.3,
        "risk_score": min(risk_score, 1.0),
        "patterns_detected": patterns_found,
        "pattern_count": len(patterns_found),
    }
