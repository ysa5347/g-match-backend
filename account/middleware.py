from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from .models import CustomUser


class AuthenticationMiddleware(MiddlewareMixin):
    """인증 상태 확인 미들웨어"""

    def process_request(self, request):
        """요청 처리 전 인증 상태 확인"""
        # 세션에서 사용자 ID 가져오기
        user_id = request.session.get('user_id')

        if user_id:
            try:
                request.user = CustomUser.objects.get(uid=user_id)
                request.is_authenticated = True
            except CustomUser.DoesNotExist:
                request.user = None
                request.is_authenticated = False
        else:
            request.user = None
            request.is_authenticated = False

        return None
