"""
MatchingService: 매칭 관련 비즈니스 로직
- Redis 대기열 관리
- MatchHistory 조회/업데이트
- 상태 전이 로직
    - ACCOUNT 완료시 연락처 조회 로직 연동 필요
"""
import json
import redis
from django.utils import timezone
from django.db import transaction
from django.db.models import Q

from account.models import CustomUser
from .models import Property, Survey, MatchHistory


class RedisQueueService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def register_user(self, user_id, property_obj: Property, survey_obj: Survey):
        queue_data = {
            "user_id": str(user_id),  # UUID를 문자열로 변환
            "property_id": property_obj.property_id,
            "survey_id": survey_obj.survey_id,
            "basic": {
                "gender": property_obj.gender,
                "dorm_building": property_obj.dorm_building,
                "stay_period": property_obj.stay_period,
                "is_smoker": property_obj.is_smoker,
                "has_fridge": property_obj.has_fridge,
                "mate_fridge": property_obj.mate_fridge,
                "has_router": property_obj.has_router,
                "mate_router": property_obj.mate_router,
            },
            "survey": survey_obj.surveys,
            "weights": survey_obj.weights,
            "priority": 0,
            "registered_at": timezone.now().isoformat(),
            "edge_calculated": False
        }

        redis_key = f"match:user-queue:{user_id}"
        self.redis.set(redis_key, json.dumps(queue_data))

    def remove_user(self, user_id: int):
        redis_key = f"match:user-queue:{user_id}"
        self.redis.delete(redis_key)


class MatchHistoryService:
    @staticmethod
    def get_by_status(user_id: int, match_status: int) -> MatchHistory | None:
        status_to_result = {
            Property.MatchStatusChoice.MATCHED: MatchHistory.ResultStatus.PENDING,
            Property.MatchStatusChoice.MY_APPROVED: MatchHistory.ResultStatus.PENDING,
            Property.MatchStatusChoice.BOTH_APPROVED: MatchHistory.ResultStatus.SUCCESS,
            Property.MatchStatusChoice.PARTNER_REJECTED: MatchHistory.ResultStatus.FAILED,
            Property.MatchStatusChoice.PARTNER_REMATCHED: MatchHistory.ResultStatus.FAILED,
        }

        result_status = status_to_result.get(match_status)
        if result_status is None:
            return None

        return MatchHistory.objects.filter(
            Q(user_a_id=user_id) | Q(user_b_id=user_id),
            final_match_status=result_status
        ).order_by('-matched_at').first()

    @staticmethod
    def get_partner_id(match_history: MatchHistory, my_id: int) -> int:
        if match_history.user_a_id == my_id:
            return match_history.user_b_id
        return match_history.user_a_id

    @staticmethod
    def get_partner_profile_ids(match_history: MatchHistory, my_id: int) -> tuple[int, int]:
        if match_history.user_a_id == my_id:
            return match_history.prop_b_id, match_history.surv_b_id
        return match_history.prop_a_id, match_history.surv_a_id

    @staticmethod
    def update_my_approval(match_history: MatchHistory, my_id: int, approval_status: int):
        if match_history.user_a_id == my_id:
            match_history.a_approval = approval_status
        else:
            match_history.b_approval = approval_status
        match_history.save()

    @staticmethod
    def get_partner_approval(match_history: MatchHistory, my_id: int) -> int:
        if match_history.user_a_id == my_id:
            return match_history.b_approval
        return match_history.a_approval


class MatchingService:
    MATCH_EXPIRY_DAYS = 30

    def __init__(self, redis_client: redis.Redis):
        self.redis_service = RedisQueueService(redis_client)
        self.history_service = MatchHistoryService()

    # ==================== 상태 조회 ====================
    def get_status(self, user_id: int) -> dict:
        property_obj = Property.objects.filter(user_id=user_id).last()

        if not property_obj:
            return {"success": False, "error": "profile_not_found"}

        match_status = property_obj.match_status

        # 30일 초과 시 초기화
        if match_status in [
            Property.MatchStatusChoice.MATCHED,
            Property.MatchStatusChoice.MY_APPROVED,
            Property.MatchStatusChoice.BOTH_APPROVED,
            Property.MatchStatusChoice.PARTNER_REJECTED,
            Property.MatchStatusChoice.PARTNER_REMATCHED,
        ]:
            match_history = self.history_service.get_by_status(user_id, match_status)
            if match_history and match_history.matched_at:
                days_passed = (timezone.now() - match_history.matched_at).days
                if days_passed > self.MATCH_EXPIRY_DAYS:
                    property_obj.match_status = Property.MatchStatusChoice.NOT_STARTED
                    property_obj.save()
                    match_status = Property.MatchStatusChoice.NOT_STARTED

        return {"success": True, "match_status": match_status}

    # ==================== 대기열 등록 ====================
    def start_matching(self, user_id: int) -> dict:
        property_obj = Property.objects.filter(user_id=user_id).last()
        survey_obj = Survey.objects.filter(user_id=user_id).last()

        if not property_obj or not survey_obj:
            return {"success": False, "error": "prerequisite:profile"}

        if property_obj.match_status != Property.MatchStatusChoice.NOT_STARTED:
            return {
                "success": False,
                "error": "invalid_status",
                "message": "이미 매칭이 진행 중입니다.",
                "match_status": property_obj.match_status
            }

        self.redis_service.register_user(user_id, property_obj, survey_obj)
        property_obj.match_status = Property.MatchStatusChoice.IN_QUEUE
        property_obj.save()

        return {"success": True, "match_status": Property.MatchStatusChoice.IN_QUEUE}

    # ==================== 매칭 취소/거절 ====================
    # status 1: 대기열 취소
    # status 2: 거절
    # status 3: 수락 후 대기 중 취소
    def cancel_matching(self, user_id: int) -> dict:
        property_obj = Property.objects.filter(user_id=user_id).last()

        if not property_obj:
            return {"success": False, "error": "prerequisite:profile"}

        current_status = property_obj.match_status

        allowed_statuses = [
            Property.MatchStatusChoice.IN_QUEUE,
            Property.MatchStatusChoice.MATCHED,
            Property.MatchStatusChoice.MY_APPROVED
        ]

        if current_status not in allowed_statuses:
            return {
                "success": False,
                "error": "invalid_status",
                "message": "현재 상태에서는 취소할 수 없습니다.",
                "match_status": current_status
            }

        # status 1: 대기열에서만 제거
        if current_status == Property.MatchStatusChoice.IN_QUEUE:
            self.redis_service.remove_user(user_id)
            property_obj.match_status = Property.MatchStatusChoice.NOT_STARTED
            property_obj.save()
            return {"success": True, "match_status": Property.MatchStatusChoice.NOT_STARTED}

        # status 2, 3: 트랜잭션으로 처리
        return self._cancel_with_partner(user_id, current_status)

    def _cancel_with_partner(self, user_id: int, expected_status: int) -> dict:
        with transaction.atomic():
            # match_history 먼저 락
            match_history = MatchHistory.objects.select_for_update().filter(
                Q(user_a_id=user_id) | Q(user_b_id=user_id),
                final_match_status=MatchHistory.ResultStatus.PENDING
            ).order_by('-matched_at').first()

            if not match_history:
                return {"success": False, "error": "match_history_not_found"}

            # 이미 FAILED면 상대가 먼저 거절한 것
            if match_history.final_match_status == MatchHistory.ResultStatus.FAILED:
                return {
                    "success": False,
                    "error": "invalid_status",
                    "match_failed": True
                }

            # 두 property를 id 순서대로 락 (데드락 방지)
            user_a_id = match_history.user_a_id
            user_b_id = match_history.user_b_id

            prop_a = Property.objects.select_for_update().filter(user_id=user_a_id).last()
            prop_b = Property.objects.select_for_update().filter(user_id=user_b_id).last()

            my_prop = prop_a if user_id == user_a_id else prop_b
            partner_prop = prop_b if user_id == user_a_id else prop_a

            if not my_prop or not partner_prop:
                return {"success": False, "error": "prerequisite:profile"}

            # 상태 검증
            if my_prop.match_status != expected_status:
                return {
                    "success": False,
                    "error": "invalid_status",
                    "match_status": my_prop.match_status
                }

            # 내 approval 업데이트 및 match_history FAILED 처리
            self.history_service.update_my_approval(
                match_history, user_id, MatchHistory.ApprovalChoice.REJECTED
            )
            match_history.final_match_status = MatchHistory.ResultStatus.FAILED
            match_history.save()

            # 상대방 status를 PARTNER_REJECTED(5)로
            if partner_prop.match_status in [
                Property.MatchStatusChoice.MATCHED,
                Property.MatchStatusChoice.MY_APPROVED
            ]:
                partner_prop.match_status = Property.MatchStatusChoice.PARTNER_REJECTED
                partner_prop.save()

            # 내 상태 초기화
            my_prop.match_status = Property.MatchStatusChoice.NOT_STARTED
            my_prop.save()

            return {"success": True, "match_status": Property.MatchStatusChoice.NOT_STARTED}

    # ==================== 매칭 결과 조회 ====================
    def get_result(self, user_id: int) -> dict:
        property_obj = Property.objects.filter(user_id=user_id).last()

        if not property_obj:
            return {"success": False, "error": "prerequisite:profile"}

        current_status = property_obj.match_status
        allowed_statuses = [
            Property.MatchStatusChoice.MATCHED,
            Property.MatchStatusChoice.MY_APPROVED,
            Property.MatchStatusChoice.PARTNER_REJECTED,
        ]

        if current_status not in allowed_statuses:
            return {
                "success": False,
                "error": "invalid_status",
                "match_status": current_status
            }

        match_history = self.history_service.get_by_status(user_id, current_status)
        if not match_history:
            return {"success": False, "error": "match_history_not_found"}

        partner_prop_id, partner_surv_id = self.history_service.get_partner_profile_ids(
            match_history, user_id
        )
        partner_property = Property.objects.filter(property_id=partner_prop_id).first()
        partner_survey = Survey.objects.filter(survey_id=partner_surv_id).first()

        if not partner_property or not partner_survey:
            return {"success": False, "error": "partner_data_fetch_failed", "retry": True}

        return {
            "success": True,
            "match_status": current_status,
            "match_id": match_history.match_id,
            "compatibility_score": match_history.compatibility_score,
            "partner_property": partner_property,
            "partner_survey": partner_survey
        }

    # ==================== 수락 ====================
    def agree(self, user_id: int) -> dict:
        with transaction.atomic():
            match_history = MatchHistory.objects.select_for_update().filter(
                Q(user_a_id=user_id) | Q(user_b_id=user_id),
                final_match_status=MatchHistory.ResultStatus.PENDING
            ).order_by('-matched_at').first()

            if not match_history:
                return {"success": False, "error": "match_history_not_found"}

            if match_history.final_match_status == MatchHistory.ResultStatus.FAILED:
                return {
                    "success": False,
                    "error": "invalid_status",
                    "match_failed": True
                }

            # 두 property를 id 순서대로 락 (데드락 방지)
            user_a_id = match_history.user_a_id
            user_b_id = match_history.user_b_id

            prop_a = Property.objects.select_for_update().filter(user_id=user_a_id).last()
            prop_b = Property.objects.select_for_update().filter(user_id=user_b_id).last()

            my_prop = prop_a if user_id == user_a_id else prop_b
            partner_prop = prop_b if user_id == user_a_id else prop_a

            if not my_prop or not partner_prop:
                return {"success": False, "error": "prerequisite:profile"}

            if my_prop.match_status != Property.MatchStatusChoice.MATCHED:
                return {
                    "success": False,
                    "error": "invalid_status",
                    "match_status": my_prop.match_status
                }

            # 내 approval 업데이트 및 상대방 확인
            self.history_service.update_my_approval(
                match_history, user_id, MatchHistory.ApprovalChoice.APPROVED
            )
            partner_approval = self.history_service.get_partner_approval(match_history, user_id)

            # property에 반영
            if partner_approval == MatchHistory.ApprovalChoice.APPROVED:
                my_prop.match_status = Property.MatchStatusChoice.BOTH_APPROVED
                if partner_prop:
                    partner_prop.match_status = Property.MatchStatusChoice.BOTH_APPROVED
                    partner_prop.save()

                match_history.final_match_status = MatchHistory.ResultStatus.SUCCESS
                match_history.save()
            else:
                my_prop.match_status = Property.MatchStatusChoice.MY_APPROVED

            my_prop.save()

            return {
                "success": True,
                "match_status": my_prop.match_status
            }

    # ==================== 거절 ====================
    # status 2에서 상대 프로필을 보고 거절 (cancel과 동일)
    def reject(self, user_id: int) -> dict:
        return self.cancel_matching(user_id)

    # ==================== 연락처 조회 ====================
    def get_contact(self, user_id: int) -> dict:
        property_obj = Property.objects.filter(user_id=user_id).last()

        if not property_obj:
            return {"success": False, "error": "prerequisite:profile"}

        current_status = property_obj.match_status
        allowed_statuses = [
            Property.MatchStatusChoice.BOTH_APPROVED,
            Property.MatchStatusChoice.PARTNER_REMATCHED,
        ]

        if current_status not in allowed_statuses:
            return {
                "success": False,
                "error": "invalid_status",
                "match_status": current_status
            }

        match_history = self.history_service.get_by_status(user_id, current_status)
        if not match_history:
            return {"success": False, "error": "match_history_not_found"}

        partner_id = self.history_service.get_partner_id(match_history, user_id)

        # 상대방의 CustomUser 정보 가져오기
        partner_user = CustomUser.objects.filter(user_id=partner_id).first()
        if not partner_user:
            return {"success": False, "error": "partner_data_fetch_failed"}

        # 상대방의 Property 정보 가져오기
        partner_property = Property.objects.filter(user_id=partner_id).last()
        if not partner_property:
            return {"success": False, "error": "partner_data_fetch_failed"}

        # 학번의 3, 4번째 자리만 추출 (예: 2024 -> 24)
        student_id_str = str(partner_user.student_id)
        student_id_display = int(student_id_str[2:4]) if len(student_id_str) >= 4 else partner_user.student_id

        return {
            "success": True,
            "match_status": current_status,
            "partner_name": partner_user.name,
            "partner_phone": partner_user.phone_number,
            "partner_gender": partner_property.gender,
            "partner_student_id": student_id_display,
        }

    # ==================== 재매칭 ====================
    def rematch(self, user_id: int) -> dict:
        # 현재 상태 확인 (락 없이)
        property_obj = Property.objects.filter(user_id=user_id).last()

        if not property_obj:
            return {"success": False, "error": "prerequisite:profile"}

        current_status = property_obj.match_status

        # status 5, 6, 9: 상대방 변경 없이 내 상태만 초기화
        if current_status in [
            Property.MatchStatusChoice.PARTNER_REJECTED,
            Property.MatchStatusChoice.PARTNER_REMATCHED,
            Property.MatchStatusChoice.EXPIRED
        ]:
            property_obj.match_status = Property.MatchStatusChoice.NOT_STARTED
            property_obj.save()
            return {"success": True, "match_status": Property.MatchStatusChoice.NOT_STARTED}

        # status 4: 트랜잭션으로 처리 (상대방 상태 변경 필요)
        if current_status == Property.MatchStatusChoice.BOTH_APPROVED:
            return self._rematch_from_both_approved(user_id)

        return {
            "success": False,
            "error": "invalid_status",
            "match_status": current_status
        }

    def _rematch_from_both_approved(self, user_id: int) -> dict:
        with transaction.atomic():
            # match_history 먼저 락
            match_history = MatchHistory.objects.select_for_update().filter(
                Q(user_a_id=user_id) | Q(user_b_id=user_id),
                final_match_status=MatchHistory.ResultStatus.SUCCESS
            ).order_by('-matched_at').first()

            if not match_history:
                return {"success": False, "error": "match_history_not_found"}

            # 두 property를 id 순서대로 락 (데드락 방지)
            user_a_id = match_history.user_a_id
            user_b_id = match_history.user_b_id

            prop_a = Property.objects.select_for_update().filter(user_id=user_a_id).last()
            prop_b = Property.objects.select_for_update().filter(user_id=user_b_id).last()

            my_prop = prop_a if user_id == user_a_id else prop_b
            partner_prop = prop_b if user_id == user_a_id else prop_a

            if not my_prop:
                return {"success": False, "error": "prerequisite:profile"}

            # 상태 검증
            if my_prop.match_status != Property.MatchStatusChoice.BOTH_APPROVED:
                return {
                    "success": False,
                    "error": "invalid_status",
                    "match_status": my_prop.match_status
                }

            # 상대방 status를 PARTNER_REMATCHED(6)로
            if partner_prop and partner_prop.match_status == Property.MatchStatusChoice.BOTH_APPROVED:
                partner_prop.match_status = Property.MatchStatusChoice.PARTNER_REMATCHED
                partner_prop.save()

            # 내 상태 초기화
            my_prop.match_status = Property.MatchStatusChoice.NOT_STARTED
            my_prop.save()

            return {"success": True, "match_status": Property.MatchStatusChoice.NOT_STARTED}
