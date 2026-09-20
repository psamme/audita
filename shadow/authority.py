"""Teaching permissions for the local prototype. Roles are explicit, not authentication."""
from shadow import db


def is_senior(con, role):
    if not role:
        return False
    normalize = lambda s: s.strip().lower().replace(" ", "_")
    return normalize(role) in {normalize(u["role"]) for u in db.q(con, "SELECT role FROM user WHERE senior=1")}


def require_senior(con, role):
    if not is_senior(con, role):
        raise PermissionError("An explicit senior role is required to approve a policy change")


SAFETY_FIELDS = ("status", "human_confirmed", "awaiting_senior", "open_question", "valid_from")
