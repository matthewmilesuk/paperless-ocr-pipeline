from django.shortcuts import redirect

from .models import is_configured

# Mirrors accounts.middleware.Enforce2FAMiddleware's exemption-prefix
# pattern. /account/ covers the 2FA flow (which must run first -- see
# MIDDLEWARE ordering in settings.py, this middleware is listed after
# Enforce2FAMiddleware), /gcp-setup/ is the wizard itself (else an
# unconfigured staff user could never reach the page that fixes this).
EXEMPT_PATH_PREFIXES = (
    "/account/",
    "/static/",
    "/gcp-setup/",
    "/accounts/logout/",
)


class RequireGcpConfigMiddleware:
    """
    Redirects staff users to the setup wizard if GCP/Document AI isn't
    configured yet -- via gcpconfig.models.Configuration (the wizard) or
    settings.py/.env (the original manual path); either counts as
    configured (see is_configured()).

    Deliberately narrower than Enforce2FAMiddleware's blanket enforcement
    for every authenticated user: GCP configuration is an operational
    readiness concern, not a security gate, and only staff can actually
    do anything about it -- redirecting a non-staff user here would just
    be confusing, since they have no way to reach or use the wizard.
    Non-staff users get a different, narrower check instead, at the
    point it actually matters: ingest.views.upload() (see AGENTS.md
    "Auth & job visibility" for the equivalent reasoning applied to job
    visibility).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and user.is_staff
            and not request.path_info.startswith(EXEMPT_PATH_PREFIXES)
        ):
            if not is_configured():
                return redirect("gcpconfig_wizard")

        return self.get_response(request)
