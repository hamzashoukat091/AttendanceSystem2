from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings  
from django.contrib.auth import get_user_model


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
    
    def __str__(self):
        return f"{self.username} ({self.email})"

class PendingFaceUpdate(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    new_image = models.ImageField(upload_to="pending_faces/")
    old_image = models.ImageField(upload_to="faces/", blank=True, null=True)
    status = models.CharField(max_length=20, choices=[("Pending","Pending"), ("Approved","Approved"), ("Rejected","Rejected")], default="Pending")
    created_at = models.DateTimeField(auto_now_add=True)    

class FaceChangeRequest(models.Model):
    STATUS_CHOICES = [
        ("Pending", "Pending"),
        ("Approved", "Approved"),
        ("Rejected", "Rejected"),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    new_face_path = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="Pending")
    created_at = models.DateTimeField(auto_now_add=True)




class Attendance(models.Model):
    STATUS_CHOICES = [
        ("Present", "Present"),
        ("Absent", "Absent"),
        ("Checked In", "Checked In (No Check-Out)"),
        ("Holiday", "Holiday"),
        ("Leave", "Leave"),
    ]

    
    LEAVE_TYPE_CHOICES = [
    ("sick leave", "Sick Leave"),
    ("casual leave", "Casual Leave"),
    ("vacation", "Vacation"),
    ("emergency", "Emergency"),
    ("other", "Other"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    date = models.DateField()
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Absent")
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPE_CHOICES, null=True, blank=True)
    

    class Meta:
        unique_together = ("user", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.user.username} - {self.date} ({self.status})"
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from .utils import update_monthly_attendance
        update_monthly_attendance(self.user, self.date)
    
from django.db import models
from django.contrib.auth import get_user_model
User = get_user_model()

class MonthlyAttendance(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    month = models.IntegerField()
    year = models.IntegerField()
    total_days = models.IntegerField(default=0)
    present_days = models.IntegerField(default=0)
    absent_days = models.IntegerField(default=0)
    leave_days = models.IntegerField(default=0)
    holidays = models.IntegerField(default=0)
    percentage = models.FloatField(default=0.0)

    def calculate_percentage(self):
        """Recalculate attendance percentage and save."""
        # Count present + leave + holiday as attended (optional)
        attended = self.present_days + self.leave_days + self.holidays

        if self.total_days > 0:
            self.percentage = round((attended / self.total_days) * 100, 2)
        else:
            self.percentage = 0.0

        #   Save explicitly
        super(MonthlyAttendance, self).save()

    def __str__(self):
        return f"{self.user.username} - {self.month}/{self.year}"


class LeaveRequest(models.Model):
    STATUS_CHOICES = [
        ("Pending", "Pending"),
        ("Approved", "Approved"),
        ("Rejected", "Rejected"),
    ]

    LEAVE_TYPE_CHOICES = Attendance.LEAVE_TYPE_CHOICES

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField()
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPE_CHOICES, default="Other")
    created_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Pending")

    def __str__(self):
        return f"{self.user.username} - {self.start_date} to {self.end_date} ({self.status})"

class UserFace(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    face_image = models.ImageField(upload_to="faces/%Y/%m/%d/", null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} Face"
    
class MasterUserRecord(models.Model):
    username = models.CharField(max_length=150)
    enrollment_no = models.CharField(max_length=50, unique=True)
    email = models.EmailField(unique=True)
    user_type = models.CharField(max_length=20, choices=[("student", "Student"), ("faculty", "Faculty")])
    face_image = models.ImageField(upload_to="faces/master_faces/", blank=True, null=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.username} ({self.enrollment_no})"
    
class MasterUpload(models.Model):
    file = models.FileField(upload_to="uploads/master_data/")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    total_rows = models.IntegerField(default=0)
    created_rows = models.IntegerField(default=0)
    updated_rows = models.IntegerField(default=0)


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