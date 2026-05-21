from django.urls import path

from .views import (
    admin_payment_cancel_view,
    admin_payment_info_view,
    health_view,
    inscription_catalogs_view,
    inscription_generate_matricula_view,
    inscription_payment_link_view,
    login_view,
    student_lookup_view,
)


urlpatterns = [
    path('health/', health_view, name='auth-health'),
    path('login/', login_view, name='auth-login'),
    path('inscription/lookup/', student_lookup_view, name='student-lookup'),
    path('inscription/catalogs/', inscription_catalogs_view, name='inscription-catalogs'),
    path('inscription/matricula/', inscription_generate_matricula_view, name='inscription-matricula'),
    path('inscription/payment-link/', inscription_payment_link_view, name='inscription-payment-link'),
    path('admin/payment-info/', admin_payment_info_view, name='admin-payment-info'),
    path('admin/payment-cancel/', admin_payment_cancel_view, name='admin-payment-cancel'),
]
