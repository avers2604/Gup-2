"""Compatibility facade for IAM application services.

New code should import from ``auth_service`` or ``personnel_service`` directly.
Existing views, commands and tests can keep importing ``apps.iam.services``.
"""
from .auth_service import (
    LOCKOUT_MAX_ATTEMPTS,
    LOCKOUT_WINDOW,
    CredentialCheckResult,
    LoginBlocked,
    TotpEnrollmentNotStarted,
    _client_ip,
    _recent_failed_attempts,
    check_credentials,
    confirm_totp_enrollment,
    is_locked_out,
    make_totp_pending_ticket,
    record_session_login,
    record_session_logout,
    start_totp_enrollment,
    user_auth_summary,
    verify_totp_login,
)
from .personnel_service import (
    HEADER,
    MAX_ROWS,
    REQUIRED_COLUMNS,
    ROLE_IMPORT_MAP,
    STATUS_IMPORT_MAP,
    ImportReport,
    ImportRowResult,
    _cell,
    _format_error,
    _is_role_elevated,
    _parse_bool,
    _resolve_department,
    import_personnel as _import_personnel,
    write_report_csv,
)


def import_personnel(file_obj, *, actor=None):
    """Compatibility wrapper; keeps ``services.MAX_ROWS`` patchable."""
    return _import_personnel(file_obj, actor=actor, max_rows=MAX_ROWS)


__all__ = [
    "LOCKOUT_MAX_ATTEMPTS",
    "LOCKOUT_WINDOW",
    "CredentialCheckResult",
    "LoginBlocked",
    "TotpEnrollmentNotStarted",
    "check_credentials",
    "confirm_totp_enrollment",
    "is_locked_out",
    "make_totp_pending_ticket",
    "record_session_login",
    "record_session_logout",
    "start_totp_enrollment",
    "user_auth_summary",
    "verify_totp_login",
    "HEADER",
    "MAX_ROWS",
    "REQUIRED_COLUMNS",
    "ROLE_IMPORT_MAP",
    "STATUS_IMPORT_MAP",
    "ImportReport",
    "ImportRowResult",
    "import_personnel",
    "write_report_csv",
]
