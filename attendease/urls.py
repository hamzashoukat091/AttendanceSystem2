from django.contrib import admin
from django.urls import path
from django.conf.urls import include
from .text_face import verify_faces

from accounts.admin import custom_admin_site


urlpatterns = [
    path('admin/', custom_admin_site.urls),
    # path("test_SFace/", verify_faces , name="test_SFace"),
    path('', include('accounts.urls')),
    path('accounts/', include('accounts.urls')),
]
