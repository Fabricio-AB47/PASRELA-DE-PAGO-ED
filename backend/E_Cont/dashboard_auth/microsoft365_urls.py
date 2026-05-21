from django.urls import path

from .views import microsoft365_create_user_view, microsoft365_student_license_view


urlpatterns = [
    path('users/', microsoft365_create_user_view, name='microsoft365-create-user-direct'),
    path('licenses/student/', microsoft365_student_license_view, name='microsoft365-student-license-direct'),
]
