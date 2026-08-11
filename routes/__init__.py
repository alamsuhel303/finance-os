"""Blueprint registration."""

from routes.accounts import accounts_bp
from routes.budget import budget_bp
from routes.checklist import checklist_bp
from routes.dashboard import dashboard_bp
from routes.envelopes import envelopes_bp
from routes.goals import goals_bp
from routes.insurance import insurance_bp
from routes.investments import investments_bp
from routes.networth import networth_bp
from routes.reports import reports_bp
from routes.settings import settings_bp
from routes.transactions import transactions_bp

__all__ = [
    "dashboard_bp",
    "checklist_bp",
    "transactions_bp",
    "accounts_bp",
    "envelopes_bp",
    "budget_bp",
    "reports_bp",
    "settings_bp",
    "investments_bp",
    "goals_bp",
    "networth_bp",
    "insurance_bp",
]
