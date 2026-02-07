import logging
import traceback

from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger('email.verification')


def send_verification_email(email, code):
    """
    GIST 이메일로 인증코드 발송

    Args:
        email: 수신 이메일 (@gist.ac.kr)
        code: 6자리 인증코드

    Returns:
        bool: 발송 성공 여부
    """
    subject = '[G-Match] 이메일 인증코드'
    message = f"""
안녕하세요, G-Match입니다.

회원가입을 위한 이메일 인증코드를 안내드립니다.

인증코드: {code}

이 코드는 5분간 유효합니다.
본인이 요청하지 않은 경우, 이 메일을 무시하셔도 됩니다.

감사합니다.
G-Match 팀
    """

    logger.info(f"[EMAIL_SEND_START] Verification email to={email}")
    logger.debug(
        f"[SMTP_CONFIG] backend={settings.EMAIL_BACKEND}, "
        f"host={settings.EMAIL_HOST}, port={settings.EMAIL_PORT}, "
        f"use_tls={settings.EMAIL_USE_TLS}, use_ssl={settings.EMAIL_USE_SSL}, "
        f"user={settings.EMAIL_HOST_USER or '(empty)'}, "
        f"password={'***' if settings.EMAIL_HOST_PASSWORD else '(empty)'}, "
        f"from={settings.DEFAULT_FROM_EMAIL}, timeout={settings.EMAIL_TIMEOUT}"
    )

    try:
        result = send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
        logger.info(f"[EMAIL_SEND_OK] Verification email sent to={email}, result={result}")
        return True
    except Exception as e:
        logger.error(
            f"[EMAIL_SEND_FAIL] Verification email to={email}, "
            f"error_type={type(e).__name__}, error={e}\n"
            f"{traceback.format_exc()}"
        )
        return False
