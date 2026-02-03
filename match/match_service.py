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

from .models import Property, Survey, MatchHistory


class RedisQueueService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def register_user(self, user_pk: int, property_obj: Property, survey_obj: Survey):
        queue_data = {
            "user_pk": user_pk,
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

        redis_key = f"match:user-queue:{user_pk}"
        self.redis.set(redis_key, json.dumps(queue_data))

    def remove_user(self, user_pk: int):
        redis_key = f"match:user-queue:{user_pk}"
        self.redis.delete(redis_key)


class MatchHistoryService:
    @staticmethod
    def get_by_status(user_pk: int, match_status: int) -> MatchHistory | None:
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
            Q(user_a_pk=user_pk) | Q(user_b_pk=user_pk),
            final_match_status=result_status
        ).order_by('-matched_at').first()

    @staticmethod
    def get_partner_pk(match_history: MatchHistory, my_pk: int) -> int:
        if match_history.user_a_pk == my_pk:
            return match_history.user_b_pk
        return match_history.user_a_pk

    @staticmethod
    def get_partner_ids(match_history: MatchHistory, my_pk: int) -> tuple[int, int]:
        if match_history.user_a_pk == my_pk:
            return match_history.prop_b_id, match_history.surv_b_id
        return match_history.prop_a_id, match_history.surv_a_id

    @staticmethod
    def update_my_approval(match_history: MatchHistory, my_pk: int, approval_status: int):
        if match_history.user_a_pk == my_pk:
            match_history.a_approval = approval_status
        else:
            match_history.b_approval = approval_status
        match_history.save()

    @staticmethod
    def get_partner_approval(match_history: MatchHistory, my_pk: int) -> int:
        if match_history.user_a_pk == my_pk:
            return match_history.b_approval
        return match_history.a_approval


class MatchingService:
    MATCH_EXPIRY_DAYS = 30

    def __init__(self, redis_client: redis.Redis):
        self.redis_service = RedisQueueService(redis_client)
        self.history_service = MatchHistoryService()

    # ==================== 상태 조회 ====================
    def get_status(self, user_pk: int) -> dict:
        property_obj = Property.objects.filter(user_pk=user_pk).last()

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
            match_history = self.history_service.get_by_status(user_pk, match_status)
            if match_history and match_history.matched_at:
                days_passed = (timezone.now() - match_history.matched_at).days
                if days_passed > self.MATCH_EXPIRY_DAYS:
                    property_obj.match_status = Property.MatchStatusChoice.NOT_STARTED
                    property_obj.save()
                    match_status = Property.MatchStatusChoice.NOT_STARTED

        return {"success": True, "match_status": match_status}

    # ==================== 대기열 등록 ====================
    def start_matching(self, user_pk: int) -> dict:
        property_obj = Property.objects.filter(user_pk=user_pk).last()
        survey_obj = Survey.objects.filter(user_pk=user_pk).last()

        if not property_obj or not survey_obj:
            return {"success": False, "error": "profile_not_found"}

        if property_obj.match_status != Property.MatchStatusChoice.NOT_STARTED:
            return {
                "success": False,
                "error": "invalid_status",
                "message": "이미 매칭이 진행 중입니다.",
                "match_status": property_obj.match_status
            }

        self.redis_service.register_user(user_pk, property_obj, survey_obj)
        property_obj.match_status = Property.MatchStatusChoice.IN_QUEUE
        property_obj.save()

        return {"success": True, "match_status": Property.MatchStatusChoice.IN_QUEUE}

    # ==================== 매칭 취소 ====================
    def cancel_matching(self, user_pk: int) -> dict:
        property_obj = Property.objects.filter(user_pk=user_pk).last()

        if not property_obj:
            return {"success": False, "error": "profile_not_found"}

        current_status = property_obj.match_status
        allowed_statuses = [
            Property.MatchStatusChoice.IN_QUEUE,
            Property.MatchStatusChoice.MATCHED,
            Property.MatchStatusChoice.MY_APPROVED,
        ]

        if current_status not in allowed_statuses:
            return {
                "success": False,
                "error": "invalid_status",
                "message": "현재 상태에서는 취소할 수 없습니다.",
                "match_status": current_status
            }

        # status 1
        if current_status == Property.MatchStatusChoice.IN_QUEUE:
            self.redis_service.remove_user(user_pk)

        # status 2, 3
        if current_status in [Property.MatchStatusChoice.MATCHED, Property.MatchStatusChoice.MY_APPROVED]:
            match_history = self.history_service.get_by_status(user_pk, current_status)
            if not match_history:
                return {"success": False, "error": "match_history_not_found"}
            self._handle_cancel_with_partner(user_pk, match_history)

        property_obj.match_status = Property.MatchStatusChoice.NOT_STARTED
        property_obj.save()

        return {"success": True, "match_status": Property.MatchStatusChoice.NOT_STARTED}

    def _handle_cancel_with_partner(self, user_pk: int, match_history: MatchHistory):
        self.history_service.update_my_approval(
            match_history, user_pk, MatchHistory.ApprovalChoice.REJECTED
        )
        match_history.final_match_status = MatchHistory.ResultStatus.FAILED
        match_history.save()

        partner_pk = self.history_service.get_partner_pk(match_history, user_pk)
        partner_property = Property.objects.filter(user_pk=partner_pk).last()

        if partner_property and partner_property.match_status in [
            Property.MatchStatusChoice.MATCHED,
            Property.MatchStatusChoice.MY_APPROVED
        ]:
            partner_property.match_status = Property.MatchStatusChoice.PARTNER_REJECTED
            partner_property.save()

    # ==================== 매칭 결과 조회 ====================
    def get_result(self, user_pk: int) -> dict:
        property_obj = Property.objects.filter(user_pk=user_pk).last()

        if not property_obj:
            return {"success": False, "error": "profile_not_found"}

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

        match_history = self.history_service.get_by_status(user_pk, current_status)
        if not match_history:
            return {"success": False, "error": "match_history_not_found"}

        partner_prop_id, partner_surv_id = self.history_service.get_partner_ids(
            match_history, user_pk
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
    def agree(self, user_pk: int) -> dict:
        with transaction.atomic():
            property_obj = Property.objects.select_for_update().filter(
                user_pk=user_pk
            ).last()

            if not property_obj:
                return {"success": False, "error": "profile_not_found"}

            current_status = property_obj.match_status

            if current_status != Property.MatchStatusChoice.MATCHED:
                return {
                    "success": False,
                    "error": "invalid_status",
                    "match_status": current_status
                }

            match_history = self.history_service.get_by_status(user_pk, current_status)
            if not match_history:
                return {"success": False, "error": "match_history_not_found"}

            self.history_service.update_my_approval(
                match_history, user_pk, MatchHistory.ApprovalChoice.APPROVED
            )

            partner_approval = self.history_service.get_partner_approval(match_history, user_pk)

            if partner_approval == MatchHistory.ApprovalChoice.APPROVED:
                # 상대도 수락 → 둘 다 status 4로
                partner_pk = self.history_service.get_partner_pk(match_history, user_pk)
                partner_property = Property.objects.select_for_update().filter(
                    user_pk=partner_pk
                ).last()

                property_obj.match_status = Property.MatchStatusChoice.BOTH_APPROVED
                if partner_property:
                    partner_property.match_status = Property.MatchStatusChoice.BOTH_APPROVED
                    partner_property.save()

                match_history.final_match_status = MatchHistory.ResultStatus.SUCCESS
                match_history.save()
            else:
                # 상대는 아직 대기 중 → 내 status 3으로
                property_obj.match_status = Property.MatchStatusChoice.MY_APPROVED

            property_obj.save()

            return {
                "success": True,
                "match_status": property_obj.match_status
            }

    # ==================== 거절 ====================
    def reject(self, user_pk: int) -> dict:
        with transaction.atomic():
            property_obj = Property.objects.select_for_update().filter(
                user_pk=user_pk
            ).last()

            if not property_obj:
                return {"success": False, "error": "profile_not_found"}

            current_status = property_obj.match_status
            allowed_statuses = [
                Property.MatchStatusChoice.MATCHED,
                Property.MatchStatusChoice.MY_APPROVED,
            ]

            if current_status not in allowed_statuses:
                return {
                    "success": False,
                    "error": "invalid_status",
                    "match_status": current_status
                }

            match_history = self.history_service.get_by_status(user_pk, current_status)
            if not match_history:
                return {"success": False, "error": "match_history_not_found"}

            self.history_service.update_my_approval(
                match_history, user_pk, MatchHistory.ApprovalChoice.REJECTED
            )
            match_history.final_match_status = MatchHistory.ResultStatus.FAILED
            match_history.save()

            # 상대방 status를 PARTNER_REJECTED(5)로
            partner_pk = self.history_service.get_partner_pk(match_history, user_pk)
            partner_property = Property.objects.select_for_update().filter(
                user_pk=partner_pk
            ).last()

            if partner_property and partner_property.match_status in [
                Property.MatchStatusChoice.MATCHED,
                Property.MatchStatusChoice.MY_APPROVED
            ]:
                partner_property.match_status = Property.MatchStatusChoice.PARTNER_REJECTED
                partner_property.save()

            # 내 상태 초기화
            property_obj.match_status = Property.MatchStatusChoice.NOT_STARTED
            property_obj.save()

            return {
                "success": True,
                "match_status": Property.MatchStatusChoice.NOT_STARTED
            }

    # ==================== 연락처 조회 ====================
    # Account 완료시 연동 필요 !!!
    def get_contact(self, user_pk: int) -> dict:
        property_obj = Property.objects.filter(user_pk=user_pk).last()

        if not property_obj:
            return {"success": False, "error": "profile_not_found"}

        current_status = property_obj.match_status
        allowed_statuses = [
            Property.MatchStatusChoice.BOTH_APPROVED,
            Property.MatchStatusChoice.PARTNER_REJECTED,
            Property.MatchStatusChoice.PARTNER_REMATCHED,
        ]

        if current_status not in allowed_statuses:
            return {
                "success": False,
                "error": "invalid_status",
                "match_status": current_status
            }

        match_history = self.history_service.get_by_status(user_pk, current_status)
        if not match_history:
            return {"success": False, "error": "match_history_not_found"}

        partner_pk = self.history_service.get_partner_pk(match_history, user_pk)
        partner_property = Property.objects.filter(user_pk=partner_pk).last()

        return {
            "success": True,
            "match_status": current_status,
            "partner_pk": partner_pk,
            "partner_nickname": partner_property.nickname if partner_property else None
        }

    # ==================== 재매칭 ====================
    def rematch(self, user_pk: int) -> dict:
        with transaction.atomic():
            property_obj = Property.objects.select_for_update().filter(
                user_pk=user_pk
            ).last()

            if not property_obj:
                return {"success": False, "error": "profile_not_found"}

            current_status = property_obj.match_status
            allowed_statuses = [
                Property.MatchStatusChoice.BOTH_APPROVED,
                Property.MatchStatusChoice.PARTNER_REJECTED,
                Property.MatchStatusChoice.PARTNER_REMATCHED,
            ]

            if current_status not in allowed_statuses:
                return {
                    "success": False,
                    "error": "invalid_status",
                    "match_status": current_status
                }

            # status 4: 상대방 status를 6으로 변경
            if current_status == Property.MatchStatusChoice.BOTH_APPROVED:
                match_history = self.history_service.get_by_status(user_pk, current_status)
                if match_history:
                    partner_pk = self.history_service.get_partner_pk(match_history, user_pk)
                    partner_property = Property.objects.select_for_update().filter(
                        user_pk=partner_pk
                    ).last()

                    if partner_property and partner_property.match_status == Property.MatchStatusChoice.BOTH_APPROVED:
                        partner_property.match_status = Property.MatchStatusChoice.PARTNER_REMATCHED
                        partner_property.save()

            property_obj.match_status = Property.MatchStatusChoice.NOT_STARTED
            property_obj.save()

            return {
                "success": True,
                "match_status": Property.MatchStatusChoice.NOT_STARTED
            }
