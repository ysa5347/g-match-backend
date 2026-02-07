"""
GIST IdP OIDC 유틸리티 함수

GIST IdP와의 OIDC 인증 흐름을 처리하는 유틸리티 함수들
"""
import secrets
import hashlib
import base64
import json
import time
import jwt
import requests
from urllib.parse import urlencode
from django.conf import settings
from django.core.cache import cache


class OIDCError(Exception):
    """OIDC 관련 에러"""
    pass


class OIDCTokenError(OIDCError):
    """토큰 관련 에러"""
    pass


class OIDCValidationError(OIDCError):
    """검증 관련 에러"""
    pass


# ==============================================
# State, Nonce & PKCE 관리
# ==============================================

def generate_state():
    """
    CSRF 방지를 위한 state 파라미터 생성

    Returns:
        str: 32바이트 URL-safe 랜덤 문자열
    """
    return secrets.token_urlsafe(32)


def generate_nonce():
    """
    리플레이 공격 방지를 위한 nonce 생성

    Returns:
        str: 32바이트 URL-safe 랜덤 문자열
    """
    return secrets.token_urlsafe(32)


def generate_code_verifier():
    """
    PKCE code_verifier 생성

    Returns:
        str: 43-128자 URL-safe 랜덤 문자열
    """
    return secrets.token_urlsafe(64)


def generate_code_challenge(code_verifier):
    """
    PKCE code_challenge 생성 (S256 방식)

    Args:
        code_verifier: code_verifier 문자열

    Returns:
        str: Base64 URL-safe 인코딩된 SHA256 해시
    """
    digest = hashlib.sha256(code_verifier.encode('ascii')).digest()
    # Base64 URL-safe 인코딩 (패딩 제거)
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


def store_oidc_state(state, nonce, code_verifier, redirect_after=None):
    """
    OIDC state, nonce, code_verifier를 Redis에 저장

    Args:
        state: state 파라미터
        nonce: nonce 파라미터
        code_verifier: PKCE code_verifier
        redirect_after: 인증 후 리다이렉트할 URL (선택)
    """
    import logging
    logger = logging.getLogger(__name__)

    key = f"oidc_state:{state}"
    data = {
        'nonce': nonce,
        'code_verifier': code_verifier,
        'redirect_after': redirect_after,
        'created_at': time.time()
    }
    ttl = settings.GIST_OIDC.get('STATE_TTL', 600)

    logger.info(f"[OIDC] Storing state: {key}, TTL: {ttl}")
    cache.set(key, data, timeout=ttl)

    # 저장 확인
    verify = cache.get(key)
    logger.info(f"[OIDC] State stored verification: {verify is not None}")


def validate_oidc_state(state):
    """
    OIDC state 검증 및 데이터 조회

    Args:
        state: 검증할 state 파라미터

    Returns:
        dict: 저장된 state 데이터 (nonce, redirect_after 포함)

    Raises:
        OIDCValidationError: state가 유효하지 않은 경우
    """
    import logging
    logger = logging.getLogger(__name__)

    key = f"oidc_state:{state}"
    logger.info(f"[OIDC] Validating state: {key}")

    data = cache.get(key)
    logger.info(f"[OIDC] State data from cache: {data is not None}")

    if not data:
        logger.error(f"[OIDC] State validation failed - state not found in cache")
        raise OIDCValidationError('Invalid or expired state parameter')

    # 사용 후 삭제 (일회성)
    cache.delete(key)
    logger.info(f"[OIDC] State validated successfully, data keys: {list(data.keys())}")
    return data


# ==============================================
# Authorization URL 생성
# ==============================================

def build_authorization_url(redirect_after=None):
    """
    GIST IdP 인증 URL 생성 (PKCE 지원)

    Args:
        redirect_after: 인증 완료 후 리다이렉트할 클라이언트 URL

    Returns:
        dict: {
            'authorization_url': str,
            'state': str
        }
    """
    state = generate_state()
    nonce = generate_nonce()

    # PKCE: code_verifier 및 code_challenge 생성
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)

    # State 저장 (code_verifier 포함)
    store_oidc_state(state, nonce, code_verifier, redirect_after)

    params = {
        'response_type': 'code',
        'client_id': settings.GIST_OIDC['CLIENT_ID'],
        'redirect_uri': settings.GIST_OIDC['REDIRECT_URI'],
        'scope': ' '.join(settings.GIST_OIDC['SCOPES']),
        'state': state,
        'nonce': nonce,
        'prompt': 'consent',  # 항상 동의 화면 표시 (모바일 redirect loop 방지)
        # PKCE 파라미터
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
    }

    authorization_url = f"{settings.GIST_OIDC['AUTHORIZATION_ENDPOINT']}?{urlencode(params)}"

    return {
        'authorization_url': authorization_url,
        'state': state
    }


# ==============================================
# Token Exchange
# ==============================================

def exchange_code_for_tokens(code, code_verifier):
    """
    Authorization code를 토큰으로 교환 (PKCE 지원)

    Args:
        code: Authorization code
        code_verifier: PKCE code_verifier

    Returns:
        dict: {
            'access_token': str,
            'id_token': str,
            'refresh_token': str (선택),
            'token_type': str,
            'expires_in': int
        }

    Raises:
        OIDCTokenError: 토큰 교환 실패 시
    """
    import logging
    logger = logging.getLogger(__name__)

    token_endpoint = settings.GIST_OIDC['TOKEN_ENDPOINT']

    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': settings.GIST_OIDC['REDIRECT_URI'],
        'client_id': settings.GIST_OIDC['CLIENT_ID'],
        'client_secret': settings.GIST_OIDC['CLIENT_SECRET'],
        # PKCE: code_verifier 전송
        'code_verifier': code_verifier,
    }

    logger.info(f"[OIDC] Token exchange - endpoint: {token_endpoint}")
    logger.info(f"[OIDC] Token exchange - redirect_uri: {data['redirect_uri']}")
    logger.info(f"[OIDC] Token exchange - client_id: {data['client_id'][:10]}...")

    try:
        response = requests.post(
            token_endpoint,
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10
        )

        logger.info(f"[OIDC] Token response status: {response.status_code}")

        if response.status_code != 200:
            error_data = response.json() if response.content else {}
            logger.error(f"[OIDC] Token exchange failed: {response.status_code} - {response.text}")
            raise OIDCTokenError(
                f"Token exchange failed: {error_data.get('error', 'Unknown error')} - "
                f"{error_data.get('error_description', '')}"
            )

        logger.info(f"[OIDC] Token exchange successful")
        return response.json()

    except requests.RequestException as e:
        logger.error(f"[OIDC] Token endpoint request failed: {str(e)}")
        raise OIDCTokenError(f"Token endpoint request failed: {str(e)}")


# ==============================================
# ID Token 검증
# ==============================================

def get_jwks():
    """
    GIST IdP의 JWKS (JSON Web Key Set) 조회

    Returns:
        dict: JWKS 데이터

    캐싱: 1시간 동안 캐시
    """
    cache_key = 'gist_oidc_jwks'
    jwks = cache.get(cache_key)

    if not jwks:
        try:
            response = requests.get(
                settings.GIST_OIDC['JWKS_URI'],
                timeout=10
            )
            response.raise_for_status()
            jwks = response.json()
            cache.set(cache_key, jwks, timeout=3600)  # 1시간 캐시
        except requests.RequestException as e:
            raise OIDCError(f"Failed to fetch JWKS: {str(e)}")

    return jwks


def validate_id_token(id_token, nonce):
    """
    ID Token 검증

    Args:
        id_token: 검증할 ID Token
        nonce: 저장된 nonce 값

    Returns:
        dict: 검증된 ID Token의 claims

    Raises:
        OIDCValidationError: 토큰 검증 실패 시
    """
    try:
        # JWKS 가져오기
        jwks = get_jwks()

        # 헤더에서 kid와 alg 추출
        unverified_header = jwt.get_unverified_header(id_token)
        kid = unverified_header.get('kid')
        alg = unverified_header.get('alg', 'ES256')  # GIST IdP uses ES256

        signing_key = None

        for key in jwks.get('keys', []):
            if key.get('kid') == kid:
                key_alg = key.get('alg', alg)
                key_kty = key.get('kty')

                # 알고리즘에 따라 적절한 키 타입 사용
                if key_kty == 'EC' or key_alg.startswith('ES'):
                    # ECDSA (ES256, ES384, ES512)
                    signing_key = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(key))
                elif key_kty == 'RSA' or key_alg.startswith('RS'):
                    # RSA (RS256, RS384, RS512)
                    signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))
                else:
                    # 기타 알고리즘은 PyJWKClient 사용 시도
                    try:
                        jwk_key = jwt.PyJWK.from_dict(key)
                        signing_key = jwk_key.key
                    except Exception:
                        continue
                break

        if not signing_key:
            raise OIDCValidationError('Unable to find appropriate signing key')

        # 토큰 검증
        # 지원 알고리즘: ES256 (GIST IdP 기본), RS256
        supported_algs = ['ES256', 'ES384', 'ES512', 'RS256', 'RS384', 'RS512']
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=supported_algs,
            audience=settings.GIST_OIDC['CLIENT_ID'],
            issuer=settings.GIST_OIDC['ISSUER'] if settings.GIST_OIDC.get('ID_TOKEN_ISSUER_VALIDATION') else None,
            options={
                'verify_aud': settings.GIST_OIDC.get('ID_TOKEN_AUDIENCE_VALIDATION', True),
                'verify_iss': settings.GIST_OIDC.get('ID_TOKEN_ISSUER_VALIDATION', True),
                'verify_exp': True,
                'verify_iat': True,
            }
        )

        # Nonce 검증
        if claims.get('nonce') != nonce:
            raise OIDCValidationError('Invalid nonce in ID token')

        return claims

    except jwt.ExpiredSignatureError:
        raise OIDCValidationError('ID token has expired')
    except jwt.InvalidAudienceError:
        raise OIDCValidationError('Invalid audience in ID token')
    except jwt.InvalidIssuerError:
        raise OIDCValidationError('Invalid issuer in ID token')
    except jwt.PyJWTError as e:
        raise OIDCValidationError(f'ID token validation failed: {str(e)}')


# ==============================================
# User Info 추출
# ==============================================

def extract_user_info_from_id_token(id_token_claims):
    """
    ID Token claims에서 사용자 정보 추출

    GIST IdP claims_supported (from OIDC_info.json):
    - sub: 고유 식별자
    - profile: 프로필 정보 (이름 포함)
    - email: 이메일
    - phone_number: 전화번호
    - student_id: 학번

    Args:
        id_token_claims: 검증된 ID Token claims

    Returns:
        dict: 정규화된 사용자 정보
    """
    # sub (고유 식별자)
    sub = id_token_claims.get('sub')

    # email
    email = id_token_claims.get('email')

    # name - profile 또는 name 필드에서 추출
    # profile이 문자열이면 그대로 사용, 객체이면 name 속성 추출
    profile = id_token_claims.get('profile')
    if isinstance(profile, str):
        name = profile
    elif isinstance(profile, dict):
        name = profile.get('name', '')
    else:
        name = id_token_claims.get('name', '')

    # student_id
    student_id = id_token_claims.get('student_id')

    # phone_number
    phone_number = id_token_claims.get('phone_number')

    return {
        'sub': sub,
        'email': email,
        'name': name,
        'student_id': student_id,
        'phone_number': phone_number,
        'email_verified': True,  # GIST IdP를 통해 인증된 이메일은 검증됨
    }


def fetch_userinfo(access_token):
    """
    UserInfo 엔드포인트에서 추가 사용자 정보 조회

    GIST IdP UserInfo 응답 예시:
    {
        "user_uuid": "9394a48a-8db3-11ed-a1eb-0242ac120002",
        "user_email_id": "example@gist.ac.kr",
        "user_name": "홍길동",
        "user_phone_number": "01012345678",
        "student_id": "20225000"
    }

    Args:
        access_token: Access Token

    Returns:
        dict: 정규화된 UserInfo 응답

    Raises:
        OIDCError: UserInfo 조회 실패 시
    """
    userinfo_endpoint = settings.GIST_OIDC['USERINFO_ENDPOINT']

    try:
        response = requests.get(
            userinfo_endpoint,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )

        if response.status_code != 200:
            raise OIDCError(f"UserInfo request failed: {response.status_code}")

        userinfo = response.json()

        # GIST IdP 응답을 정규화된 형식으로 변환
        return {
            'sub': userinfo.get('user_uuid'),
            'email': userinfo.get('user_email_id'),
            'name': userinfo.get('user_name', ''),
            'student_id': userinfo.get('student_id'),
            'phone_number': userinfo.get('user_phone_number'),
            'email_verified': True,
        }

    except requests.RequestException as e:
        raise OIDCError(f"UserInfo endpoint request failed: {str(e)}")


# ==============================================
# OIDC Callback 처리
# ==============================================

def process_oidc_callback(code, state):
    """
    OIDC Callback 처리 통합 함수 (PKCE 지원)

    GIST IdP (인포팀 계정)의 경우:
    - Token 응답에 id_token이 포함될 수 있음
    - id_token이 없는 경우 UserInfo 엔드포인트로 사용자 정보 조회

    Args:
        code: Authorization code
        state: State 파라미터

    Returns:
        dict: {
            'user_info': dict,  # 사용자 정보
            'tokens': dict,     # 토큰 정보
            'redirect_after': str  # 인증 후 리다이렉트 URL
        }

    Raises:
        OIDCError: OIDC 처리 실패 시
    """
    import logging
    logger = logging.getLogger(__name__)

    logger.info(f"[OIDC] Processing callback - code: {code[:20]}..., state: {state[:20]}...")

    # 1. State 검증 및 code_verifier 가져오기
    state_data = validate_oidc_state(state)
    nonce = state_data['nonce']
    code_verifier = state_data['code_verifier']

    logger.info(f"[OIDC] State validated - nonce: {nonce[:10]}..., code_verifier: {code_verifier[:10]}...")

    # 2. Token 교환 (PKCE: code_verifier 전송)
    logger.info(f"[OIDC] Exchanging code for tokens...")
    tokens = exchange_code_for_tokens(code, code_verifier)
    logger.info(f"[OIDC] Token exchange successful - keys: {list(tokens.keys())}")

    # 3. 사용자 정보 추출
    # ID Token이 있으면 검증 후 사용, 없으면 UserInfo 엔드포인트 사용
    if tokens.get('id_token'):
        try:
            id_token_claims = validate_id_token(tokens['id_token'], nonce)
            user_info = extract_user_info_from_id_token(id_token_claims)
        except OIDCValidationError:
            # ID Token 검증 실패 시 UserInfo 엔드포인트로 fallback
            user_info = fetch_userinfo(tokens['access_token'])
    else:
        # ID Token이 없는 경우 UserInfo 엔드포인트 사용
        user_info = fetch_userinfo(tokens['access_token'])

    # 4. 이메일 검증 확인 (GIST IdP는 이메일 검증됨)
    if not user_info.get('email'):
        raise OIDCValidationError('Email not provided by IdP')

    # GIST 이메일 도메인 검증
    if not (user_info['email'].endswith('@gist.ac.kr') or user_info['email'].endswith('@gm.gist.ac.kr')):
        raise OIDCValidationError(f'Only GIST email addresses are allowed. {user_info["email"]}')

    return {
        'user_info': user_info,
        'tokens': tokens,
        'redirect_after': state_data.get('redirect_after')
    }
