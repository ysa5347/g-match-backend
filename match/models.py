from django.db import models
from django.contrib.auth import get_user_model
import uuid
User = get_user_model()

class Property(models.Model):
    class PreferenceChoice(models.IntegerChoices):
        DONT_CARE = 0, '상관없음'
        PREFER = 1, '선호'
        AVOID = 2, '비선호'

    class MatchStatusChoice(models.IntegerChoices):
        NOT_STARTED = 0, '매칭 시작 전'
        IN_QUEUE = 1, '대기열 등록됨'
        MATCHED = 2, '임시 매칭됨'
        MY_APPROVED = 3, '내가 수락함'
        BOTH_APPROVED = 4, '둘 다 수락'
        PARTNER_REJECTED = 5, '상대가 거절함'
        PARTNER_REMATCHED = 6, '상대가 재매칭함'

    property_id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True)
    user_id = models.UUIDField(default=uuid.uuid4)  # 논리적 연결 (FK 제약 없음)

    # Account DB에서 받아올 내용
    nickname = models.CharField(max_length=20)
    student_id = models.SmallIntegerField()
    gender = models.CharField(max_length=1)  # 'M' or 'F'

    is_smoker = models.BooleanField()

    dorm_building = models.CharField(max_length=1)  # 'G', 'I' ... 'N'
    stay_period = models.SmallIntegerField()  # 최소 입주 기간을 나타내는 열 ...

    has_fridge = models.BooleanField()
    mate_fridge = models.SmallIntegerField(
        choices=PreferenceChoice.choices
    )
    has_router = models.BooleanField()
    mate_router = models.SmallIntegerField(
        choices=PreferenceChoice.choices
    )

    match_status = models.SmallIntegerField(
        choices=MatchStatusChoice.choices,
        default=MatchStatusChoice.NOT_STARTED
    )

    class Meta:
        db_table = 'match_properties'
        ordering = ['-created_at']

    def __str__(self):
        date_str = self.created_at.strftime('%Y-%m-%d')
        return f"[property #{self.pk}] User({self.user_id}) date({date_str})"


class Survey(models.Model):
    survey_id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True)
    user_id = models.UUIDField()  # 논리적 연결

    surveys = models.JSONField()
    weights = models.JSONField()
    scores = models.JSONField()  # 인사이트
    badges = models.JSONField()  # 인사이트

    class Meta:
        db_table = 'match_surveys'
        ordering = ['-created_at']

    def __str__(self):
        date_str = self.created_at.strftime('%Y-%m-%d')
        return f"[survey #{self.pk}] User({self.user_id}) date({date_str})"


class MatchHistory(models.Model):
    class ApprovalChoice(models.IntegerChoices):
        PENDING = 0, '대기'
        APPROVED = 1, '수락'
        REJECTED = 2, '거절'
    class ResultStatus(models.IntegerChoices):
        PENDING = 0, '결정안됨'  # 아직 쌍방 수락/거절이 안 끝난 상태
        SUCCESS = 1, '성사'      # 쌍방 수락 -> 매칭 확정
        FAILED = 2, '실패'       # 한 명이라도 거절 -> 매칭 파기

    match_id = models.BigAutoField(primary_key=True)
    matched_at = models.DateTimeField(auto_now_add=True)

    # 사용자 (논리적 연결)
    user_a_id = models.UUIDField()
    user_b_id = models.UUIDField()

    # 매칭 당시 프로필 ID (논리적 연결)
    prop_a_id = models.BigIntegerField()
    prop_b_id = models.BigIntegerField()
    surv_a_id = models.BigIntegerField()
    surv_b_id = models.BigIntegerField()

    compatibility_score = models.JSONField()  # 0~100 및 항목별 유사도점수

    a_approval = models.SmallIntegerField(
        choices=ApprovalChoice.choices,
        default=ApprovalChoice.PENDING
    )
    b_approval = models.SmallIntegerField(
        choices=ApprovalChoice.choices,
        default=ApprovalChoice.PENDING
    )

    final_match_status = models.SmallIntegerField(
        choices=ResultStatus.choices,
        default=ResultStatus.PENDING
    )

    class Meta:
        db_table = 'match_history'
        ordering = ['-matched_at']

    def __str__(self):
        return f"[history #{self.pk}] User({self.user_a_id}) & User({self.user_b_id})"