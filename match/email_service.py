"""
EmailService: 매칭 상태 변경 시 이메일 알림 발송
- AWS SES를 통한 이메일 발송
- 비동기 발송 지원 (thread 기반)
"""
import logging
import traceback
import threading
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from account.models import CustomUser
from .models import Property

logger = logging.getLogger('email.match_service')


class MatchEmailService:
    """매칭 관련 이메일 알림 서비스"""

    # 이메일 발송이 필요한 상태 변경
    NOTIFICATION_EVENTS = {
        'matched': {
            'subject': '[G-Match] 새로운 룸메이트 후보가 매칭되었습니다!',
            'template': 'match/email/matched.html',
        },
        'partner_approved': {
            'subject': '[G-Match] 상대방이 매칭을 수락했습니다!',
            'template': 'match/email/partner_approved.html',
        },
        'both_approved': {
            'subject': '[G-Match] 매칭이 성사되었습니다! 🎉',
            'template': 'match/email/both_approved.html',
        },
        'partner_rejected': {
            'subject': '[G-Match] 상대방이 매칭을 거절했습니다',
            'template': 'match/email/partner_rejected.html',
        },
        'partner_rematched': {
            'subject': '[G-Match] 상대방이 재매칭을 요청했습니다',
            'template': 'match/email/partner_rematched.html',
        },
        'expired': {
            'subject': '[G-Match] 매칭 대기가 만료되었습니다',
            'template': 'match/email/expired.html',
        },
    }

    @staticmethod
    def get_user_email(user_id) -> str | None:
        """사용자 이메일 조회"""
        try:
            user = CustomUser.objects.get(user_id=user_id)
            logger.debug(f"[USER_LOOKUP] user_id={user_id}, email={user.email}")
            return user.email
        except CustomUser.DoesNotExist:
            logger.warning(f"[USER_LOOKUP_FAIL] User not found: {user_id}")
            return None

    @staticmethod
    def get_user_name(user_id) -> str:
        """사용자 이름 조회"""
        try:
            user = CustomUser.objects.get(user_id=user_id)
            name = user.nickname or user.name or '사용자'
            logger.debug(f"[USER_LOOKUP] user_id={user_id}, name={name}")
            return name
        except CustomUser.DoesNotExist:
            logger.debug(f"[USER_LOOKUP_FAIL] user_id={user_id}, using default name")
            return '사용자'

    @classmethod
    def send_notification(
        cls,
        event: str,
        user_id,
        context: dict = None,
        async_send: bool = True
    ) -> bool:
        """
        이메일 알림 발송

        Args:
            event: 알림 이벤트 타입 (matched, partner_approved, etc.)
            user_id: 수신자 user_id (UUID)
            context: 템플릿에 전달할 추가 컨텍스트
            async_send: 비동기 발송 여부 (기본: True)

        Returns:
            발송 성공 여부
        """
        logger.info(f"[NOTIFY_START] event={event}, user_id={user_id}, async={async_send}")

        if event not in cls.NOTIFICATION_EVENTS:
            logger.error(f"[NOTIFY_FAIL] Unknown notification event: {event}")
            return False

        email = cls.get_user_email(user_id)
        if not email:
            logger.error(f"[NOTIFY_FAIL] No email found for user_id={user_id}")
            return False

        event_config = cls.NOTIFICATION_EVENTS[event]
        user_name = cls.get_user_name(user_id)

        # 템플릿 컨텍스트 구성
        template_context = {
            'user_name': user_name,
            'frontend_url': settings.FRONTEND_URL,
            'match_url': f"{settings.FRONTEND_URL}/match",
            **(context or {})
        }

        logger.debug(
            f"[SMTP_CONFIG] backend={settings.EMAIL_BACKEND}, "
            f"host={settings.EMAIL_HOST}, port={settings.EMAIL_PORT}, "
            f"use_tls={settings.EMAIL_USE_TLS}, use_ssl={settings.EMAIL_USE_SSL}, "
            f"user={settings.EMAIL_HOST_USER or '(empty)'}, "
            f"password={'***' if settings.EMAIL_HOST_PASSWORD else '(empty)'}, "
            f"from={settings.DEFAULT_FROM_EMAIL}"
        )

        if async_send:
            logger.info(f"[NOTIFY_ASYNC] Dispatching to thread: event={event}, to={email}")
            thread = threading.Thread(
                target=cls._send_email,
                args=(email, event_config['subject'], event, template_context)
            )
            thread.start()
            return True
        else:
            return cls._send_email(
                email, event_config['subject'], event, template_context
            )

    @classmethod
    def _send_email(
        cls,
        recipient: str,
        subject: str,
        event: str,
        context: dict
    ) -> bool:
        """실제 이메일 발송 (동기)"""
        logger.info(f"[EMAIL_SEND_START] event={event}, to={recipient}, subject={subject}")
        try:
            # HTML 템플릿 렌더링 시도, 실패 시 기본 텍스트 사용
            template_name = cls.NOTIFICATION_EVENTS[event]['template']
            try:
                logger.debug(f"[TEMPLATE_RENDER] template={template_name}")
                html_message = render_to_string(template_name, context)
                plain_message = strip_tags(html_message)
                logger.debug(f"[TEMPLATE_RENDER_OK] html_len={len(html_message)}, plain_len={len(plain_message)}")
            except Exception as template_error:
                logger.warning(
                    f"[TEMPLATE_RENDER_FAIL] template={template_name}, "
                    f"error_type={type(template_error).__name__}, error={template_error}"
                )
                # 기본 텍스트 메시지 사용
                html_message, plain_message = cls._get_fallback_message(event, context)
                logger.debug(f"[FALLBACK_MSG] Using fallback message for event={event}")

            logger.debug(
                f"[SMTP_SENDING] from={settings.DEFAULT_FROM_EMAIL}, to={recipient}, "
                f"backend={settings.EMAIL_BACKEND}"
            )
            result = send_mail(
                subject=subject,
                message=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                html_message=html_message,
                fail_silently=False,
            )

            logger.info(f"[EMAIL_SEND_OK] event={event}, to={recipient}, result={result}")
            return True

        except Exception as e:
            logger.error(
                f"[EMAIL_SEND_FAIL] event={event}, to={recipient}, "
                f"error_type={type(e).__name__}, error={e}\n"
                f"{traceback.format_exc()}"
            )
            return False

    @staticmethod
    def _get_fallback_message(event: str, context: dict) -> tuple[str, str]:
        """템플릿 렌더링 실패 시 기본 메시지"""
        user_name = context.get('user_name', '사용자')
        match_url = context.get('match_url', '')

        messages = {
            'matched': (
                f"안녕하세요, {user_name}님!\n\n"
                f"새로운 룸메이트 후보가 매칭되었습니다.\n"
                f"G-Match에서 상대방의 프로필을 확인하고 수락 여부를 결정해주세요.\n\n"
                f"매칭 확인하기: {match_url}"
            ),
            'partner_approved': (
                f"안녕하세요, {user_name}님!\n\n"
                f"좋은 소식입니다! 상대방이 매칭을 수락했습니다.\n"
                f"아직 수락하지 않으셨다면 G-Match에서 확인해주세요.\n\n"
                f"매칭 확인하기: {match_url}"
            ),
            'both_approved': (
                f"안녕하세요, {user_name}님!\n\n"
                f"축하합니다! 매칭이 성사되었습니다! 🎉\n"
                f"G-Match에서 상대방의 연락처를 확인하고 연락해보세요.\n\n"
                f"연락처 확인하기: {match_url}"
            ),
            'partner_rejected': (
                f"안녕하세요, {user_name}님!\n\n"
                f"아쉽게도 상대방이 매칭을 거절했습니다.\n"
                f"새로운 룸메이트를 찾으시려면 재매칭을 요청해주세요.\n\n"
                f"재매칭하기: {match_url}"
            ),
            'partner_rematched': (
                f"안녕하세요, {user_name}님!\n\n"
                f"상대방이 재매칭을 요청했습니다.\n"
                f"새로운 룸메이트를 찾으시려면 재매칭을 요청해주세요.\n\n"
                f"재매칭하기: {match_url}"
            ),
            'expired': (
                f"안녕하세요, {user_name}님!\n\n"
                f"매칭 대기 시간이 만료되어 대기열에서 제외되었습니다.\n"
                f"새로운 룸메이트를 찾으시려면 다시 매칭을 시작해주세요.\n\n"
                f"매칭 시작하기: {match_url}"
            ),
        }

        plain = messages.get(event, f"G-Match 알림이 있습니다. {match_url}")
        html = f"<html><body><p>{plain.replace(chr(10), '<br>')}</p></body></html>"

        return html, plain

    # ==================== 편의 메서드 ====================

    @classmethod
    def notify_matched(cls, user_id, partner_nickname: str = None, compatibility_score: float = None):
        """매칭됨 알림 (status 2)"""
        context = {}
        if partner_nickname:
            context['partner_nickname'] = partner_nickname
        if compatibility_score is not None:
            context['compatibility_score'] = round(compatibility_score, 1)
        return cls.send_notification('matched', user_id, context)

    @classmethod
    def notify_partner_approved(cls, user_id):
        """상대방 수락 알림 (내가 아직 미수락 상태일 때)"""
        return cls.send_notification('partner_approved', user_id)

    @classmethod
    def notify_both_approved(cls, user_id):
        """매칭 성사 알림 (status 4)"""
        return cls.send_notification('both_approved', user_id)

    @classmethod
    def notify_partner_rejected(cls, user_id):
        """상대방 거절 알림 (status 5)"""
        return cls.send_notification('partner_rejected', user_id)

    @classmethod
    def notify_partner_rematched(cls, user_id):
        """상대방 재매칭 알림 (status 6)"""
        return cls.send_notification('partner_rematched', user_id)

    @classmethod
    def notify_expired(cls, user_id):
        """매칭 대기 만료 알림 (status 9)"""
        return cls.send_notification('expired', user_id)
