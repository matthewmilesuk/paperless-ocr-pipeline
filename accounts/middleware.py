from django.shortcuts import redirect
from django_otp import user_has_device

# two_factor's own views (login, device setup, QR code, backup tokens,
# disable) all live under this prefix -- see config/urls.py. They must
# stay reachable or a user with no device (or an unverified session)
# could never escape the redirect loop below.
EXEMPT_PATH_PREFIXES = (
    "/account/",
    "/static/",
)


class Enforce2FAMiddleware:
    """
    Confines any authenticated user to the 2FA setup/login flow until
    they have a verified TOTP (or backup-code) device -- a password
    alone is never enough to reach uploads, job status, or /admin/
    (see AGENTS.md "Auth & job visibility").

    Applies to every authenticated request, including staff/admin, per
    explicit product decision -- admin is patched via
    TWO_FACTOR_PATCH_ADMIN and goes through the same enforcement here.

    Anonymous requests are left alone; @login_required on individual
    views (or admin's own login-required check) is what stops those --
    this middleware only tightens things for users who already have a
    session.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and not request.path_info.startswith(EXEMPT_PATH_PREFIXES)
        ):
            if not user_has_device(user):
                return redirect("two_factor:setup")
            if not user.is_verified():
                return redirect("two_factor:login")

        return self.get_response(request)
