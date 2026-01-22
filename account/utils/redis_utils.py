import secrets
import string
from django.core.cache import cache
from datetime import timedelta


def generate_reg_sid():
    """회원가입 세션 ID 생성 (32자 랜덤 문자열)"""
    return secrets.token_urlsafe(24)


def generate_registration_token():
    """회원가입 토큰 생성 (64자 랜덤 문자열)"""
    return secrets.token_urlsafe(48)


def store_registration_session(reg_sid, data, step, token=None):
    """
    회원가입 세션 데이터 저장

    Args:
        reg_sid: 회원가입 세션 ID
        data: 저장할 데이터 (dict)
        step: 현재 단계 ('email_verified', 'agreed', 'basic_info_submitted')
        token: registration_token (선택)
    """
    key = f"registration:{reg_sid}"
    session_data = {
        'step': step,
        'data': data,
        'token': token
    }
    cache.set(key, session_data, timeout=1800)  # 30분


def validate_registration_session(reg_sid, required_step=None, token=None):
    """
    회원가입 세션 검증

    Args:
        reg_sid: 회원가입 세션 ID
        required_step: 필요한 단계 (선택)
        token: registration_token (선택)

    Returns:
        tuple: (is_valid, session_data)
    """
    key = f"registration:{reg_sid}"
    session_data = cache.get(key)

    if not session_data:
        return False, None

    if required_step and session_data.get('step') != required_step:
        return False, None

    if token and session_data.get('token') != token:
        return False, None

    return True, session_data


def store_verification_code(email, code, reg_sid):
    """
    이메일 인증코드 저장

    Args:
        email: 이메일 주소
        code: 인증코드 (6자리)
        reg_sid: 회원가입 세션 ID
    """
    key = f"verification_code:{reg_sid}:{email}"
    cache.set(key, code, timeout=300)  # 5분


def validate_verification_code(email, code, reg_sid):
    """
    이메일 인증코드 검증

    Args:
        email: 이메일 주소
        code: 입력된 인증코드
        reg_sid: 회원가입 세션 ID

    Returns:
        bool: 검증 성공 여부
    """
    key = f"verification_code:{reg_sid}:{email}"
    stored_code = cache.get(key)

    if not stored_code:
        return False

    if stored_code == code:
        cache.delete(key)
        return True

    # 실패 횟수 증가
    fail_key = f"verification_fail:{reg_sid}:{email}"
    fail_count = cache.get(fail_key, 0)
    fail_count += 1

    if fail_count >= 5:
        cache.set(fail_key, fail_count, timeout=900)  # 15분 블록
    else:
        cache.set(fail_key, fail_count, timeout=300)  # 5분

    return False


def increment_email_send_count(email):
    """
    이메일 발송 횟수 증가

    Args:
        email: 이메일 주소

    Returns:
        int: 현재 발송 횟수
    """
    minute_key = f"email_send_minute:{email}"
    day_key = f"email_send_day:{email}"

    # 1분 카운트
    minute_count = cache.get(minute_key, 0)
    cache.set(minute_key, minute_count + 1, timeout=60)

    # 하루 카운트
    day_count = cache.get(day_key, 0)
    cache.set(day_key, day_count + 1, timeout=86400)  # 24시간

    return minute_count + 1


def check_email_send_limit(email):
    """
    이메일 발송 제한 확인

    Args:
        email: 이메일 주소

    Returns:
        tuple: (can_send, reason)
    """
    minute_key = f"email_send_minute:{email}"
    day_key = f"email_send_day:{email}"

    minute_count = cache.get(minute_key, 0)
    day_count = cache.get(day_key, 0)

    if minute_count >= 1:
        return False, "1분에 1회만 발송 가능합니다."

    if day_count >= 5:
        return False, "하루 최대 5회까지 발송 가능합니다."

    return True, None


def increment_login_attempts(email):
    """
    로그인 시도 횟수 증가

    Args:
        email: 이메일 주소

    Returns:
        int: 현재 시도 횟수
    """
    key = f"login_attempts:{email}"
    attempts = cache.get(key, 0)
    attempts += 1

    if attempts >= 5:
        cache.set(key, attempts, timeout=1800)  # 30분 잠금
    else:
        cache.set(key, attempts, timeout=300)  # 5분

    return attempts


def check_login_attempts(email):
    """
    로그인 시도 횟수 확인

    Args:
        email: 이메일 주소

    Returns:
        tuple: (is_locked, attempts)
    """
    key = f"login_attempts:{email}"
    attempts = cache.get(key, 0)

    if attempts >= 5:
        return True, attempts

    return False, attempts


def reset_login_attempts(email):
    """
    로그인 시도 횟수 초기화

    Args:
        email: 이메일 주소
    """
    key = f"login_attempts:{email}"
    cache.delete(key)


def generate_verification_code():
    """6자리 숫자 인증코드 생성"""
    return ''.join(secrets.choice(string.digits) for _ in range(6))
