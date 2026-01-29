from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from datetime import timedelta
import redis
import json

from django.db.models import Q
from .models import Property, Survey, MatchHistory
from .serializers import PropertySerializer, SurveySerializer
from .profile_service import InsightService

# Redis 연결
redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

class ProfileViewSet(viewsets.ViewSet):
    # ===== 테스트 환경 설정 =====
    # permission_classes = [permissions.IsAuthenticated]
    permission_classes = [permissions.AllowAny]

    # 프로필 상태 상수
    PROFILE_STATUS_NO_PROPERTY = 0
    PROFILE_STATUS_NO_SURVEY = 1
    PROFILE_STATUS_COMPLETE = 2

    PROFILE_SCREEN_MAP = {
        0: "need_property",
        1: "need_survey",
        2: "complete",
    }

    def list(self, request):
        """
        GET /profile/
        프로필 상태 조회 (화면 결정용)
        """
        user = request.user

        property_obj = Property.objects.filter(user_pk=user.user_pk).last()
        survey_obj = Survey.objects.filter(user_pk=user.user_pk).last()

        if not property_obj:
            return Response({
                "success": True,
                "profile_status": self.PROFILE_STATUS_NO_PROPERTY,
                "screen": self.PROFILE_SCREEN_MAP[self.PROFILE_STATUS_NO_PROPERTY]
            }, status=status.HTTP_200_OK)

        if not survey_obj:
            return Response({
                "success": True,
                "profile_status": self.PROFILE_STATUS_NO_SURVEY,
                "screen": self.PROFILE_SCREEN_MAP[self.PROFILE_STATUS_NO_SURVEY]
            }, status=status.HTTP_200_OK)

        return Response({
            "success": True,
            "profile_status": self.PROFILE_STATUS_COMPLETE,
            "screen": self.PROFILE_SCREEN_MAP[self.PROFILE_STATUS_COMPLETE],
            "user_pk": user.user_pk,
            "property": PropertySerializer(property_obj).data,
            "survey": SurveySerializer(survey_obj).data,
        }, status=status.HTTP_200_OK)


    @action(detail=False, methods=['get', 'post'], url_path='property')
    def property_info(self, request):
        user = request.user

        if request.method == 'GET':
            property_obj = Property.objects.filter(user_pk=user.user_pk).last()
            if property_obj:
                return Response({
                    "success": True,
                    "property": PropertySerializer(property_obj).data
                }, status=status.HTTP_200_OK)
            else:
                return Response({
                    "success": False,
                    "error": "property_not_found"
                }, status=status.HTTP_404_NOT_FOUND)

        if request.method == 'POST':
            serializer = PropertySerializer(data=request.data)

            if serializer.is_valid():
                serializer.save(
                    user_pk=user.user_pk,
                    nickname=user.nickname,
                    department=user.department,
                    gender=user.gender
                )
                return Response({
                    "success": True,
                    "message": "Property was created successfully"
                }, status=status.HTTP_201_CREATED)

            return Response({
                "success": False,
                "error": "validation_failed",
                "details": serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get', 'post'], url_path='survey')
    def survey_info(self, request):
        user = request.user

        if request.method == 'GET':
            survey_obj = Survey.objects.filter(user_pk=user.user_pk).last()

            if survey_obj:
                return Response({
                    "success": True,
                    "survey": SurveySerializer(survey_obj).data
                }, status=status.HTTP_200_OK)
            else:
                return Response({
                    "success": False,
                    "error": "survey_not_found"
                }, status=status.HTTP_404_NOT_FOUND)

        if request.method == 'POST':
            serializer = SurveySerializer(data=request.data)
            if serializer.is_valid():
                service = InsightService(request.data.get('surveys', {}), request.data.get('weights', {}))
                score, badge = service.calculate()
                serializer.save(
                    user_pk=user.user_pk,
                    scores=score,
                    badges=badge
                )
                return Response({
                    "success": True,
                    "message": "Survey was created successfully"
                }, status=status.HTTP_201_CREATED)

            return Response({
                "success": False,
                "error": "validation_failed",
                "details": serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)


class MatchingViewSet(viewsets.ViewSet):
    """
    매칭 관련 API
    - GET  /matching/          : 현재 매칭 상태 조회
    - POST /matching/start/    : 대기열 등록
    - POST /matching/cancel/   : 매칭 취소
    - GET  /matching/result/   : 매칭 결과 상세 조회
    """
    # TODO: 프로덕션에서는 IsAuthenticated로 변경
    # permission_classes = [permissions.IsAuthenticated]
    permission_classes = [permissions.AllowAny]

    # 화면 매핑 (MatchStatusChoice와 일관성 유지)
    SCREEN_MAP = {
        Property.MatchStatusChoice.NOT_STARTED: "initial",
        Property.MatchStatusChoice.IN_QUEUE: "waiting_queue",
        Property.MatchStatusChoice.MATCHED: "show_result",
        Property.MatchStatusChoice.MY_APPROVED: "waiting_partner",
        Property.MatchStatusChoice.BOTH_APPROVED: "contact_exchange",
        Property.MatchStatusChoice.PARTNER_REJECTED: "partner_rejected",
        Property.MatchStatusChoice.PARTNER_CANCELLED: "partner_cancelled",
    }

    # ==================== 상태 조회 ====================
    def list(self, request):
        """
        GET /matching/
        현재 매칭 상태 조회 (화면 결정용)
        """
        user = request.user
        property_obj = Property.objects.filter(user_pk=user.user_pk).last()

        if not property_obj:
            return Response({
                "success": False,
                "error": "profile_not_found"
            }, status=status.HTTP_404_NOT_FOUND)

        match_status = property_obj.match_status

        # 30일 초과 시 초기화 (status 2~6 모두 해당)
        if match_status in [
            Property.MatchStatusChoice.MATCHED,
            Property.MatchStatusChoice.MY_APPROVED,
            Property.MatchStatusChoice.BOTH_APPROVED,
            Property.MatchStatusChoice.PARTNER_REJECTED,
            Property.MatchStatusChoice.PARTNER_CANCELLED,
        ]:
            match_history = self._get_match_history_by_status(user.user_pk, match_status)
            if match_history and match_history.matched_at:
                days_passed = (timezone.now() - match_history.matched_at).days
                if days_passed > 30:
                    property_obj.match_status = Property.MatchStatusChoice.NOT_STARTED
                    property_obj.save()
                    match_status = Property.MatchStatusChoice.NOT_STARTED

        return Response({
            "success": True,
            "match_status": match_status,
            "screen": self.SCREEN_MAP.get(match_status, "initial")
        }, status=status.HTTP_200_OK)

    # ==================== 대기열 등록 ====================
    @action(detail=False, methods=['post'], url_path='start')
    def start(self, request):
        """
        POST /matching/start/
        대기열 등록 (status 0 → 1)
        """
        user = request.user

        # Property, Survey 존재 확인
        property_obj = Property.objects.filter(user_pk=user.user_pk).last()
        survey_obj = Survey.objects.filter(user_pk=user.user_pk).last()

        if not property_obj or not survey_obj:
            return Response({
                "success": False,
                "error": "profile_incomplete"
            }, status=status.HTTP_400_BAD_REQUEST)

        # 현재 상태 확인
        if property_obj.match_status != Property.MatchStatusChoice.NOT_STARTED:
            return Response({
                "success": False,
                "error": "invalid_status",
                "message": "이미 매칭이 진행 중입니다.",
                "match_status": property_obj.match_status
            }, status=status.HTTP_400_BAD_REQUEST)

        # Redis 대기열 등록
        queue_data = {
            "user_pk": user.user_pk,
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
            "priority": 0,  # 초기 우선순위
            "registered_at": timezone.now().isoformat()
        }

        redis_key = f"match:user-queue:{user.user_pk}"
        redis_client.set(redis_key, json.dumps(queue_data))

        # 상태 변경
        property_obj.match_status = Property.MatchStatusChoice.IN_QUEUE
        property_obj.save()

        return Response({
            "success": True,
            "match_status": Property.MatchStatusChoice.IN_QUEUE
        }, status=status.HTTP_200_OK)

    # ==================== 매칭 취소 ====================
    @action(detail=False, methods=['post'], url_path='cancel')
    def cancel(self, request):
        """
        POST /matching/cancel/
        매칭 취소 (status 1, 2, 3 → 0)
        """
        user = request.user
        property_obj = Property.objects.filter(user_pk=user.user_pk).last()

        if not property_obj:
            return Response({
                "success": False,
                "error": "profile_not_found"
            }, status=status.HTTP_404_NOT_FOUND)

        current_status = property_obj.match_status
        allowed_statuses = [
            Property.MatchStatusChoice.IN_QUEUE,
            Property.MatchStatusChoice.MATCHED,
            Property.MatchStatusChoice.MY_APPROVED,
        ]

        if current_status not in allowed_statuses:
            return Response({
                "success": False,
                "error": "invalid_status",
                "message": "현재 상태에서는 취소할 수 없습니다.",
                "match_status": current_status
            }, status=status.HTTP_400_BAD_REQUEST)

        # status 1인 경우: Redis 대기열에서 제거
        if current_status == Property.MatchStatusChoice.IN_QUEUE:
            redis_key = f"match:user-queue:{user.user_pk}"
            redis_client.delete(redis_key)

        # status 2, 3인 경우: 상대방 처리 + MatchHistory 업데이트
        if current_status in [Property.MatchStatusChoice.MATCHED, Property.MatchStatusChoice.MY_APPROVED]:
            match_history = self._get_pending_match_history(user.user_pk)
            if match_history:
                # 내 approval을 REJECTED로
                self._update_my_approval(match_history, user.user_pk, MatchHistory.ApprovalChoice.REJECTED)
                match_history.final_match_status = MatchHistory.ResultStatus.FAILED
                match_history.save()

                # 상대방 status를 PARTNER_REJECTED(5)로
                partner_pk = self._get_partner_pk(match_history, user.user_pk)
                partner_property = Property.objects.filter(user_pk=partner_pk).last()
                if partner_property and partner_property.match_status in [
                    Property.MatchStatusChoice.MATCHED,
                    Property.MatchStatusChoice.MY_APPROVED
                ]:
                    partner_property.match_status = Property.MatchStatusChoice.PARTNER_REJECTED
                    partner_property.save()

        # 내 상태 초기화
        property_obj.match_status = Property.MatchStatusChoice.NOT_STARTED
        property_obj.save()

        return Response({
            "success": True,
            "match_status": Property.MatchStatusChoice.NOT_STARTED
        }, status=status.HTTP_200_OK)

    # ==================== 매칭 결과 조회 ====================
    @action(detail=False, methods=['get'], url_path='result')
    def result(self, request):
        """
        GET /matching/result/
        매칭 결과 상세 조회 (status 2, 3일 때)
        """
        user = request.user
        property_obj = Property.objects.filter(user_pk=user.user_pk).last()

        if not property_obj:
            return Response({
                "success": False,
                "error": "profile_not_found"
            }, status=status.HTTP_404_NOT_FOUND)

        # 상태 확인
        allowed_statuses = [
            Property.MatchStatusChoice.MATCHED,
            Property.MatchStatusChoice.MY_APPROVED,
        ]
        if property_obj.match_status not in allowed_statuses:
            return Response({
                "success": False,
                "error": "no_match_result",
                "match_status": property_obj.match_status
            }, status=status.HTTP_400_BAD_REQUEST)

        # MatchHistory에서 현재 매칭 조회
        match_history = self._get_pending_match_history(user.user_pk)
        if not match_history:
            return Response({
                "success": False,
                "error": "match_history_not_found"
            }, status=status.HTTP_404_NOT_FOUND)

        # 상대방 정보 조회
        partner_prop_id = match_history.prop_b_id if match_history.user_a_pk == user.user_pk else match_history.prop_a_id
        partner_surv_id = match_history.surv_b_id if match_history.user_a_pk == user.user_pk else match_history.surv_a_id

        partner_property = Property.objects.filter(property_id=partner_prop_id).first()
        partner_survey = Survey.objects.filter(survey_id=partner_surv_id).first()

        if not partner_property or not partner_survey:
            return Response({
                "success": False,
                "error": "partner_data_fetch_failed",
                "retry": True
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({
            "success": True,
            "match_id": match_history.match_id,
            "compatibility_score": match_history.compatibility_score,
            "partner": {
                "property": PropertySerializer(partner_property).data,
                "survey": SurveySerializer(partner_survey).data
            }
        }, status=status.HTTP_200_OK)

    # ==================== Helper Methods ====================
    def _get_match_history_by_status(self, user_pk, match_status):
        """
        match_status에 따라 적절한 MatchHistory 조회
        - status 2, 3: PENDING
        - status 4: SUCCESS
        - status 5, 6: FAILED
        """
        status_to_result = {
            Property.MatchStatusChoice.MATCHED: MatchHistory.ResultStatus.PENDING,
            Property.MatchStatusChoice.MY_APPROVED: MatchHistory.ResultStatus.PENDING,
            Property.MatchStatusChoice.BOTH_APPROVED: MatchHistory.ResultStatus.SUCCESS,
            Property.MatchStatusChoice.PARTNER_REJECTED: MatchHistory.ResultStatus.FAILED,
            Property.MatchStatusChoice.PARTNER_CANCELLED: MatchHistory.ResultStatus.FAILED,
        }

        result_status = status_to_result.get(match_status)
        if result_status is None:
            return None

        return MatchHistory.objects.filter(
            Q(user_a_pk=user_pk) | Q(user_b_pk=user_pk),
            final_match_status=result_status
        ).order_by('-matched_at').first()

    def _get_pending_match_history(self, user_pk):
        """현재 진행 중인 (PENDING) MatchHistory 조회"""
        return MatchHistory.objects.filter(
            Q(user_a_pk=user_pk) | Q(user_b_pk=user_pk),
            final_match_status=MatchHistory.ResultStatus.PENDING
        ).order_by('-matched_at').first()

    def _get_partner_pk(self, match_history, my_pk):
        """MatchHistory에서 상대방 pk 반환"""
        if match_history.user_a_pk == my_pk:
            return match_history.user_b_pk
        return match_history.user_a_pk

    def _update_my_approval(self, match_history, my_pk, approval_status):
        """내 approval 상태 업데이트"""
        if match_history.user_a_pk == my_pk:
            match_history.a_approval = approval_status
        else:
            match_history.b_approval = approval_status
        match_history.save()

