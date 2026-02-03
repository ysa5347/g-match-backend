from rest_framework import serializers
from .models import Property, Survey, MatchHistory


class PropertySerializer(serializers.ModelSerializer):
    class Meta:
        model = Property
        fields = '__all__'
        read_only_fields = ['property_id', 'created_at', 'user_id', "nickname", "student_id", "gender", 'match_status']

    def validate_dorm_building(self, value):
        allowed_buildings = ['G', 'I', 'S', 'T']
        if value not in allowed_buildings:
            raise serializers.ValidationError(
                f"기숙사 동은 {', '.join(allowed_buildings)} 중 하나여야 합니다."
            )
        return value

    def validate_stay_period(self, value):
        """입주 기간 검증"""
        if value not in [1, 2, 3]:
            raise serializers.ValidationError("최소 입주 기간은 1학기에서 3학기 사이여야 합니다.")
        return value

class SurveySerializer(serializers.ModelSerializer):
    REQUIRED_KEYS = {
        'time_1', 'time_2', 'time_3', 'time_4',
        'clean_1', 'clean_2', 'clean_3', 'clean_4',
        'habit_1', 'habit_2', 'habit_3', 'habit_4',
        'social_1', 'social_2', 'social_3', 'social_4', 'social_5',
        'etc_1', 'etc_2'
    }

    class Meta:
        model = Survey
        fields = '__all__'
        read_only_fields = ['survey_id', 'created_at', 'user_id', 'scores', 'badges']

    def validate_surveys(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("surveys는 객체 형식이어야 합니다.")

        input_keys = set(value.keys())
        missing_keys = self.REQUIRED_KEYS - input_keys
        if missing_keys:
            raise serializers.ValidationError(f"다음 필수 설문 항목이 누락되었습니다: {missing_keys}")

        for key, answer in value.items():
            if not isinstance(answer, int):
                raise serializers.ValidationError(
                    f"{key}: 정수형이어야 합니다."
                )
            if not (1 <= answer <= 5):
                raise serializers.ValidationError(
                    f"{key}: 1-5 사이 값이어야 합니다. (입력: {answer})"
                )
        return value

    def validate_weights(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("weights는 객체 형식이어야 합니다.")

        input_keys = set(value.keys())
        missing_keys = self.REQUIRED_KEYS - input_keys
        if missing_keys:
            raise serializers.ValidationError(f"다음 항목에 대한 가중치가 누락되었습니다: {missing_keys}")

        allowed_weights = [0.5, 1.0, 1.5]
        for key, weight in value.items():
            if weight not in allowed_weights:
                raise serializers.ValidationError(
                    f"{key}: 가중치는 0.5, 1.0, 1.5 중 하나여야 합니다. (입력: {weight})"
                )
        return value

    def validate(self, data):
        """surveys와 weights 키 일치 검증"""
        surveys = data.get('surveys', {})
        weights = data.get('weights', {})

        if set(surveys.keys()) != set(weights.keys()):
            raise serializers.ValidationError(
                "surveys와 weights의 질문 항목이 일치해야 합니다."
            )
        return data

