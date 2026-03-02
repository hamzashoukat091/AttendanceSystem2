from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings


# Create your models here.

class CustomUser(AbstractUser):
    # External API user ID
    api_user_id = models.IntegerField(null=True, blank=True, unique=True, help_text="User ID from external API")

    enrollment_no = models.CharField(max_length=11 , null=True , unique=True , blank=True)
    user_type = models.CharField(
        max_length=10,
        choices=(("student", "Student") , ("faculty", "Faculty")),
        default="student"
    )
    is_approved = models.BooleanField(default=False)  #For check wheather the user is approved or not
    has_face_data = models.BooleanField(default=False)

    # Track how many face images are stored
    face_images_count = models.IntegerField(default=0)

    def get_display_name(self):
        """Return formatted username: replace underscores with spaces and capitalize each word"""
        return self.username.replace('_', ' ').title()

    def __str__(self):
        return f"{self.username} ({self.email})"


class Attendance(models.Model):
    STATUS_CHOICES = [
        ("Checked In", "Checked In"),
        ("Present", "Present"),
        ("Absent", "Absent"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField()
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Absent")
    check_in_confidence = models.FloatField(null=True, blank=True, help_text="Face recognition confidence % at check-in")
    check_out_confidence = models.FloatField(null=True, blank=True, help_text="Face recognition confidence % at check-out")
    api_message = models.CharField(max_length=255, blank=True, default="", help_text="Response message from external API")

    class Meta:
        unique_together = ("user", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.user.username} — {self.date} ({self.status})"


class UserFaceEmbedding(models.Model):
    """
    Store pre-computed face embeddings for fast recognition.
    Each face image gets a 512D vector computed by SFace model.
    This eliminates the need to run DeepFace.verify() on every attendance scan.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='face_embeddings')
    image_path = models.CharField(max_length=500, help_text="Relative path to the face image")
    embedding = models.JSONField(help_text="512D face embedding vector as JSON array")
    model_name = models.CharField(max_length=50, default="SFace", help_text="DeepFace model used")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ("user", "image_path")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=['user']),
        ]
    
    def __str__(self):
        return f"{self.user.username} - Embedding {self.id}"