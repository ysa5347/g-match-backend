from django.urls import path
from . import views

app_name = 'account'

urlpatterns = [
    # Account main
    path('', views.account_main, name='main'),

    # Auth main
    path('auth', views.auth_main, name='auth_main'),

    # GIST IdP OIDC endpoints
    path('auth/oidc/login', views.oidc_login_view, name='oidc_login'),
    path('auth/oidc/callback', views.oidc_callback_view, name='oidc_callback'),

    # Auth - Logout
    path('auth/logout', views.logout_view, name='logout'),

    # Auth - Withdraw (회원탈퇴)
    path('auth/withdraw', views.withdraw_view, name='withdraw'),

    # Auth - Recovery (계정 복구)
    path('auth/recovery', views.account_recovery_view, name='account_recovery'),

    # Auth - Registration main
    path('auth/registration', views.registration_main, name='registration_main'),

    # Auth - Registration endpoints (OIDC 기반)
    path('auth/registration/agree', views.registration_agree_view, name='registration_agree'),

    # User info endpoints
    path('info', views.user_info_view, name='user_info'),

    # ============================================
    # [DEPRECATED] 기존 API (하위 호환성)
    # 410 Gone 응답 반환
    # ============================================
    path('auth/login', views.login_view, name='login'),  # Deprecated
    path('auth/registration/email/verification-code', views.send_verification_code_view, name='send_verification_code'),  # Deprecated
    path('auth/registration/email/verification-code/verify', views.verify_code_view, name='verify_code'),  # Deprecated
    path('auth/registration/basic-info', views.registration_basic_info_view, name='registration_basic_info'),  # Deprecated
    path('oauth', views.oauth_main, name='oauth_main'),  # Deprecated
]
