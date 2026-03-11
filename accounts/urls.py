from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from . import simple_views

urlpatterns = [
    path('', simple_views.home, name='home'),

    # Face Registration
    path('register/select/', simple_views.select_user_for_registration, name='select_user_registration'),
    path('register/face/<int:user_id>/', simple_views.register_face, name='register_face'),
    path('api/save-faces-batch/', simple_views.save_faces_batch, name='save_faces_batch'),
    path('delete-face-data/<int:user_id>/', simple_views.delete_face_data, name='delete_face_data'),

    # Attendance
    path('api/recognize/', simple_views.recognize_and_mark_attendance, name='recognize_and_mark'),
    path('api/users/', simple_views.api_users, name='api_users'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
