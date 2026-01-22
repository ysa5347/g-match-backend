from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import CustomUser, Agreement


@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    list_display = ['email', 'name', 'uid', 'is_active', 'is_staff', 'date_joined']
    list_filter = ['is_active', 'is_staff', 'gender', 'house']
    search_fields = ['email', 'name', 'student_id']
    ordering = ['-date_joined']

    fieldsets = (
        ('Authentication', {
            'fields': ('email', 'password')
        }),
        ('Personal Info', {
            'fields': ('name', 'student_id', 'phone_number', 'birth_year', 'gender', 'house')
        }),
        ('Privacy Settings', {
            'fields': ('is_age_public', 'is_house_public')
        }),
        ('OAuth', {
            'fields': ('kakao_id', 'naver_id')
        }),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')
        }),
        ('Important Dates', {
            'fields': ('last_login', 'date_joined')
        }),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'name', 'password1', 'password2'),
        }),
    )

    readonly_fields = ['uid', 'date_joined', 'last_login']


@admin.register(Agreement)
class AgreementAdmin(admin.ModelAdmin):
    list_display = ['user', 'terms_of_service', 'privacy_policy', 'agreed_at']
    list_filter = ['terms_of_service', 'privacy_policy']
    search_fields = ['user__email', 'user__name']
    readonly_fields = ['agreed_at']
