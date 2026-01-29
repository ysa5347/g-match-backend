from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Property, Survey
from .serializers import PropertySerializer, SurveySerializer
from .profile_service import InsightService

class ProfileViewSet(viewsets.ViewSet):
    # ===== 테스트 환경 설정 =====
    # permission_classes = [permissions.IsAuthenticated]
    permission_classes = [permissions.AllowAny]

    def list(self, request):
        user = request.user  # 프로덕션용

        property_obj = Property.objects.filter(user_pk=user.user_pk).last()
        survey_obj = Survey.objects.filter(user_pk=user.user_pk).last()

        if not property_obj:
            return Response({
                "message": "Profile does not exist",
                "need": "Property"
            }, status=status.HTTP_404_NOT_FOUND)
        elif not survey_obj:
            return Response({
                "message": "Profile does not exist",
                "need": "Survey"
            }, status=status.HTTP_404_NOT_FOUND)

        return Response({
            "user_pk": user.user_pk,
            "profile": PropertySerializer(property_obj).data,
            "survey": SurveySerializer(survey_obj).data,
        }, status=status.HTTP_200_OK)


    @action(detail=False, methods=['get', 'post'], url_path='property')
    def property_info(self, request):
        user = request.user

        if request.method == 'GET':
            property_obj = Property.objects.filter(user_pk=user.user_pk).last()
            if property_obj:
                serializer = PropertySerializer(property_obj)
                return Response(serializer.data, status=status.HTTP_200_OK)
            else:
                return Response({"message": "Property does not exist"}, status=status.HTTP_404_NOT_FOUND)

        if request.method == 'POST':
            serializer = PropertySerializer(data=request.data)

            if serializer.is_valid():
                serializer.save(
                    user_pk=user.user_pk,
                    nickname=user.nickname,
                    department=user.department,
                    gender=user.gender
                    )
                return Response({"message": "Property was created successfully"}, status=status.HTTP_201_CREATED)

            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get', 'post'], url_path='survey')
    def survey_info(self, request):
        user = request.user

        if request.method == 'GET':
            survey_obj = Survey.objects.filter(user_pk=user.user_pk).last()

            if survey_obj:
                serializer = SurveySerializer(survey_obj)
                return Response(serializer.data, status=status.HTTP_200_OK)
            else:
                return Response({"message": "Survey does not exist"}, status=status.HTTP_404_NOT_FOUND)

        if request.method == 'POST':
            serializer = SurveySerializer(data=request.data)
            if serializer.is_valid():
                service = InsightService(request.data.get('surveys', {}), request.data.get('weights', {}))
                score, badge = service.calculate()
                serializer.save(
                    user_pk=user.user_pk,
                    scores=score,
                    badges=badge)
                return Response({"message": "Survey was created successfully"}, status=status.HTTP_201_CREATED)

            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


