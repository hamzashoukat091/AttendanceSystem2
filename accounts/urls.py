from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from . import simple_views

urlpatterns = [
    # ===== SIMPLIFIED ATTENDANCE SYSTEM =====
    path('', simple_views.home, name='home'),

    # Face Registration
    path('register/select/', simple_views.select_user_for_registration, name='select_user_registration'),
    path('register/face/<int:user_id>/', simple_views.register_face, name='register_face'),
    path('api/save-face/', simple_views.save_face_image, name='save_face_image'),
    path('delete-face-data/<int:user_id>/', simple_views.delete_face_data, name='delete_face_data'),

    # Attendance Scanning
    path('attendance/scanner/', simple_views.attendance_scanner, name='attendance_scanner'),
    path('api/recognize/', simple_views.recognize_and_mark_attendance, name='recognize_and_mark'),

    # User Management
    path('users/view/', simple_views.view_users, name='view_users'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
