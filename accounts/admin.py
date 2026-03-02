import os
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from django.urls import reverse

from .models import CustomUser, UserFaceEmbedding, Attendance


# ---------------------------
# UserFaceEmbedding Inline
# ---------------------------
class UserFaceEmbeddingInline(admin.TabularInline):
    model = UserFaceEmbedding
    extra = 0
    fields = ("image_path", "model_name", "embedding_preview", "created_at")
    readonly_fields = ("image_path", "model_name", "embedding_preview", "created_at")
    can_delete = True
    verbose_name = "Face Embedding"
    verbose_name_plural = "Face Embeddings"

    def embedding_preview(self, obj):
        """Display first 10 dimensions of the 512D embedding vector."""
        if obj and obj.embedding:
            try:
                import json
                if isinstance(obj.embedding, str):
                    embedding = json.loads(obj.embedding)
                else:
                    embedding = obj.embedding

                preview = embedding[:10] if len(embedding) > 10 else embedding
                preview_str = ", ".join([f"{x:.4f}" for x in preview])
                return format_html(
                    "<span title='512D vector' style='font-family: monospace; font-size: 11px;'>[{}...]</span>",
                    preview_str
                )
            except Exception as e:
                return f"Error: {str(e)}"
        return "No embedding"
    embedding_preview.short_description = "Embedding (first 10)"

    def has_add_permission(self, request, obj=None):
        return False  # Prevent manual creation from admin


# ---------------------------
# CustomUser Admin
# ---------------------------
class CustomUserAdmin(UserAdmin):
    list_display = (
        "id", "username", "first_name", "last_name", "email",
        "api_user_id", "user_type", "is_approved", "is_active",
        "has_face_data", "face_images_count", "embedding_count",
        "date_joined",
    )
    list_filter = ("is_approved", "is_active", "user_type", "has_face_data")
    search_fields = ("username", "first_name", "last_name", "email", "api_user_id")
    ordering = ("id",)
    actions = ["approve_users"]
    inlines = [UserFaceEmbeddingInline]

    def embedding_count(self, obj):
        """Display total number of face embeddings for this user."""
        count = obj.face_embeddings.count()
        if count > 0:
            return format_html(
                '<span style="color: green; font-weight: bold;">{}</span>',
                count
            )
        return format_html(
            '<span style="color: red;">0</span>'
        )
    embedding_count.short_description = "Embeddings"

    def approve_users(self, request, queryset):
        queryset.update(is_approved=True)
    approve_users.short_description = "Approve selected users"


admin.site.register(CustomUser, CustomUserAdmin)


# ---------------------------
# UserFaceEmbedding Admin
# ---------------------------
class UserFaceEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("user_link", "image_preview", "model_name", "embedding_dimensions", "created_at")
    list_filter = ("model_name", "created_at")
    search_fields = ("user__username", "user__enrollment_no", "image_path")
    readonly_fields = ("user", "image_path", "image_preview_large", "embedding", "model_name", "created_at", "embedding_preview", "embedding_dimensions")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    fieldsets = (
        ("User Information", {
            "fields": ("user", "image_path", "image_preview_large")
        }),
        ("Embedding Details", {
            "fields": ("model_name", "embedding_dimensions", "embedding_preview", "created_at")
        }),
        ("Raw Embedding Data", {
            "fields": ("embedding",),
            "classes": ("collapse",),
            "description": "Full 512D embedding vector (collapsed for performance)"
        }),
    )

    def user_link(self, obj):
        """Create clickable link to user's admin page."""
        url = reverse("admin:accounts_customuser_change", args=[obj.user.id])
        return format_html('<a href="{}">{}</a>', url, obj.user.username)
    user_link.short_description = "User"
    user_link.admin_order_field = "user__username"

    def image_preview(self, obj):
        """Display small thumbnail of the face image (clickable to full size)."""
        if obj and obj.image_path:
            try:
                image_url = f"{settings.MEDIA_URL}{obj.image_path}"
                return format_html(
                    '<a href="{}" target="_blank">'
                    '<img src="{}" width="50" height="50" '
                    'style="border-radius: 4px; object-fit: cover; cursor: pointer;" '
                    'title="Click to view full size"/>'
                    '</a>',
                    image_url, image_url
                )
            except Exception:
                return format_html('<span style="color: red;">Error loading image</span>')
        return "No image"
    image_preview.short_description = "Image"

    def image_preview_large(self, obj):
        """Display larger preview of the face image in detail view."""
        if obj and obj.image_path:
            try:
                image_url = f"{settings.MEDIA_URL}{obj.image_path}"
                return format_html(
                    '<a href="{}" target="_blank">'
                    '<img src="{}" style="max-width: 300px; max-height: 300px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);" '
                    'title="Click to view full size"/>'
                    '</a><br/>'
                    '<small style="color: gray;">Path: {}</small>',
                    image_url, image_url, obj.image_path
                )
            except Exception as e:
                return format_html('<span style="color: red;">Error: {}</span>', str(e))
        return "No image"
    image_preview_large.short_description = "Face Image Preview"

    def embedding_dimensions(self, obj):
        """Display the dimension count of the embedding."""
        if obj and obj.embedding:
            try:
                import json
                if isinstance(obj.embedding, str):
                    embedding = json.loads(obj.embedding)
                else:
                    embedding = obj.embedding
                return f"{len(embedding)}D"
            except Exception:
                return "Error"
        return "N/A"
    embedding_dimensions.short_description = "Dimensions"

    def embedding_preview(self, obj):
        """Display first 10 dimensions of the 512D embedding vector."""
        if obj and obj.embedding:
            try:
                import json
                if isinstance(obj.embedding, str):
                    embedding = json.loads(obj.embedding)
                else:
                    embedding = obj.embedding

                preview = embedding[:10] if len(embedding) > 10 else embedding
                preview_str = ", ".join([f"{x:.6f}" for x in preview])

                return format_html(
                    "<div style='font-family: monospace; font-size: 11px;'>"
                    "<strong>First 10 dimensions:</strong><br/>"
                    "<code>[{}...]</code><br/>"
                    "<small style='color: gray;'>Full vector available in 'Raw Embedding Data' section</small>"
                    "</div>",
                    preview_str
                )
            except Exception as e:
                return format_html('<span style="color: red;">Error: {}</span>', str(e))
        return "No embedding"
    embedding_preview.short_description = "Embedding Preview"

    def has_add_permission(self, request):
        """Prevent manual creation - embeddings should be computed programmatically."""
        return False

    def get_queryset(self, request):
        """Optimize queries by selecting related user."""
        qs = super().get_queryset(request)
        return qs.select_related('user')


admin.site.register(UserFaceEmbedding, UserFaceEmbeddingAdmin)


# ---------------------------
# Attendance Admin
# ---------------------------
class AttendanceAdmin(admin.ModelAdmin):
    list_display = (
        "id", "user", "date", "status",
        "check_in", "check_out",
        "check_in_confidence", "check_out_confidence",
        "api_message",
    )
    list_filter = ("status", "date")
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__email")
    ordering = ("-date", "-check_in")
    readonly_fields = ("user", "date", "check_in", "check_out", "status", "check_in_confidence", "check_out_confidence", "api_message")
    date_hierarchy = "date"
    list_per_page = 50


admin.site.register(Attendance, AttendanceAdmin)
