"""
30일 이상 비활성화된 사용자를 영구 삭제하는 관리 명령어

사용법:
    python manage.py cleanup_deactivated_users [--dry-run]

옵션:
    --dry-run: 실제 삭제 없이 삭제 대상만 확인

실행 주기:
    - 크론잡이나 스케줄러를 통해 매일 1회 실행 권장
    - 예: 0 3 * * * cd /app && python manage.py cleanup_deactivated_users
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction

from account.models import CustomUser


class Command(BaseCommand):
    help = '30일 이상 비활성화된 사용자와 관련 데이터를 영구 삭제합니다.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='실제 삭제 없이 삭제 대상만 확인합니다.',
        )
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='비활성화 후 삭제까지의 유예 기간 (기본값: 30일)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        retention_days = options['days']
        cutoff_date = timezone.now() - timedelta(days=retention_days)

        # 삭제 대상 조회:
        # - is_active=False
        # - deactivated_at이 설정되어 있고 cutoff_date 이전인 사용자
        users_to_delete = CustomUser.objects.filter(
            is_active=False,
            deactivated_at__isnull=False,
            deactivated_at__lt=cutoff_date
        )

        count = users_to_delete.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS('삭제할 사용자가 없습니다.'))
            return

        self.stdout.write(f'삭제 대상 사용자 수: {count}명')
        self.stdout.write(f'기준 날짜: {cutoff_date.strftime("%Y-%m-%d %H:%M:%S")} (UTC)')
        self.stdout.write('')

        # 삭제 대상 목록 출력
        for user in users_to_delete[:20]:  # 최대 20명만 표시
            days_since = (timezone.now() - user.deactivated_at).days
            self.stdout.write(
                f'  - {user.email} (탈퇴일: {user.deactivated_at.strftime("%Y-%m-%d")}, '
                f'{days_since}일 경과)'
            )

        if count > 20:
            self.stdout.write(f'  ... 외 {count - 20}명')

        self.stdout.write('')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '[DRY RUN] 실제 삭제는 수행되지 않았습니다.'
            ))
            return

        # 관련 데이터 삭제 (Match 앱)
        try:
            from match.models import Property, Survey, MatchHistory

            user_ids = list(users_to_delete.values_list('user_id', flat=True))

            with transaction.atomic():
                # Match 데이터 삭제 (FK 관계 없으므로 수동 삭제)
                prop_deleted = Property.objects.filter(user_id__in=user_ids).delete()[0]
                survey_deleted = Survey.objects.filter(user_id__in=user_ids).delete()[0]

                # MatchHistory에서 해당 사용자가 참여한 매칭 삭제
                from django.db.models import Q
                history_deleted = MatchHistory.objects.filter(
                    Q(user_a_id__in=user_ids) | Q(user_b_id__in=user_ids)
                ).delete()[0]

                # 사용자 삭제 (Agreement는 CASCADE로 자동 삭제)
                users_deleted = users_to_delete.delete()[0]

                self.stdout.write(self.style.SUCCESS(
                    f'삭제 완료:\n'
                    f'  - 사용자: {users_deleted}명\n'
                    f'  - Property: {prop_deleted}개\n'
                    f'  - Survey: {survey_deleted}개\n'
                    f'  - MatchHistory: {history_deleted}개'
                ))

        except ImportError:
            # Match 앱이 없는 경우 사용자만 삭제
            with transaction.atomic():
                users_deleted = users_to_delete.delete()[0]

                self.stdout.write(self.style.SUCCESS(
                    f'삭제 완료: 사용자 {users_deleted}명'
                ))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'삭제 중 오류 발생: {str(e)}'))
            raise
