"""Shared runtime state for extracted modules. Populated by bot.py at startup."""

bot = None
DATASTORE = None
CONFIG = {}
logger = None
RECONCILE_LOCK = None
MONTHLY_AUDIT_PENDING = False
RITES_LOCK = None
MACHINE_SPIRITS_LOCK = None
ROTATION_LOCK = None
ACTIVITY_STATUS_LOCK = None
PROMOTION_TRACKING_LOCK = None
INDUCTION_OVERRIDES_LOCK = None
CHALLENGE_PROGRESS_LOCK = None
LFG_QUEUE_LOCK = None
LFG_ACTIVE_QUEUES = {}
SHUTDOWN_INITIATED = False
LAST_MILESTONE_CHECK_DATE = None
DEBUG_MODE = False
# Terminus Kill Log subsystem
TERMINUS_SLAYER_LOCK = None
# Auto-roster embed subsystem
ROSTER_STATE_LOCK = None
