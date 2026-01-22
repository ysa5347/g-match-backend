from django.urls import path
from . import views

app_name = 'account'

urlpatterns = [
    # Account main page
    path('', views.account_main, name='main'),

    # Authentication endpoints
    path('auth/login', views.login_view, name='login'),
    path('auth/logout', views.logout_view, name='logout'),

    # Registration endpoints
    path('auth/registration/email/verification-code', views.send_verification_code_view, name='send_verification_code'),
    path('auth/registration/email/verification-code/verify', views.verify_code_view, name='verify_code'),
    path('auth/registration/agree', views.registration_agree_view, name='registration_agree'),
    path('auth/registration/basic-info', views.registration_basic_info_view, name='registration_basic_info'),

    # User info endpoints
    path('info', views.user_info_view, name='user_info'),

    # OAuth endpoints (TODO)
    # path('oauth/kakao', views.kakao_oauth, name='kakao_oauth'),
    # path('oauth/kakao/callback', views.kakao_callback, name='kakao_callback'),
    # path('oauth/naver', views.naver_oauth, name='naver_oauth'),
    # path('oauth/naver/callback', views.naver_callback, name='naver_callback'),

    # Withdrawal (TODO)
    # path('withdrawal', views.withdrawal, name='withdrawal'),
]
