"""
Email Notifier for Matcher
- 매칭 완료 시 양쪽 사용자에게 이메일 알림 발송
- AWS SES를 통한 이메일 발송 (boto3)
"""
import logging
import threading
import boto3
from botocore.exceptions import ClientError

from config import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    AWS_SES_REGION,
    DEFAULT_FROM_EMAIL,
    FRONTEND_URL,
    EMAIL_ENABLED,
)

logger = logging.getLogger('email_notifier')


class EmailNotifier:
    """매칭 알림 이메일 발송"""

    def __init__(self):
        self.enabled = EMAIL_ENABLED and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY
        if self.enabled:
            self.ses_client = boto3.client(
                'ses',
                region_name=AWS_SES_REGION,
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY
            )
            logger.info("Email notifier initialized with AWS SES")
        else:
            self.ses_client = None
            logger.info("Email notifier disabled (EMAIL_ENABLED=false or missing AWS credentials)")

    def notify_matched(
        self,
        user_email: str,
        user_name: str,
        partner_nickname: str = None,
        compatibility_score: float = None,
        async_send: bool = True
    ) -> bool:
        """매칭됨 알림 발송"""
        if not self.enabled:
            logger.debug(f"Email disabled, skipping notification to {user_email}")
            return False

        subject = "[G-Match] 새로운 룸메이트 후보가 매칭되었습니다!"
        match_url = f"{FRONTEND_URL}/match"

        # 이메일 본문 생성
        html_body = self._generate_matched_html(
            user_name, partner_nickname, compatibility_score, match_url
        )
        text_body = self._generate_matched_text(
            user_name, partner_nickname, compatibility_score, match_url
        )

        if async_send:
            thread = threading.Thread(
                target=self._send_email,
                args=(user_email, subject, html_body, text_body)
            )
            thread.start()
            return True
        else:
            return self._send_email(user_email, subject, html_body, text_body)

    def _send_email(
        self,
        recipient: str,
        subject: str,
        html_body: str,
        text_body: str
    ) -> bool:
        """AWS SES를 통해 이메일 발송"""
        try:
            response = self.ses_client.send_email(
                Source=DEFAULT_FROM_EMAIL,
                Destination={
                    'ToAddresses': [recipient]
                },
                Message={
                    'Subject': {
                        'Data': subject,
                        'Charset': 'UTF-8'
                    },
                    'Body': {
                        'Text': {
                            'Data': text_body,
                            'Charset': 'UTF-8'
                        },
                        'Html': {
                            'Data': html_body,
                            'Charset': 'UTF-8'
                        }
                    }
                }
            )
            logger.info(f"Email sent to {recipient}, MessageId: {response['MessageId']}")
            return True
        except ClientError as e:
            logger.error(f"Failed to send email to {recipient}: {e.response['Error']['Message']}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending email to {recipient}: {e}")
            return False

    def _generate_matched_html(
        self,
        user_name: str,
        partner_nickname: str = None,
        compatibility_score: float = None,
        match_url: str = ""
    ) -> str:
        """매칭됨 알림 HTML 본문 생성"""
        info_section = ""
        if partner_nickname or compatibility_score:
            info_items = []
            if partner_nickname:
                info_items.append(f"<p><strong>상대방 닉네임:</strong> {partner_nickname}</p>")
            if compatibility_score:
                info_items.append(f"<p><strong>호환성 점수:</strong> {compatibility_score:.1f}점</p>")
            info_section = f'''
            <div style="background-color: #f3f4f6; border-radius: 8px; padding: 16px; margin: 16px 0;">
                {''.join(info_items)}
            </div>
            '''

        return f'''
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f5f5f5;">
    <div style="background-color: #ffffff; border-radius: 8px; padding: 32px; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);">
        <div style="text-align: center; margin-bottom: 24px; padding-bottom: 24px; border-bottom: 1px solid #eee;">
            <div style="font-size: 28px; font-weight: bold; color: #6366f1;">G-Match</div>
            <p style="color: #6b7280; margin: 8px 0 0 0; font-size: 14px;">GIST 룸메이트 매칭 서비스</p>
        </div>

        <div style="margin-bottom: 24px;">
            <h1 style="color: #1f2937; font-size: 22px; margin-bottom: 16px;">새로운 룸메이트 후보를 찾았습니다!</h1>
            <p style="margin-bottom: 12px; color: #4b5563;">안녕하세요, <span style="color: #6366f1; font-weight: 600;">{user_name}</span>님!</p>
            <p style="margin-bottom: 12px; color: #4b5563;">G-Match에서 회원님과 잘 맞을 것 같은 룸메이트 후보를 찾았습니다.</p>
            {info_section}
            <p style="margin-bottom: 12px; color: #4b5563;">상대방의 프로필을 확인하고 <strong>48시간 이내에</strong> 수락 여부를 결정해주세요.</p>
            <div style="text-align: center;">
                <a href="{match_url}" style="display: inline-block; background-color: #6366f1; color: #ffffff !important; text-decoration: none; padding: 14px 28px; border-radius: 8px; font-weight: 600; margin: 16px 0;">프로필 확인하기</a>
            </div>
            <p style="font-size: 14px; color: #6b7280;">상대방도 회원님의 프로필을 확인하고 있습니다.<br>양쪽 모두 수락하면 서로의 연락처가 공개됩니다.</p>
        </div>

        <div style="text-align: center; padding-top: 24px; border-top: 1px solid #eee; font-size: 12px; color: #9ca3af;">
            <p>이 메일은 G-Match 서비스에서 자동으로 발송되었습니다.</p>
            <p>&copy; 2025 G-Match. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
'''

    def _generate_matched_text(
        self,
        user_name: str,
        partner_nickname: str = None,
        compatibility_score: float = None,
        match_url: str = ""
    ) -> str:
        """매칭됨 알림 텍스트 본문 생성"""
        info_lines = []
        if partner_nickname:
            info_lines.append(f"상대방 닉네임: {partner_nickname}")
        if compatibility_score:
            info_lines.append(f"호환성 점수: {compatibility_score:.1f}점")

        info_section = "\n".join(info_lines) + "\n" if info_lines else ""

        return f"""안녕하세요, {user_name}님!

G-Match에서 회원님과 잘 맞을 것 같은 룸메이트 후보를 찾았습니다.

{info_section}
상대방의 프로필을 확인하고 48시간 이내에 수락 여부를 결정해주세요.

프로필 확인하기: {match_url}

상대방도 회원님의 프로필을 확인하고 있습니다.
양쪽 모두 수락하면 서로의 연락처가 공개됩니다.

---
이 메일은 G-Match 서비스에서 자동으로 발송되었습니다.
"""


# 싱글톤 인스턴스
_notifier = None


def get_notifier() -> EmailNotifier:
    """EmailNotifier 싱글톤 인스턴스 반환"""
    global _notifier
    if _notifier is None:
        _notifier = EmailNotifier()
    return _notifier
