from django.urls import path

from login.views import (
    login_modal,
    check_email,
    verify_code,
    cancel_code,
    login_anonymously,
    logout_modal,
    perform_logout,
)

app_name = "login"

urlpatterns = [
    path("", login_modal, name="login_modal"),
    path("check-email/", check_email, name="check_email"),
    path("verify-code/", verify_code, name="verify_code"),
    path("cancel-code/", cancel_code, name="cancel_code"),
    path("anonymous/", login_anonymously, name="login_anonymously"),
    path("logout/", logout_modal, name="logout_modal"),
    path("logout/confirm/", perform_logout, name="logout"),
]
