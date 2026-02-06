# 연락처 조회 파트 ACCOUNT와 연동 필요

from django.conf import settings as django_settings
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
import redis

from account.decorators import identity_check
from account.models import CustomUser
from .models import Property, Survey
from .serializers import PropertySerializer, SurveySerializer
from .profile_service import InsightService
from .match_service import MatchingService


redis_client = redis.Redis(
    host=django_settings.REDIS_HOST,
    port=int(django_settings.REDIS_PORT),
    password=django_settings.REDIS_PASSWORD or None,
    db=0,
    decode_responses=True,
)


class ProfileViewSet(viewsets.ViewSet):
    """프로필 관련 API (Controller)"""

    PROFILE_STATUS_NO_PROPERTY = 0
    PROFILE_STATUS_NO_SURVEY = 1
    PROFILE_STATUS_COMPLETE = 2

    @identity_check
    def list(self, request):
        user = request.user

        property_obj = Property.objects.filter(user_id=user.user_id).last()
        survey_obj = Survey.objects.filter(user_id=user.user_id).last()

        if not property_obj:
            return Response({
                "success": True,
                "profile_status": self.PROFILE_STATUS_NO_PROPERTY
            }, status=status.HTTP_200_OK)

        if not survey_obj:
            return Response({
                "success": True,
                "profile_status": self.PROFILE_STATUS_NO_SURVEY
            }, status=status.HTTP_200_OK)

        return Response({
            "success": True,
            "profile_status": self.PROFILE_STATUS_COMPLETE,
            "user_id": user.user_id,
            "property": PropertySerializer(property_obj).data,
            "survey": SurveySerializer(survey_obj).data,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get', 'post'], url_path='property')
    @identity_check
    def property_info(self, request):
        user = request.user

        if request.method == 'GET':
            property_obj = Property.objects.filter(user_id=user.user_id).last()
            if property_obj:
                return Response({
                    "success": True,
                    "property": PropertySerializer(property_obj).data
                }, status=status.HTTP_200_OK)
            return Response({
                "success": False,
                "error": "property_not_found"
            }, status=status.HTTP_404_NOT_FOUND)

        if request.method == 'POST':
            serializer = PropertySerializer(data=request.data)
            if serializer.is_valid():
                serializer.save(
                    user_id=user.user_id,
                    nickname=user.nickname,
                    student_id=user.student_id,
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
    @identity_check
    def survey_info(self, request):
        user = request.user

        if request.method == 'GET':
            survey_obj = Survey.objects.filter(user_id=user.user_id).last()
            if survey_obj:
                return Response({
                    "success": True,
                    "survey": SurveySerializer(survey_obj).data
                }, status=status.HTTP_200_OK)
            return Response({
                "success": False,
                "error": "survey_not_found"
            }, status=status.HTTP_404_NOT_FOUND)

        if request.method == 'POST':
            serializer = SurveySerializer(data=request.data)
            if serializer.is_valid():
                service = InsightService(
                    request.data.get('surveys', {}),
                    request.data.get('weights', {})
                )
                score, badge = service.calculate()
                serializer.save(
                    user_id=user.user_id,
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
    """매칭 관련 API (Controller)"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service = MatchingService(redis_client)

    def _get_status_code(self, error: str) -> int:
        error_to_status = {
            "profile_not_found": status.HTTP_404_NOT_FOUND,
            "match_history_not_found": status.HTTP_404_NOT_FOUND,
            "partner_data_fetch_failed": status.HTTP_404_NOT_FOUND,
            "invalid_status": status.HTTP_400_BAD_REQUEST,
        }
        return error_to_status.get(error, status.HTTP_500_INTERNAL_SERVER_ERROR)

    @identity_check
    def list(self, request):
        """GET /matching/ - 현재 매칭 상태 조회"""
        result = self.service.get_status(request.user.user_id)
        status_code = status.HTTP_200_OK if result["success"] else self._get_status_code(result.get("error"))
        return Response(result, status=status_code)

    @action(detail=False, methods=['post'], url_path='start')
    @identity_check
    def start(self, request):
        """POST /matching/start/ - 대기열 등록"""
        result = self.service.start_matching(request.user.user_id)
        status_code = status.HTTP_200_OK if result["success"] else self._get_status_code(result.get("error"))
        return Response(result, status=status_code)

    @action(detail=False, methods=['post'], url_path='cancel')
    @identity_check
    def cancel(self, request):
        """POST /matching/cancel/ - 매칭 취소"""
        result = self.service.cancel_matching(request.user.user_id)
        status_code = status.HTTP_200_OK if result["success"] else self._get_status_code(result.get("error"))
        return Response(result, status=status_code)

    @action(detail=False, methods=['get'], url_path='result')
    @identity_check
    def result(self, request):
        """GET /matching/result/ - 매칭 결과 상세 조회"""
        result = self.service.get_result(request.user.user_id)

        if not result["success"]:
            return Response(result, status=self._get_status_code(result.get("error")))

        return Response({
            "success": True,
            "match_status": result["match_status"],
            "match_id": result["match_id"],
            "compatibility_score": result["compatibility_score"],
            "partner": {
                "property": PropertySerializer(result["partner_property"]).data,
                "survey": SurveySerializer(result["partner_survey"]).data
            }
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='agree')
    @identity_check
    def agree(self, request):
        """POST /matching/agree/ - 매칭 수락"""
        result = self.service.agree(request.user.user_id)
        status_code = status.HTTP_200_OK if result["success"] else self._get_status_code(result.get("error"))
        return Response(result, status=status_code)

    @action(detail=False, methods=['post'], url_path='reject')
    @identity_check
    def reject(self, request):
        """POST /matching/reject/ - 매칭 거절"""
        result = self.service.reject(request.user.user_id)
        status_code = status.HTTP_200_OK if result["success"] else self._get_status_code(result.get("error"))
        return Response(result, status=status_code)

    # ACCOUNT와 연동 필요
    @action(detail=False, methods=['get'], url_path='contact')
    @identity_check
    def contact(self, request):
        """GET /matching/contact/ - 상대방 연락처 조회"""
        result = self.service.get_contact(request.user.user_id)

        if not result["success"]:
            return Response(result, status=self._get_status_code(result.get("error")))

        return Response({
            "success": True,
            "match_status": result["match_status"],
            "partner": {
                "user_id": result["partner_id"],
                "nickname": result["partner_nickname"],
            }
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='rematch')
    @identity_check
    def rematch(self, request):
        """POST /matching/rematch/ - 재매칭 요청"""
        result = self.service.rematch(request.user.user_id)
        status_code = status.HTTP_200_OK if result["success"] else self._get_status_code(result.get("error"))
        return Response(result, status=status_code)
