from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings


class Command(BaseCommand):
    help = 'Test AWS SES email sending'

    def add_arguments(self, parser):
        parser.add_argument(
            '--to',
            type=str,
            required=True,
            help='Recipient email address (must be verified in SES sandbox)',
        )

    def handle(self, *args, **options):
        recipient = options['to']

        self.stdout.write(self.style.WARNING(f'Sending test email to: {recipient}'))
        self.stdout.write(f'From: {settings.DEFAULT_FROM_EMAIL}')
        self.stdout.write(f'AWS Region: {settings.AWS_SES_REGION_NAME}')

        # Temporarily use SES backend for this test
        original_backend = settings.EMAIL_BACKEND
        settings.EMAIL_BACKEND = 'django_ses.SESBackend'

        try:
            send_mail(
                subject='G-Match AWS SES 테스트 이메일',
                message='이 이메일은 G-Match 프로젝트의 AWS SES 연동 테스트입니다.\n\n정상적으로 수신되셨다면 SES 설정이 완료되었습니다!',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=False,
            )

            self.stdout.write(self.style.SUCCESS(f'✓ 이메일이 성공적으로 전송되었습니다!'))
            self.stdout.write(self.style.SUCCESS(f'  수신자: {recipient}'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ 이메일 전송 실패: {str(e)}'))
            self.stdout.write('\n문제 해결 방법:')
            self.stdout.write('1. AWS SES에서 발신자 이메일이 검증되었는지 확인')
            self.stdout.write('2. 샌드박스 모드에서는 수신자 이메일도 검증 필요')
            self.stdout.write('3. .env 파일의 AWS 자격증명 확인')
            self.stdout.write(f'4. AWS SES 리전 확인: {settings.AWS_SES_REGION_NAME}')

        finally:
            # Restore original backend
            settings.EMAIL_BACKEND = original_backend
