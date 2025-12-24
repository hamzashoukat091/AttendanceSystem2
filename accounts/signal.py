# signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.mail import send_mail
from .models import CustomUser

@receiver(post_save, sender=CustomUser)
def send_approval_email(sender, instance, created, **kwargs):
    if not created and instance.is_approved:
        sender = "tushyparmar@gmail.com.com"
        send_mail(
            "Your account is approved",
            "You can now log in to AttendEase.",
            "tushyparmar@gmail.com.com",
            [instance.email],
            fail_silently=True,
        )