from django.contrib import admin
from django.urls import path
from django.conf.urls import include
from .text_face import verify_faces


urlpatterns = [
    path('admin/', admin.site.urls),
    # path("test_SFace/", verify_faces , name="test_SFace"),
    path('', include('accounts.urls')),
    path('accounts/', include('accounts.urls')),
]
