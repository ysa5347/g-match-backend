from rest_framework.authentication import SessionAuthentication


class CsrfExemptSessionAuthentication(SessionAuthentication):
    """
    CSRF 검증을 수행하지 않는 SessionAuthentication.

    DRF의 기본 SessionAuthentication은 Django 미들웨어와 별개로
    enforce_csrf()에서 자체 CSRF 검증을 수행한다.
    Dev 환경(CSRF_ENABLED=False)에서 이를 우회하기 위한 클래스.

    Prod 환경에서는 settings.py에서 기본 SessionAuthentication을 사용.
    """

    def enforce_csrf(self, request):
        # CSRF 검증 스킵
        return
