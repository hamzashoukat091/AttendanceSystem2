from django.urls import path
from django.contrib.auth import views as auth_views
from . import views
from . import simple_views
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Using built-in LoginView
    path('admin/', admin.site.urls),
    
    # ===== NEW SIMPLIFIED ATTENDANCE SYSTEM =====
    path('', simple_views.home, name='home'),
    
    # Face Registration
    path('register/select/', simple_views.select_user_for_registration, name='select_user_registration'),
    path('register/face/<int:user_id>/', simple_views.register_face, name='register_face'),
    path('api/save-face/', simple_views.save_face_image, name='save_face_image'),
    
    # Attendance Scanning
    path('attendance/scanner/', simple_views.attendance_scanner, name='attendance_scanner'),
    path('api/recognize/', simple_views.recognize_and_mark_attendance, name='recognize_and_mark'),
    
    # User Management
    path('users/view/', simple_views.view_users, name='view_users'),
    
    # ===== OLD SYSTEM (kept for admin/backup) =====
    path("register/", views.register, name="register"),
    path('userlogin/', views.login_view, name='userlogin'),
    
    path('changepasssword/',views.change_password , name='change_password'),
    
    path('old-index/',views.index, name="old_index"),
    path('userprofile/', views.userprofile_view, name='userprofile'),
    path("userdash/", views.userdash_view, name="userdash"),
    
    
    path('face_add/', views.face_add, name='face_add'),
    # path("save_face/", views.save_face, name="save_face"),
    
    path('face_scan/', views.face_scan, name='face_scan'),
    path('mark_attendance/', views.mark_attendance_ajax, name='mark_attendance'),
    
    path('face_view/', views.face_view , name='face_view'),
    path('leaverequest/', views.leave_request_view , name='leave_request'), 
       
    path('attendance_report/', views.attendance_report, name='attendance_report'),
    path('download/', views.download_attendance_csv, name='download_attendance_csv'),
    path('download_monthly_attendance_csv/', views.download_monthly_attendance_csv, name='download_monthly_attendance_csv'),
    # path("attendancemark/", views.mark_attendance_ajax, name="mark_attendance"),
    # path("attendance/my/", views.my_attendance, name="my_attendance"),
    # path("attendance/all/", views.all_attendance, name="all_attendance"),
    
    path("report/", views.attendance_report, name="attendance_report"),
    
    path('help_support/', views.help_support, name='help_support'),
    path('contact_admin/', views.contact_admin, name='contact_admin'),
    
    path("chatbot/", views.chatbot_view, name="chatbot"),
    path("chatbot/api/", views.chatbot_api, name="chatbot_api"),

    path("logout/", auth_views.LogoutView.as_view(next_page="userlogin"), name="logout")
]+ static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)