from functools import wraps
from django.http import JsonResponse


def login_required(view_func):
    """로그인 필수 데코레이터"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('user_id'):
            return JsonResponse({
                'success': False,
                'error': 'Login required',
                'message': '로그인이 필요합니다.'
            }, status=401)
        return view_func(request, *args, **kwargs)
    return wrapper


def identity_check(view_func):
    """신원 확인 데코레이터"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user_id = request.session.get('user_id')
        if not user_id:
            return JsonResponse({
                'success': False,
                'error': 'Identity check failed',
                'message': '신원 확인에 실패했습니다.'
            }, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


def registration_step_required(required_step):
    """회원가입 단계 확인 데코레이터"""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            from .utils.redis_utils import validate_registration_session

            reg_sid = request.COOKIES.get('reg_sid')
            registration_token = request.headers.get('X-Registration-Token')

            if not reg_sid or not registration_token:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid registration session',
                    'message': '유효하지 않은 회원가입 세션입니다.'
                }, status=400)

            is_valid, session_data = validate_registration_session(
                reg_sid,
                required_step=required_step,
                token=registration_token
            )

            if not is_valid:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid registration step',
                    'message': '회원가입 단계가 유효하지 않습니다.'
                }, status=400)

            request.registration_data = session_data
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
