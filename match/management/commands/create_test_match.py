import uuid
from django.core.management.base import BaseCommand
from django.utils import timezone
from account.models import CustomUser
from match.models import Property, Survey, MatchHistory


class Command(BaseCommand):
    help = 'Create test match data for a user'

    def add_arguments(self, parser):
        parser.add_argument('email', type=str, help='Email of the user')
        parser.add_argument(
            '--status',
            type=int,
            default=2,
            help='Match status (2=MATCHED, 3=MY_APPROVED, 4=BOTH_APPROVED, 5=PARTNER_REJECTED, 6=PARTNER_REMATCHED, 9=EXPIRED)'
        )

    def handle(self, *args, **options):
        email = options['email']
        match_status = options['status']

        try:
            user = CustomUser.objects.get(email=email)
        except CustomUser.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'User {email} does not exist'))
            return

        # 가상의 파트너 사용자 생성
        partner_email = f'partner_{uuid.uuid4().hex[:8]}@gm.gist.ac.kr'
        partner_user = CustomUser.objects.create_user(
            email=partner_email,
            name='홍길동',
            student_id='20245112',
            gender='M',
            nickname='테스트룸메',
            phone_number='010-1234-5678'
        )

        self.stdout.write(f'Created partner user: {partner_email}')

        # 현재 사용자의 Property와 Survey 가져오기
        user_property = Property.objects.filter(user_id=user.user_id).last()
        user_survey = Survey.objects.filter(user_id=user.user_id).last()

        if not user_property or not user_survey:
            self.stdout.write(
                self.style.ERROR('User must have Property and Survey data')
            )
            return

        # status 9(EXPIRED)는 파트너/MatchHistory 없이 상태만 변경
        if match_status == 9:
            user_property.match_status = match_status
            user_property.save()
            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully set EXPIRED status for {email} (no partner/history needed)'
                )
            )
            return

        # 파트너의 Property와 Survey 생성
        partner_property = Property.objects.create(
            user_id=partner_user.user_id,
            nickname=partner_user.nickname,
            student_id=int(str(partner_user.student_id)[2:4]),
            gender=partner_user.gender,
            is_smoker=False,
            dorm_building='G',
            stay_period=3,
            has_fridge=True,
            mate_fridge=0,
            has_router=True,
            mate_router=0,
            match_status=match_status if match_status in [3, 4] else 2
        )

        partner_survey = Survey.objects.create(
            user_id=partner_user.user_id,
            surveys={
                "time_1": 3, "time_2": 4, "time_3": 3, "time_4": 4,
                "clean_1": 4, "clean_2": 4, "clean_3": 3, "clean_4": 4,
                "habit_1": 3, "habit_2": 4, "habit_3": 3, "habit_4": 3,
                "social_1": 3, "social_2": 3, "social_3": 4, "social_4": 3, "social_5": 3,
                "etc_1": 4, "etc_2": 3
            },
            weights={
                "time_1": 1.5, "time_2": 1.5, "time_3": 1.0, "time_4": 1.0,
                "clean_1": 1.5, "clean_2": 1.0, "clean_3": 1.0, "clean_4": 1.0,
                "habit_1": 1.0, "habit_2": 1.0, "habit_3": 1.0, "habit_4": 0.5,
                "social_1": 1.0, "social_2": 1.0, "social_3": 1.0, "social_4": 0.5, "social_5": 0.5,
                "etc_1": 1.0, "etc_2": 0.5
            },
            scores={
                "생활 리듬": 4.5,
                "공간 관리": 1.4,
                "생활 습관": 2,
                "사회성": 3
            },
            badges={
                "badge1": "아침형",
                "badge2": "깔끔형",
                "badge3": "조용한편"
            }
        )

        self.stdout.write(f'Created partner Property (ID: {partner_property.property_id})')
        self.stdout.write(f'Created partner Survey (ID: {partner_survey.survey_id})')

        # MatchHistory 생성 (상태 2 이상일 때만)
        if match_status >= 2:
            # approval 상태 설정
            a_approval = 0  # PENDING
            b_approval = 0  # PENDING
            final_status = MatchHistory.ResultStatus.PENDING

            if match_status == 3:  # MY_APPROVED
                a_approval = 1  # APPROVED
            elif match_status == 4:  # BOTH_APPROVED
                a_approval = 1
                b_approval = 1
                final_status = MatchHistory.ResultStatus.SUCCESS
            elif match_status in [5, 6]:  # PARTNER_REJECTED or PARTNER_REMATCHED
                b_approval = 2  # REJECTED
                final_status = MatchHistory.ResultStatus.FAILED

            match_history = MatchHistory.objects.create(
                user_a_id=user.user_id,
                user_b_id=partner_user.user_id,
                prop_a_id=user_property.property_id,
                prop_b_id=partner_property.property_id,
                surv_a_id=user_survey.survey_id,
                surv_b_id=partner_survey.survey_id,
                compatibility_score=89.50,
                a_approval=a_approval,
                b_approval=b_approval,
                final_match_status=final_status
            )

            self.stdout.write(f'Created MatchHistory (ID: {match_history.match_id})')

        # 사용자의 match_status 업데이트
        user_property.match_status = match_status
        user_property.save()

        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully created test match data for {email} with status {match_status}'
            )
        )
