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


class PendingAttendanceSync(models.Model):
    """
    Queue of attendance records that failed to post to the remote API.
    A background worker retries these periodically until success or max retries.
    """
    STATUS_PENDING   = 'pending'
    STATUS_ABANDONED = 'abandoned'
    STATUS_CHOICES   = [(STATUS_PENDING, 'Pending'), (STATUS_ABANDONED, 'Abandoned')]

    user_id         = models.IntegerField()
    attendance_type = models.CharField(max_length=20)       # 'check_in' or 'check_out'
    scheduled_time  = models.DateTimeField()                # when the original event occurred
    retry_count     = models.IntegerField(default=0)
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    last_error      = models.TextField(blank=True, default='')
    created_at      = models.DateTimeField(auto_now_add=True)
    last_attempted  = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user_id', 'attendance_type', 'scheduled_time'],
                name='unique_pending_sync_event'
            )
        ]

    def __str__(self):
        return f"PendingSync user={self.user_id} type={self.attendance_type} retries={self.retry_count}"
