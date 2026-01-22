from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .models import CustomUser, Agreement
from .serializers import (
    EmailVerificationSerializer,
    CodeVerificationSerializer,
    AgreementSerializer,
    UserRegistrationSerializer,
    UserLoginSerializer,
    UserInfoSerializer,
    UserUpdateSerializer
)
from .utils.redis_utils import (
    generate_reg_sid,
    generate_registration_token,
    generate_verification_code,
    store_registration_session,
    validate_registration_session,
    store_verification_code,
    validate_verification_code,
    check_email_send_limit,
    increment_email_send_count,
    check_login_attempts,
    increment_login_attempts,
    reset_login_attempts
)
from .utils.email_utils import send_verification_email
from .decorators import login_required, registration_step_required


# ============================================
# 회원가입 API
# ============================================

@swagger_auto_schema(
    method='post',
    operation_summary='이메일 인증코드 발송',
    operation_description='GIST 이메일로 인증코드를 발송합니다. 약관 동의 후 사용 가능합니다.',
    request_body=EmailVerificationSerializer,
    manual_parameters=[
        openapi.Parameter('X-Registration-Token', openapi.IN_HEADER, description='회원가입 토큰', type=openapi.TYPE_STRING, required=True),
    ],
    responses={
        200: openapi.Response('인증코드 발송 성공'),
        400: openapi.Response('잘못된 요청'),
        429: openapi.Response('발송 제한 초과')
    }
)
@api_view(['POST'])
@registration_step_required('agreed')
def send_verification_code_view(request):
    """
    이메일 인증코드 발송
    POST /api/v1alpha1/account/auth/registration/email/verification-code
    """
    serializer = EmailVerificationSerializer(data=request.data)

    if not serializer.is_valid():
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)

    email = serializer.validated_data['email']

    # Rate limiting 확인
    can_send, reason = check_email_send_limit(email)
    if not can_send:
        return Response({
            'success': False,
            'error': 'Rate limit exceeded',
            'message': reason
        }, status=status.HTTP_429_TOO_MANY_REQUESTS)

    # 인증코드 생성 및 저장
    code = generate_verification_code()
    reg_sid = request.COOKIES.get('reg_sid')
    store_verification_code(email, code, reg_sid)

    # 이메일 발송
    if send_verification_email(email, code):
        increment_email_send_count(email)
        return Response({
            'success': True,
            'message': '인증코드가 이메일로 발송되었습니다.',
            'email': email
        }, status=status.HTTP_200_OK)
    else:
        return Response({
            'success': False,
            'error': 'Email send failed',
            'message': '이메일 발송에 실패했습니다.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@swagger_auto_schema(
    method='post',
    operation_summary='이메일 인증코드 검증',
    operation_description='발송된 인증코드를 검증합니다.',
    request_body=CodeVerificationSerializer,
    manual_parameters=[
        openapi.Parameter('X-Registration-Token', openapi.IN_HEADER, description='회원가입 토큰', type=openapi.TYPE_STRING, required=True),
    ],
    responses={
        200: openapi.Response('인증코드 검증 성공'),
        400: openapi.Response('유효하지 않은 인증코드')
    }
)
@api_view(['POST'])
@registration_step_required('agreed')
def verify_code_view(request):
    """
    인증코드 검증
    POST /api/v1alpha1/account/auth/registration/email/verification-code/verify
    """
    serializer = CodeVerificationSerializer(data=request.data)

    if not serializer.is_valid():
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)

    email = serializer.validated_data['email']
    code = serializer.validated_data['code']
    reg_sid = request.COOKIES.get('reg_sid')

    # 인증코드 검증
    if validate_verification_code(email, code, reg_sid):
        # 기존 세션 데이터 가져오기
        registration_token = request.headers.get('X-Registration-Token')
        _, session_data = validate_registration_session(reg_sid, token=registration_token)

        # 새로운 registration_token 생성
        new_token = generate_registration_token()

        # 세션 업데이트
        store_registration_session(
            reg_sid,
            data={
                **session_data['data'],
                'email': email
            },
            step='email_verified',
            token=new_token
        )

        return Response({
            'success': True,
            'message': '이메일 인증이 완료되었습니다.',
            'registration_token': new_token
        }, status=status.HTTP_200_OK)
    else:
        return Response({
            'success': False,
            'error': 'Invalid verification code',
            'message': '유효하지 않은 인증코드입니다.'
        }, status=status.HTTP_400_BAD_REQUEST)


@swagger_auto_schema(
    method='get',
    operation_summary='약관 내용 조회',
    operation_description='서비스 이용약관 및 개인정보 처리방침 내용을 조회합니다.',
    responses={200: openapi.Response('약관 내용 조회 성공')}
)
@swagger_auto_schema(
    method='post',
    operation_summary='약관 동의',
    operation_description='서비스 이용약관 및 개인정보 처리방침에 동의하고 회원가입을 시작합니다.',
    request_body=AgreementSerializer,
    responses={
        200: openapi.Response('약관 동의 성공', openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'success': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                'message': openapi.Schema(type=openapi.TYPE_STRING),
                'registration_token': openapi.Schema(type=openapi.TYPE_STRING)
            }
        )),
        400: openapi.Response('잘못된 요청')
    }
)
@api_view(['GET', 'POST'])
def registration_agree_view(request):
    """
    약관 동의
    GET/POST /api/v1alpha1/account/auth/registration/agree
    """
    if request.method == 'GET':
        # 약관 내용 반환
        return Response({
            'success': True,
            'terms_of_service': {
                'title': '서비스 이용약관',
                'content': '서비스 이용약관 내용...'
            },
            'privacy_policy': {
                'title': '개인정보 처리방침',
                'content': '개인정보 처리방침 내용...'
            }
        }, status=status.HTTP_200_OK)

    elif request.method == 'POST':
        serializer = AgreementSerializer(data=request.data)

        if not serializer.is_valid():
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        # reg_sid 및 registration_token 생성
        reg_sid = generate_reg_sid()
        registration_token = generate_registration_token()

        # 세션 저장
        store_registration_session(
            reg_sid,
            data={
                'agreements': serializer.validated_data
            },
            step='agreed',
            token=registration_token
        )

        response = Response({
            'success': True,
            'message': '약관 동의가 완료되었습니다.',
            'registration_token': registration_token
        }, status=status.HTTP_200_OK)

        # reg_sid 쿠키 설정
        response.set_cookie(
            key='reg_sid',
            value=reg_sid,
            max_age=1800,  # 30분
            httponly=True,
            samesite='Lax'
        )

        return response


@swagger_auto_schema(
    method='post',
    operation_summary='기본정보 등록 및 회원가입 완료',
    operation_description='사용자 기본정보를 등록하고 회원가입을 완료합니다.',
    request_body=UserRegistrationSerializer,
    manual_parameters=[
        openapi.Parameter('X-Registration-Token', openapi.IN_HEADER, description='회원가입 토큰', type=openapi.TYPE_STRING, required=True),
    ],
    responses={
        201: openapi.Response('회원가입 성공'),
        400: openapi.Response('잘못된 요청')
    }
)
@api_view(['POST'])
@registration_step_required('email_verified')
def registration_basic_info_view(request):
    """
    기본정보 등록 및 회원가입 완료
    POST /api/v1alpha1/account/auth/registration/basic-info
    """
    # 기존 세션 데이터 가져오기
    reg_sid = request.COOKIES.get('reg_sid')
    registration_token = request.headers.get('X-Registration-Token')
    _, session_data = validate_registration_session(reg_sid, token=registration_token)

    email = session_data['data']['email']
    agreements = session_data['data']['agreements']

    # 회원가입 데이터 병합
    registration_data = {
        **request.data,
        'email': email
    }

    serializer = UserRegistrationSerializer(data=registration_data)

    if not serializer.is_valid():
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)

    # 사용자 생성
    user = serializer.save()

    # 약관 동의 저장
    Agreement.objects.create(
        user=user,
        **agreements
    )

    # 세션 삭제 (회원가입 완료)
    from django.core.cache import cache
    cache.delete(f"registration:{reg_sid}")

    response = Response({
        'success': True,
        'message': '회원가입이 완료되었습니다.',
        'user': {
            'uid': str(user.uid),
            'email': user.email,
            'name': user.name
        }
    }, status=status.HTTP_201_CREATED)

    # reg_sid 쿠키 삭제
    response.delete_cookie('reg_sid')

    return response


# ============================================
# 로그인/로그아웃 API
# ============================================

@swagger_auto_schema(
    method='get',
    operation_summary='로그인 페이지',
    operation_description='로그인 페이지 정보를 반환합니다.',
    responses={200: openapi.Response('로그인 페이지')}
)
@swagger_auto_schema(
    method='post',
    operation_summary='로그인',
    operation_description='이메일과 비밀번호로 로그인합니다.',
    request_body=UserLoginSerializer,
    responses={
        200: openapi.Response('로그인 성공'),
        401: openapi.Response('인증 실패'),
        429: openapi.Response('로그인 시도 횟수 초과')
    }
)
@api_view(['GET', 'POST'])
def login_view(request):
    """
    로그인
    GET/POST /api/v1alpha1/account/auth/login
    """
    if request.method == 'GET':
        # 로그인 페이지 정보 반환
        return Response({
            'success': True,
            'message': 'Login page'
        }, status=status.HTTP_200_OK)

    elif request.method == 'POST':
        serializer = UserLoginSerializer(data=request.data)

        if not serializer.is_valid():
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data['email']
        password = serializer.validated_data['password']

        # 로그인 시도 횟수 확인
        is_locked, attempts = check_login_attempts(email)
        if is_locked:
            return Response({
                'success': False,
                'error': 'Account locked',
                'message': f'로그인 시도 횟수를 초과했습니다. 30분 후 다시 시도해주세요.'
            }, status=status.HTTP_429_TOO_MANY_REQUESTS)

        # 사용자 인증
        try:
            user = CustomUser.objects.get(email=email)
            if user.check_password(password):
                # 로그인 성공
                reset_login_attempts(email)

                # 세션 생성
                request.session['user_id'] = str(user.uid)
                request.session.cycle_key()  # 세션 고정 공격 방지

                return Response({
                    'success': True,
                    'message': '로그인에 성공했습니다.',
                    'user': {
                        'uid': str(user.uid),
                        'email': user.email,
                        'name': user.name
                    }
                }, status=status.HTTP_200_OK)
            else:
                # 비밀번호 불일치
                increment_login_attempts(email)
                return Response({
                    'success': False,
                    'error': 'Invalid credentials',
                    'message': '이메일 또는 비밀번호가 올바르지 않습니다.',
                    'attempts_left': 5 - (attempts + 1)
                }, status=status.HTTP_401_UNAUTHORIZED)

        except CustomUser.DoesNotExist:
            # 사용자 없음
            increment_login_attempts(email)
            return Response({
                'success': False,
                'error': 'Invalid credentials',
                'message': '이메일 또는 비밀번호가 올바르지 않습니다.'
            }, status=status.HTTP_401_UNAUTHORIZED)


@swagger_auto_schema(
    method='post',
    operation_summary='로그아웃',
    operation_description='현재 세션을 종료하고 로그아웃합니다.',
    responses={
        200: openapi.Response('로그아웃 성공'),
        401: openapi.Response('로그인 필요')
    }
)
@api_view(['POST'])
@login_required
def logout_view(request):
    """
    로그아웃
    POST /api/v1alpha1/account/auth/logout
    """
    request.session.flush()

    return Response({
        'success': True,
        'message': '로그아웃되었습니다.'
    }, status=status.HTTP_200_OK)


# ============================================
# 사용자 정보 API
# ============================================

@swagger_auto_schema(
    method='get',
    operation_summary='사용자 정보 조회',
    operation_description='현재 로그인한 사용자의 정보를 조회합니다.',
    responses={
        200: openapi.Response('사용자 정보 조회 성공', UserInfoSerializer),
        401: openapi.Response('로그인 필요')
    }
)
@swagger_auto_schema(
    method='post',
    operation_summary='사용자 정보 수정',
    operation_description='현재 로그인한 사용자의 정보를 수정합니다.',
    request_body=UserUpdateSerializer,
    responses={
        200: openapi.Response('사용자 정보 수정 성공'),
        400: openapi.Response('잘못된 요청'),
        401: openapi.Response('로그인 필요')
    }
)
@swagger_auto_schema(
    method='put',
    operation_summary='사용자 정보 수정',
    operation_description='현재 로그인한 사용자의 정보를 수정합니다.',
    request_body=UserUpdateSerializer,
    responses={
        200: openapi.Response('사용자 정보 수정 성공'),
        400: openapi.Response('잘못된 요청'),
        401: openapi.Response('로그인 필요')
    }
)
@api_view(['GET', 'POST', 'PUT'])
@login_required
def user_info_view(request):
    """
    사용자 정보 조회/수정
    GET/POST/PUT /api/v1alpha1/account/info
    """
    user_id = request.session.get('user_id')
    user = CustomUser.objects.get(uid=user_id)

    if request.method == 'GET':
        serializer = UserInfoSerializer(user)
        return Response({
            'success': True,
            'user': serializer.data
        }, status=status.HTTP_200_OK)

    elif request.method in ['POST', 'PUT']:
        serializer = UserUpdateSerializer(user, data=request.data, partial=True)

        if not serializer.is_valid():
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()

        return Response({
            'success': True,
            'message': '사용자 정보가 수정되었습니다.',
            'user': UserInfoSerializer(user).data
        }, status=status.HTTP_200_OK)


# ============================================
# Entry Points
# ============================================

@swagger_auto_schema(
    method='get',
    operation_summary='Account 서비스 메인',
    operation_description='Account 서비스의 엔드포인트 목록을 반환합니다.',
    responses={200: openapi.Response('엔드포인트 목록')}
)
@api_view(['GET'])
def account_main(request):
    """
    Account 서비스 메인
    GET /api/v1alpha1/account/
    """
    return Response({
        'success': True,
        'service': 'Account',
        'version': 'v1alpha1',
        'endpoints': {
            'auth': '/api/v1alpha1/account/auth',
            'oauth': '/api/v1alpha1/account/oauth',
            'user_info': '/api/v1alpha1/account/info'
        }
    }, status=status.HTTP_200_OK)


@swagger_auto_schema(
    method='get',
    operation_summary='Auth 서비스 메인',
    operation_description='Auth 서비스의 엔드포인트 목록을 반환합니다.',
    responses={200: openapi.Response('엔드포인트 목록')}
)
@api_view(['GET'])
def auth_main(request):
    """
    Auth 서비스 메인
    GET /api/v1alpha1/account/auth
    """
    return Response({
        'success': True,
        'service': 'Auth',
        'version': 'v1alpha1',
        'endpoints': {
            'login': '/api/v1alpha1/account/auth/login',
            'logout': '/api/v1alpha1/account/auth/logout',
            'registration': '/api/v1alpha1/account/auth/registration'
        }
    }, status=status.HTTP_200_OK)


@swagger_auto_schema(
    method='get',
    operation_summary='OAuth 서비스 메인',
    operation_description='OAuth 서비스의 엔드포인트 목록을 반환합니다.',
    responses={200: openapi.Response('엔드포인트 목록')}
)
@api_view(['GET'])
def oauth_main(request):
    """
    OAuth 서비스 메인
    GET /api/v1alpha1/account/oauth
    """
    return Response({
        'success': True,
        'service': 'OAuth',
        'version': 'v1alpha1',
        'endpoints': {
            'kakao': '/api/v1alpha1/account/oauth/kakao',
            'naver': '/api/v1alpha1/account/oauth/naver'
        }
    }, status=status.HTTP_200_OK)


@swagger_auto_schema(
    method='get',
    operation_summary='Registration 서비스 메인',
    operation_description='Registration 서비스의 엔드포인트 목록 및 플로우를 반환합니다.',
    responses={200: openapi.Response('엔드포인트 목록 및 플로우')}
)
@api_view(['GET'])
def registration_main(request):
    """
    Registration 서비스 메인
    GET /api/v1alpha1/account/auth/registration
    """
    return Response({
        'success': True,
        'service': 'Registration',
        'version': 'v1alpha1',
        'flow': [
            '1. agree',
            '2. send_verification_code',
            '3. verify_code',
            '4. basic_info'
        ],
        'endpoints': {
            'agree': '/api/v1alpha1/account/auth/registration/agree',
            'send_verification_code': '/api/v1alpha1/account/auth/registration/email/verification-code',
            'verify_code': '/api/v1alpha1/account/auth/registration/email/verification-code/verify',
            'basic_info': '/api/v1alpha1/account/auth/registration/basic-info'
        }
    }, status=status.HTTP_200_OK)
