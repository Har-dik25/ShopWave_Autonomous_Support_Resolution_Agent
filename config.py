"""
NexusDesk Agent — Configuration (Production Readiness)
======================================================
All tunable parameters centralized. No magic numbers in business logic.
"""

import os

# ─── Agent Settings ────────────────────────────────────────
AGENT_NAME = "NexusDesk Support Agent v4"
AGENT_VERSION = "4.0.0"
MAX_WORKERS = int(os.environ.get("NEXUSDESK_MAX_WORKERS", "8"))

# ─── Tool Retry Settings ──────────────────────────────────
TOOL_MAX_RETRIES = int(os.environ.get("NEXUSDESK_TOOL_RETRIES", "3"))
TOOL_BASE_DELAY_S = float(os.environ.get("NEXUSDESK_TOOL_DELAY", "0.05"))

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
API_KEY = os.environ.get("NEXUSDESK_API_KEY", "")  # empty = open access (dev mode)
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("NEXUSDESK_RATE_LIMIT", "10"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("NEXUSDESK_RATE_WINDOW", "60"))
CORS_ALLOWED_ORIGINS = os.environ.get("NEXUSDESK_CORS_ORIGINS", "http://localhost:8080,http://localhost:8000").split(",")
ENABLE_PII_MASKING = os.environ.get("NEXUSDESK_PII_MASKING", "true").lower() == "true"

# ─── Failure Simulation ──────────────────────────────────
ENABLE_FAILURE_SIMULATION = os.environ.get("NEXUSDESK_FAILURES", "true").lower() == "true"
ENABLE_RANDOM_FAILURES = os.environ.get("NEXUSDESK_RANDOM_FAILURES", "true").lower() == "true"
RANDOM_FAILURE_PROBABILITY = float(os.environ.get("NEXUSDESK_RANDOM_FAILURE_PROB", "0.05"))

# ─── Scalability ──────────────────────────────────────────
MAX_CONCURRENT_SWEEPS = int(os.environ.get("NEXUSDESK_MAX_SWEEPS", "3"))
REQUEST_QUEUE_SIZE = int(os.environ.get("NEXUSDESK_QUEUE_SIZE", "10"))
SSE_MAX_CLIENTS = int(os.environ.get("NEXUSDESK_SSE_MAX_CLIENTS", "50"))

# ─── NLP Settings ─────────────────────────────────────────
FUZZY_MATCH_THRESHOLD = int(os.environ.get("NEXUSDESK_FUZZY_THRESHOLD", "2"))  # max edit distance
SENTIMENT_WEIGHT = float(os.environ.get("NEXUSDESK_SENTIMENT_WEIGHT", "0.15"))  # urgency influence

# ─── Persistence ──────────────────────────────────────────
ENABLE_PERSISTENCE = os.environ.get("NEXUSDESK_PERSISTENCE", "true").lower() == "true"

# ─── Paths ────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUDIT_LOG_PATH = os.path.join(BASE_DIR, "audit_log.json")
RESOLUTION_REPORT_PATH = os.path.join(BASE_DIR, "resolution_report.json")
STATE_FILE_PATH = os.path.join(DATA_DIR, "state.json")

# ─── Logging ──────────────────────────────────────────────
LOG_LEVEL = os.environ.get("NEXUSDESK_LOG_LEVEL", "INFO")
