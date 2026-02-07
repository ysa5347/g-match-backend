from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.shortcuts import redirect
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .models import CustomUser, Agreement
from .serializers import (
    AgreementSerializer,
    BasicInfoSerializer,
    UserInfoSerializer,
    UserUpdateSerializer,
    OIDCCallbackSerializer,
    OIDCUserInfoSerializer,
)
from .utils.redis_utils import (
    generate_reg_sid,
    generate_registration_token,
    store_registration_session,
    validate_registration_session,
)
from .utils.oidc_utils import (
    build_authorization_url,
    process_oidc_callback,
    OIDCError,
    OIDCValidationError,
    OIDCTokenError,
)
from .decorators import login_required, registration_step_required


# ============================================
# GIST IdP OIDC 인증 API
# ============================================

@swagger_auto_schema(
    method='get',
    operation_summary='GIST IdP 로그인/회원가입 시작',
    operation_description='''
    GIST IdP OIDC 인증을 시작합니다.
    이 엔드포인트는 GIST IdP 로그인 페이지로 리다이렉트합니다.

    회원가입과 로그인 모두 이 엔드포인트를 통해 처리됩니다:
    - 기존 사용자: 로그인 처리
    - 신규 사용자: 회원가입 플로우 시작 (약관 동의 필요)
    ''',
    manual_parameters=[
        openapi.Parameter(
            'redirect_after',
            openapi.IN_QUERY,
            description='인증 완료 후 리다이렉트할 클라이언트 URL',
            type=openapi.TYPE_STRING,
            required=False
        ),
    ],
    responses={
        302: openapi.Response('GIST IdP 로그인 페이지로 리다이렉트'),
        200: openapi.Response('Authorization URL 반환 (AJAX 요청 시)')
    }
)
@api_view(['GET'])
def oidc_login_view(request):
    """
    GIST IdP OIDC 인증 시작
    GET /api/v1alpha1/account/auth/oidc/login
    """
    redirect_after = request.GET.get('redirect_after')

    # Authorization URL 생성
    auth_data = build_authorization_url(redirect_after)

    # AJAX 요청인 경우 JSON 응답
    if request.headers.get('Accept') == 'application/json':
        return Response({
            'success': True,
            'authorization_url': auth_data['authorization_url']
        }, status=status.HTTP_200_OK)

    # 일반 요청인 경우 리다이렉트
    return redirect(auth_data['authorization_url'])


@swagger_auto_schema(
    method='get',
    operation_summary='GIST IdP OIDC Callback',
    operation_description='''
    GIST IdP에서 인증 후 리다이렉트되는 콜백 엔드포인트입니다.

    처리 흐름:
    1. Authorization code와 state 검증
    2. Token 교환 (code → tokens)
    3. ID Token 검증 및 사용자 정보 추출
    4. 기존 사용자: 로그인 처리
    5. 신규 사용자: 회원가입 플로우 시작 (약관 동의 페이지로 이동)
    ''',
    manual_parameters=[
        openapi.Parameter('code', openapi.IN_QUERY, description='Authorization code', type=openapi.TYPE_STRING),
        openapi.Parameter('state', openapi.IN_QUERY, description='State parameter', type=openapi.TYPE_STRING),
        openapi.Parameter('error', openapi.IN_QUERY, description='Error code (if any)', type=openapi.TYPE_STRING),
        openapi.Parameter('error_description', openapi.IN_QUERY, description='Error description', type=openapi.TYPE_STRING),
    ],
    responses={
        200: openapi.Response('인증 성공'),
        400: openapi.Response('잘못된 요청'),
        401: openapi.Response('인증 실패')
    }
)
@api_view(['GET'])
def oidc_callback_view(request):
    """
    GIST IdP OIDC Callback 처리
    GET /api/v1alpha1/account/auth/oidc/callback
    처리 완료 후 F/E Auth Callback 페이지로 리다이렉트합니다.
    """
    from django.conf import settings as django_settings
    from django.contrib.auth import login as auth_login
    from urllib.parse import urlencode

    frontend_callback_url = django_settings.FRONTEND_AUTH_CALLBACK_URL

    def redirect_to_frontend(params):
        """F/E 콜백 URL로 리다이렉트"""
        url = f"{frontend_callback_url}?{urlencode(params)}"
        return redirect(url)

    # 파라미터 검증
    serializer = OIDCCallbackSerializer(data=request.GET)
    if not serializer.is_valid():
        return redirect_to_frontend({
            'error': 'Invalid callback parameters',
        })

    code = serializer.validated_data['code']
    state = serializer.validated_data['state']

    try:
        # OIDC 콜백 처리
        result = process_oidc_callback(code, state)
        user_info = result['user_info']
        redirect_after = result.get('redirect_after')

        # 사용자 정보 검증
        user_info_serializer = OIDCUserInfoSerializer(data=user_info)
        if not user_info_serializer.is_valid():
            return redirect_to_frontend({
                'error': 'Invalid user info from IdP',
            })

        # 기존 사용자 확인
        gist_id = user_info['sub']
        email = user_info['email']

        # gist_id 또는 email로 기존 사용자 조회
        user = CustomUser.objects.filter(gist_id=gist_id).first()
        if not user:
            user = CustomUser.objects.filter(email=email).first()

        if user:
            # 비활성화된 사용자인지 확인 (회원탈퇴 후 30일 이내)
            if not user.is_active:
                # 30일 초과 확인
                if user.deactivated_at:
                    days_since = (timezone.now() - user.deactivated_at).days
                    if days_since > 30:
                        # 복구 불가 - 새로 가입 필요
                        return redirect_to_frontend({
                            'error': 'account_expired',
                            'message': '복구 기간이 만료되었습니다. 새로 가입해주세요.',
                        })

                # 복구 가능 - 복구 페이지로 리다이렉트
                # 서명된 복구 토큰 생성 (세션 대신 토큰 사용)
                from django.core.signing import TimestampSigner
                signer = TimestampSigner()
                recovery_token = signer.sign(str(user.user_id))

                params = {
                    'needs_recovery': 'true',
                    'user_email': user.email,
                    'recovery_token': recovery_token,
                }
                if redirect_after:
                    params['redirect_after'] = redirect_after

                return redirect_to_frontend(params)

            # 기존 사용자: 로그인 처리
            # gist_id 연결 (기존 이메일 사용자인 경우)
            if not user.gist_id:
                user.gist_id = gist_id

            # IdP 정보로 업데이트
            user.name = user_info.get('name') or user.name
            user.student_id = user_info.get('student_id') or user.student_id
            user.phone_number = user_info.get('phone_number') or user.phone_number
            user.save()

            # Django auth 로그인 (SESSION_KEY에 user pk 저장)
            auth_login(request, user)

            # F/E로 리다이렉트 (기존 사용자)
            params = {'is_new_user': 'false'}
            if redirect_after:
                params['redirect_after'] = redirect_after

            return redirect_to_frontend(params)

        # 신규 사용자: 회원가입 플로우 시작
        reg_sid = generate_reg_sid()
        registration_token = generate_registration_token()

        # OIDC 사용자 정보를 세션에 저장
        store_registration_session(
            reg_sid,
            data={
                'oidc_user_info': user_info
            },
            step='oidc_authenticated',
            token=registration_token
        )

        # F/E로 리다이렉트 (신규 사용자)
        params = {
            'is_new_user': 'true',
            'registration_token': registration_token,
        }
        if redirect_after:
            params['redirect_after'] = redirect_after

        response = redirect_to_frontend(params)

        # reg_sid 쿠키 설정
        response.set_cookie(
            key='reg_sid',
            value=reg_sid,
            max_age=1800,  # 30분
            httponly=True,
            samesite='Lax'
        )

        return response

    except OIDCValidationError as e:
        return redirect_to_frontend({
            'error': f'OIDC validation failed: {str(e)}',
        })

    except OIDCTokenError as e:
        return redirect_to_frontend({
            'error': f'Token exchange failed: {str(e)}',
        })

    except OIDCError as e:
        return redirect_to_frontend({
            'error': f'OIDC error: {str(e)}',
        })


# ============================================
# 회원가입 API (OIDC 기반)
# ============================================

@swagger_auto_schema(
    method='get',
    operation_summary='약관 내용 조회',
    operation_description='서비스 이용약관 및 개인정보 처리방침 내용을 조회합니다.',
    responses={200: openapi.Response('약관 내용 조회 성공')}
)
@swagger_auto_schema(
    method='post',
    operation_summary='약관 동의 (회원가입 Step 1)',
    operation_description='''
    서비스 이용약관 및 개인정보 처리방침에 동의합니다.

    GIST IdP OIDC 인증 후 호출해야 합니다.
    약관 동의 후 기본정보 입력 단계(/registration/basic-info)로 진행합니다.
    ''',
    request_body=AgreementSerializer,
    manual_parameters=[
        openapi.Parameter('X-Registration-Token', openapi.IN_HEADER, description='회원가입 토큰', type=openapi.TYPE_STRING, required=True),
    ],
    responses={
        200: openapi.Response('약관 동의 성공, 다음 단계로 이동'),
        400: openapi.Response('잘못된 요청'),
        401: openapi.Response('인증 필요')
    }
)
@api_view(['GET', 'POST'])
def registration_agree_view(request):
    """
    약관 동의 (회원가입 Step 1)
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
        # OIDC 인증 세션 검증
        reg_sid = request.COOKIES.get('reg_sid')
        registration_token = request.headers.get('X-Registration-Token')

        if not reg_sid or not registration_token:
            return Response({
                'success': False,
                'error': 'Authentication required',
                'message': 'GIST IdP 인증이 필요합니다.',
                'login_url': '/api/v1alpha1/account/auth/oidc/login'
            }, status=status.HTTP_401_UNAUTHORIZED)

        is_valid, session_data = validate_registration_session(
            reg_sid,
            required_step='oidc_authenticated',
            token=registration_token
        )

        if not is_valid:
            return Response({
                'success': False,
                'error': 'Invalid or expired session',
                'message': '세션이 만료되었습니다. GIST IdP 인증을 다시 진행해주세요.',
                'login_url': '/api/v1alpha1/account/auth/oidc/login'
            }, status=status.HTTP_401_UNAUTHORIZED)

        # 약관 동의 검증
        agreement_serializer = AgreementSerializer(data=request.data)
        if not agreement_serializer.is_valid():
            return Response({
                'success': False,
                'errors': agreement_serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        # 새 토큰 생성
        new_registration_token = generate_registration_token()

        # OIDC 사용자 정보 가져오기
        oidc_user_info = session_data['data']['oidc_user_info']

        # 세션 업데이트: 약관 동의 정보 저장, step='agreed'로 변경
        store_registration_session(
            reg_sid,
            data={
                'oidc_user_info': oidc_user_info,
                'agreement': agreement_serializer.validated_data
            },
            step='agreed',
            token=new_registration_token
        )

        return Response({
            'success': True,
            'message': '약관 동의가 완료되었습니다.',
            'registration_token': new_registration_token,
            'next_step': '/api/v1alpha1/account/auth/registration/basic-info'
        }, status=status.HTTP_200_OK)


# ============================================
# 로그아웃 API
# ============================================

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
    from django.contrib.auth import logout as auth_logout
    auth_logout(request)

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
    operation_description='''
    현재 로그인한 사용자의 정보를 수정합니다.

    Note: email, name, student_id, phone_number는 GIST IdP에서 관리하므로
    수정할 수 없습니다. 해당 정보 변경은 GIST IdP에서 진행해주세요.
    ''',
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
    operation_description='''
    현재 로그인한 사용자의 정보를 수정합니다.

    Note: email, name, student_id, phone_number는 GIST IdP에서 관리하므로
    수정할 수 없습니다. 해당 정보 변경은 GIST IdP에서 진행해주세요.
    ''',
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
    user = request.user

    if request.method == 'GET':
        serializer = UserInfoSerializer(user)
        return Response({
            'success': True,
            'user': serializer.data
        }, status=status.HTTP_200_OK)

    elif request.method in ['POST', 'PUT']:
        # 닉네임 변경 시 match_status 확인
        if 'nickname' in request.data:
            from match.models import Property
            active = Property.objects.filter(
                user_id=user.user_id
            ).order_by('-created_at').first()

            if active and active.match_status != Property.MatchStatusChoice.NOT_STARTED:
                return Response({
                    'success': False,
                    'error': '매칭 진행 중에는 닉네임을 변경할 수 없습니다.'
                }, status=status.HTTP_400_BAD_REQUEST)

        serializer = UserUpdateSerializer(user, data=request.data, partial=True)

        if not serializer.is_valid():
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()

        # 닉네임 변경 시 Property에도 전파
        if 'nickname' in request.data:
            from match.models import Property
            Property.objects.filter(
                user_id=user.user_id
            ).update(nickname=user.nickname)

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
        'description': 'GIST IdP OIDC 기반 인증',
        'endpoints': {
            'login': '/api/v1alpha1/account/auth/oidc/login',
            'callback': '/api/v1alpha1/account/auth/oidc/callback',
            'logout': '/api/v1alpha1/account/auth/logout',
            'registration': '/api/v1alpha1/account/auth/registration'
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
        'description': 'GIST IdP OIDC 기반 회원가입',
        'flow': [
            '1. GIST IdP 로그인 (/auth/oidc/login)',
            '2. OIDC Callback 처리 (/auth/oidc/callback) → step: oidc_authenticated',
            '3. 약관 동의 (/auth/registration/agree) → step: agreed',
            '4. 기본정보 입력 및 회원가입 완료 (/auth/registration/basic-info)'
        ],
        'endpoints': {
            'oidc_login': '/api/v1alpha1/account/auth/oidc/login',
            'oidc_callback': '/api/v1alpha1/account/auth/oidc/callback',
            'agree': '/api/v1alpha1/account/auth/registration/agree',
            'basic_info': '/api/v1alpha1/account/auth/registration/basic-info'
        },
        'session_steps': {
            'oidc_authenticated': 'OIDC 인증 완료',
            'agreed': '약관 동의 완료'
        },
        'note': '사용자 정보(이메일, 이름, 학번, 전화번호)는 GIST IdP에서 제공받습니다. 성별과 닉네임은 필수 입력 항목입니다.'
    }, status=status.HTTP_200_OK)


# ============================================
# 회원탈퇴 API
# ============================================

@swagger_auto_schema(
    method='get',
    operation_summary='회원탈퇴 정보 조회',
    operation_description='회원탈퇴 시 삭제되는 정보와 주의사항을 조회합니다.',
    responses={
        200: openapi.Response('회원탈퇴 정보'),
        401: openapi.Response('로그인 필요')
    }
)
@swagger_auto_schema(
    method='post',
    operation_summary='회원탈퇴 요청',
    operation_description='''
    현재 로그인한 사용자의 계정을 비활성화합니다.

    - 계정은 즉시 비활성화되며 로그인이 불가능해집니다.
    - 30일 이내에 다시 로그인하면 계정을 복구할 수 있습니다.
    - 30일이 지나면 모든 데이터가 영구 삭제됩니다.
    ''',
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            'confirmation': openapi.Schema(
                type=openapi.TYPE_STRING,
                description='탈퇴 확인 문구 ("회원탈퇴"를 입력)'
            )
        },
        required=['confirmation']
    ),
    responses={
        200: openapi.Response('회원탈퇴 성공'),
        400: openapi.Response('잘못된 요청'),
        401: openapi.Response('로그인 필요')
    }
)
@api_view(['GET', 'POST', 'OPTIONS'])
@login_required
def withdraw_view(request):
    """
    회원탈퇴
    GET/POST /api/v1alpha1/account/auth/withdraw
    """
    user = request.user

    if request.method == 'GET':
        return Response({
            'success': True,
            'message': '회원탈퇴 안내',
            'warning': '회원탈퇴 시 다음 데이터가 삭제됩니다.',
            'deleted_data': [
                '계정 정보 (이메일, 이름, 학번 등)',
                '매칭 프로필 정보',
                '설문 응답 데이터',
                '매칭 이력'
            ],
            'retention_period': '30일',
            'recovery_info': '탈퇴 후 30일 이내에 다시 로그인하면 계정을 복구할 수 있습니다.',
            'confirmation_required': '회원탈퇴',
        }, status=status.HTTP_200_OK)

    elif request.method == 'POST':
        confirmation = request.data.get('confirmation', '')

        if confirmation != '회원탈퇴':
            return Response({
                'success': False,
                'error': 'invalid_confirmation',
                'message': '탈퇴 확인 문구가 올바르지 않습니다. "회원탈퇴"를 정확히 입력해주세요.'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Soft delete: is_active = False, deactivated_at 설정
        user.is_active = False
        user.deactivated_at = timezone.now()
        user.save()

        # 세션 종료
        from django.contrib.auth import logout as auth_logout
        auth_logout(request)

        return Response({
            'success': True,
            'message': '회원탈퇴가 완료되었습니다.',
            'recovery_info': '30일 이내에 다시 로그인하면 계정을 복구할 수 있습니다.'
        }, status=status.HTTP_200_OK)


# ============================================
# 계정 복구 API
# ============================================

@swagger_auto_schema(
    method='get',
    operation_summary='계정 복구 가능 여부 확인',
    operation_description='비활성화된 계정의 복구 가능 여부를 확인합니다.',
    responses={
        200: openapi.Response('복구 정보'),
    }
)
@swagger_auto_schema(
    method='post',
    operation_summary='계정 복구 요청',
    operation_description='''
    비활성화된 계정을 복구합니다.

    OIDC 콜백에서 비활성화된 사용자가 감지되면 이 API로 리다이렉트됩니다.
    복구에 동의하면 계정이 다시 활성화됩니다.
    ''',
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            'confirm_recovery': openapi.Schema(
                type=openapi.TYPE_BOOLEAN,
                description='복구 동의 여부'
            )
        },
        required=['confirm_recovery']
    ),
    responses={
        200: openapi.Response('복구 성공'),
        400: openapi.Response('잘못된 요청'),
        401: openapi.Response('인증 필요')
    }
)
@api_view(['GET', 'POST', 'OPTIONS'])
def account_recovery_view(request):
    """
    계정 복구
    GET/POST /api/v1alpha1/account/auth/recovery

    인증 방식:
    1. X-Recovery-Token 헤더 (서명된 토큰) - 권장
    2. recovery_token 쿼리 파라미터
    3. 세션 기반 (fallback)
    """
    from datetime import timedelta
    from django.core.signing import TimestampSigner, BadSignature, SignatureExpired

    recovery_user_id = None
    token_source = None

    # 1. 헤더에서 토큰 확인
    recovery_token = request.headers.get('X-Recovery-Token')
    if not recovery_token:
        # 2. 쿼리 파라미터에서 토큰 확인
        recovery_token = request.query_params.get('recovery_token')
    if not recovery_token:
        # 3. POST body에서 토큰 확인
        recovery_token = request.data.get('recovery_token')

    if recovery_token:
        try:
            signer = TimestampSigner()
            # 토큰 유효기간: 30분
            recovery_user_id = signer.unsign(recovery_token, max_age=1800)
            token_source = 'token'
        except SignatureExpired:
            return Response({
                'success': False,
                'error': 'token_expired',
                'message': '복구 토큰이 만료되었습니다. GIST IdP 로그인을 다시 시도해주세요.',
                'login_url': '/api/v1alpha1/account/auth/oidc/login'
            }, status=status.HTTP_401_UNAUTHORIZED)
        except BadSignature:
            return Response({
                'success': False,
                'error': 'invalid_token',
                'message': '유효하지 않은 복구 토큰입니다.',
                'login_url': '/api/v1alpha1/account/auth/oidc/login'
            }, status=status.HTTP_401_UNAUTHORIZED)

    # 4. 세션에서 확인 (fallback)
    if not recovery_user_id:
        recovery_user_id = request.session.get('recovery_user_id')
        if recovery_user_id:
            token_source = 'session'

    if not recovery_user_id:
        return Response({
            'success': False,
            'error': 'no_recovery_session',
            'message': '복구 세션이 없습니다. GIST IdP 로그인을 다시 시도해주세요.',
            'login_url': '/api/v1alpha1/account/auth/oidc/login'
        }, status=status.HTTP_401_UNAUTHORIZED)

    try:
        user = CustomUser.objects.get(user_id=recovery_user_id)
    except CustomUser.DoesNotExist:
        if token_source == 'session':
            del request.session['recovery_user_id']
        return Response({
            'success': False,
            'error': 'user_not_found',
            'message': '사용자를 찾을 수 없습니다.',
            'login_url': '/api/v1alpha1/account/auth/oidc/login'
        }, status=status.HTTP_400_BAD_REQUEST)

    if user.is_active:
        if token_source == 'session':
            del request.session['recovery_user_id']
        return Response({
            'success': False,
            'error': 'already_active',
            'message': '이미 활성화된 계정입니다.',
        }, status=status.HTTP_400_BAD_REQUEST)

    # 30일 초과 확인
    if user.deactivated_at:
        days_since_deactivation = (timezone.now() - user.deactivated_at).days
        if days_since_deactivation > 30:
            if token_source == 'session' and 'recovery_user_id' in request.session:
                del request.session['recovery_user_id']
            return Response({
                'success': False,
                'error': 'recovery_expired',
                'message': '복구 기간(30일)이 만료되었습니다. 새로 가입해주세요.',
            }, status=status.HTTP_400_BAD_REQUEST)
    else:
        days_since_deactivation = 0

    if request.method == 'GET':
        remaining_days = max(0, 30 - days_since_deactivation)
        return Response({
            'success': True,
            'message': '계정 복구 안내',
            'user_email': user.email,
            'user_name': user.name,
            'deactivated_at': user.deactivated_at.isoformat() if user.deactivated_at else None,
            'remaining_days': remaining_days,
            'recovery_info': f'{remaining_days}일 이내에 복구하지 않으면 모든 데이터가 영구 삭제됩니다.',
        }, status=status.HTTP_200_OK)

    elif request.method == 'POST':
        confirm_recovery = request.data.get('confirm_recovery', False)

        if not confirm_recovery:
            return Response({
                'success': False,
                'error': 'recovery_declined',
                'message': '계정 복구가 취소되었습니다.',
            }, status=status.HTTP_400_BAD_REQUEST)

        # 계정 복구
        user.is_active = True
        user.deactivated_at = None
        user.save()

        # 세션에서 복구 정보 삭제 (세션 기반인 경우)
        if token_source == 'session' and 'recovery_user_id' in request.session:
            del request.session['recovery_user_id']

        # Django auth 로그인
        from django.contrib.auth import login as auth_login
        auth_login(request, user)

        return Response({
            'success': True,
            'message': '계정이 복구되었습니다.',
            'user': {
                'user_id': str(user.user_id),
                'email': user.email,
                'name': user.name,
            }
        }, status=status.HTTP_200_OK)


# ============================================
# [DEPRECATED] 기존 API (하위 호환성)
# ============================================

@api_view(['GET', 'POST'])
def login_view(request):
    """
    [DEPRECATED] 기존 로그인 API

    이 API는 더 이상 사용되지 않습니다.
    GIST IdP OIDC 로그인을 사용해주세요: /api/v1alpha1/account/auth/oidc/login
    """
    return Response({
        'success': False,
        'error': 'Deprecated',
        'message': '이 API는 더 이상 사용되지 않습니다. GIST IdP 로그인을 사용해주세요.',
        'login_url': '/api/v1alpha1/account/auth/oidc/login'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def send_verification_code_view(request):
    """
    [DEPRECATED] 이메일 인증코드 발송 API

    이 API는 더 이상 사용되지 않습니다.
    GIST IdP OIDC 인증을 사용해주세요.
    """
    return Response({
        'success': False,
        'error': 'Deprecated',
        'message': '이메일 인증은 GIST IdP를 통해 처리됩니다.',
        'login_url': '/api/v1alpha1/account/auth/oidc/login'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def verify_code_view(request):
    """
    [DEPRECATED] 인증코드 검증 API

    이 API는 더 이상 사용되지 않습니다.
    GIST IdP OIDC 인증을 사용해주세요.
    """
    return Response({
        'success': False,
        'error': 'Deprecated',
        'message': '이메일 인증은 GIST IdP를 통해 처리됩니다.',
        'login_url': '/api/v1alpha1/account/auth/oidc/login'
    }, status=status.HTTP_410_GONE)


@swagger_auto_schema(
    method='get',
    operation_summary='기본정보 입력 필드 조회',
    operation_description='회원가입 시 입력 가능한 추가 정보 필드를 조회합니다.',
    manual_parameters=[
        openapi.Parameter('X-Registration-Token', openapi.IN_HEADER, description='회원가입 토큰', type=openapi.TYPE_STRING, required=True),
    ],
    responses={200: openapi.Response('필드 정보 조회 성공')}
)
@swagger_auto_schema(
    method='post',
    operation_summary='기본정보 등록 및 회원가입 완료 (Step 2)',
    operation_description='''
    추가 정보를 입력하고 회원가입을 완료합니다.

    약관 동의(/registration/agree) 완료 후 호출해야 합니다.
    - gender: 필수 (M 또는 F)
    - nickname: 필수 (2~20자)
    ''',
    request_body=BasicInfoSerializer,
    manual_parameters=[
        openapi.Parameter('X-Registration-Token', openapi.IN_HEADER, description='회원가입 토큰', type=openapi.TYPE_STRING, required=True),
    ],
    responses={
        201: openapi.Response('회원가입 완료'),
        400: openapi.Response('잘못된 요청'),
        401: openapi.Response('인증 필요 또는 이전 단계 미완료')
    }
)
@api_view(['GET', 'POST'])
def registration_basic_info_view(request):
    """
    기본정보 등록 및 회원가입 완료 (Step 2)
    GET/POST /api/v1alpha1/account/auth/registration/basic-info
    """
    # 세션 검증 (step='agreed' 필요)
    reg_sid = request.COOKIES.get('reg_sid')
    registration_token = request.headers.get('X-Registration-Token')

    if not reg_sid or not registration_token:
        return Response({
            'success': False,
            'error': 'Authentication required',
            'message': 'GIST IdP 인증이 필요합니다.',
            'login_url': '/api/v1alpha1/account/auth/oidc/login'
        }, status=status.HTTP_401_UNAUTHORIZED)

    is_valid, session_data = validate_registration_session(
        reg_sid,
        required_step='agreed',
        token=registration_token
    )

    if not is_valid:
        # step이 'oidc_authenticated'인 경우 약관 동의가 필요함
        is_oidc_auth, _ = validate_registration_session(reg_sid, required_step='oidc_authenticated')
        if is_oidc_auth:
            return Response({
                'success': False,
                'error': 'Agreement required',
                'message': '약관 동의가 필요합니다.',
                'redirect_to': '/api/v1alpha1/account/auth/registration/agree'
            }, status=status.HTTP_401_UNAUTHORIZED)

        return Response({
            'success': False,
            'error': 'Invalid or expired session',
            'message': '세션이 만료되었습니다. GIST IdP 인증을 다시 진행해주세요.',
            'login_url': '/api/v1alpha1/account/auth/oidc/login'
        }, status=status.HTTP_401_UNAUTHORIZED)

    if request.method == 'GET':
        # 입력 가능한 필드 정보 반환
        return Response({
            'success': True,
            'fields': {
                'gender': {
                    'type': 'string',
                    'required': True,
                    'choices': ['M', 'F'],
                    'description': '성별 (M: 남성, F: 여성) - 필수'
                },
                'nickname': {
                    'type': 'string',
                    'required': True,
                    'min_length': 2,
                    'max_length': 20,
                    'description': '닉네임 (2~20자) - 필수'
                }
            },
            'note': 'email, name, student_id, phone_number는 GIST IdP에서 제공됩니다. gender와 nickname은 필수 입력 항목입니다.'
        }, status=status.HTTP_200_OK)

    elif request.method == 'POST':
        # 기본정보 검증 (빈 요청도 허용)
        basic_info_serializer = BasicInfoSerializer(data=request.data)
        if not basic_info_serializer.is_valid():
            return Response({
                'success': False,
                'errors': basic_info_serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        # OIDC 사용자 정보 + 약관 동의 정보 가져오기
        oidc_user_info = session_data['data']['oidc_user_info']
        agreement_data = session_data['data']['agreement']

        # 사용자 생성 (extra_data로 추가 정보 전달)
        user, created = CustomUser.objects.get_or_create_oidc_user(
            oidc_user_info,
            extra_data=basic_info_serializer.validated_data
        )

        if not created:
            # 이미 존재하는 사용자 (동시 요청 등)
            return Response({
                'success': False,
                'error': 'User already exists',
                'message': '이미 가입된 사용자입니다.'
            }, status=status.HTTP_400_BAD_REQUEST)

        # 약관 동의 저장
        Agreement.objects.create(user=user, **agreement_data)

        # 세션 삭제 (회원가입 완료)
        from django.core.cache import cache
        from django.contrib.auth import login as auth_login
        cache.delete(f"registration:{reg_sid}")

        # Django auth 로그인 (SESSION_KEY에 user pk 저장)
        auth_login(request, user)

        response = Response({
            'success': True,
            'message': '회원가입이 완료되었습니다.',
            'user': {
                'user_id': str(user.user_id),
                'email': user.email,
                'name': user.name,
                'student_id': user.student_id
            }
        }, status=status.HTTP_201_CREATED)

        # reg_sid 쿠키 삭제
        response.delete_cookie('reg_sid')

        return response


@api_view(['GET'])
def oauth_main(request):
    """
    [DEPRECATED] OAuth 서비스 메인

    Kakao/Naver OAuth는 더 이상 사용되지 않습니다.
    GIST IdP OIDC만 지원합니다.
    """
    return Response({
        'success': False,
        'error': 'Deprecated',
        'message': 'Kakao/Naver OAuth는 더 이상 지원되지 않습니다. GIST IdP OIDC를 사용해주세요.',
        'login_url': '/api/v1alpha1/account/auth/oidc/login'
    }, status=status.HTTP_410_GONE)
