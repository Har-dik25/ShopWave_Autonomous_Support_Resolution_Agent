"""
ShopWave Agent — Configuration (Production Readiness)
======================================================
All tunable parameters centralized. No magic numbers in business logic.
"""

import os

# ─── Agent Settings ────────────────────────────────────────
AGENT_NAME = "ShopWave Support Agent v3"
AGENT_VERSION = "3.0.0"
MAX_WORKERS = int(os.environ.get("SHOPWAVE_MAX_WORKERS", "8"))

# ─── Tool Retry Settings ──────────────────────────────────
TOOL_MAX_RETRIES = int(os.environ.get("SHOPWAVE_TOOL_RETRIES", "3"))
TOOL_BASE_DELAY_S = float(os.environ.get("SHOPWAVE_TOOL_DELAY", "0.05"))

# ─── Business Rules ───────────────────────────────────────
REFUND_ESCALATION_THRESHOLD = 200.00   # USD — refunds above this need supervisor
STANDARD_RETURN_WINDOW_DAYS = 30
ELECTRONICS_RETURN_WINDOW_DAYS = 15
ACCESSORIES_RETURN_WINDOW_DAYS = 60
REFUND_PROCESSING_DAYS = "5–7 business days"

# ─── Security ─────────────────────────────────────────────
MAX_REPLY_LENGTH = 10000               # chars
MAX_ESCALATION_SUMMARY_LENGTH = 2000   # chars
MIN_KB_QUERY_LENGTH = 2                # chars

# ─── Failure Simulation ──────────────────────────────────
ENABLE_FAILURE_SIMULATION = os.environ.get("SHOPWAVE_FAILURES", "true").lower() == "true"

# ─── Paths ────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUDIT_LOG_PATH = os.path.join(BASE_DIR, "audit_log.json")
RESOLUTION_REPORT_PATH = os.path.join(BASE_DIR, "resolution_report.json")

# ─── Logging ──────────────────────────────────────────────
LOG_LEVEL = os.environ.get("SHOPWAVE_LOG_LEVEL", "INFO")
