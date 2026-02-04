"""
인증 미들웨어

django.contrib.auth.login()을 사용하므로 Django의 내장
django.contrib.auth.middleware.AuthenticationMiddleware가
request.user를 자동으로 설정합니다.

settings.py MIDDLEWARE에 'django.contrib.auth.middleware.AuthenticationMiddleware'가
포함되어 있는지 확인하세요.
"""
