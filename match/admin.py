from django.contrib import admin
from .models import Property, Survey, MatchHistory


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ['property_id', 'user_id', 'nickname', 'gender', 'match_status', 'created_at']
    list_filter = ['match_status', 'gender', 'dorm_building']
    search_fields = ['user_id', 'nickname']
    ordering = ['-created_at']


@admin.register(Survey)
class SurveyAdmin(admin.ModelAdmin):
    list_display = ['survey_id', 'user_id', 'created_at']
    search_fields = ['user_id']
    ordering = ['-created_at']


@admin.register(MatchHistory)
class MatchHistoryAdmin(admin.ModelAdmin):
    list_display = ['match_id', 'user_a_id', 'user_b_id', 'final_match_status', 'a_approval', 'b_approval', 'matched_at']
    list_filter = ['final_match_status', 'a_approval', 'b_approval']
    search_fields = ['user_a_id', 'user_b_id']
    ordering = ['-matched_at']
