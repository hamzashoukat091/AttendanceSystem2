import calendar
import csv
import os
from pyexpat.errors import messages
import shutil
from datetime import timedelta, date 
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.shortcuts import redirect, render
from django.utils.html import format_html
from django.template.response import TemplateResponse
from django.http import HttpResponse, JsonResponse
from django.db.models import Count, Q, F
from django.db.models.functions import TruncMonth
from django.urls import path, reverse

from django.contrib import messages
import csv, io

from .models import CustomUser, Attendance, LeaveRequest, FaceChangeRequest, UserFace, MasterUserRecord



# ---------------------------
# Custom Admin Site
# ---------------------------
class CustomAdminSite(admin.AdminSite):
    site_header = "AttendEase Administration"
    site_title = "AttendEase Admin"
    index_title = "Dashboard"
    

    # Custom URLs
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('ajax/dashboard/', self.admin_view(self.ajax_dashboard_data), name='ajax_dashboard_data'),
            path('upload-master-data/', self.admin_view(self.upload_master_data_view), name='upload_master_data'),
        ]
        return custom_urls + urls




    def upload_master_data_view(self, request):
            from django.shortcuts import render, redirect, get_object_or_404
            from django.http import JsonResponse
            from django.contrib import messages
            from django.conf import settings
            import csv, io, os, json, shutil, requests
            from .models import MasterUserRecord, UserFace, MasterUpload, CustomUser
            from django.db import transaction, IntegrityError

            print(f"➡️ Entered upload_master_data_view | Method: {request.method}")

            context = {"uploads": MasterUpload.objects.all().order_by("-uploaded_at")[:20]}

            #   Helper: Save face image
            def save_default_face(username, face_src):
                """
                Save the default face from URL, absolute, or relative path to:
                MEDIA_ROOT/faces/<username>/<username>_default.jpg
                Returns relative media path (faces/username/...) if success, else None.
                """
                import requests

                if not face_src:
                    print(f"⚠️ No face source provided for {username}")
                    return None

                # Normalize and sanitize
                face_src = face_src.strip().strip('"').strip("'").replace("\\", "/")
                print(f"\n🧭 Processing face for {username}")
                print(f"📄 Original path from CSV: {face_src}")

                # Prepare destination folder
                faces_dir = os.path.join(settings.MEDIA_ROOT, "faces", username)
                os.makedirs(faces_dir, exist_ok=True)

                dest_name = f"{username}_default.jpg"
                dest_path = os.path.join(faces_dir, dest_name)

                try:
                    # CASE 1: Remote URL
                    if face_src.startswith(("http://", "https://")):
                        print(f"🌐 Downloading image from URL for {username}")
                        response = requests.get(face_src, timeout=10, stream=True)
                        if response.status_code == 200:
                            with open(dest_path, "wb") as f:
                                for chunk in response.iter_content(1024):
                                    f.write(chunk)
                        else:
                            print(f"  URL returned {response.status_code}")
                            return None

                    # CASE 2: Absolute path (Windows or Unix)
                    elif os.path.isabs(face_src):
                        print(f"📁 Detected absolute path → {face_src}")
                        if os.path.exists(face_src):
                            shutil.copy(face_src, dest_path)
                            print(f"  Copied from absolute path")
                        else:
                            print(f"  Absolute path not found: {face_src}")
                            return None

                    # CASE 3: Relative to MEDIA_ROOT
                    else:
                        abs_src = os.path.join(settings.MEDIA_ROOT, face_src)
                        if os.path.exists(abs_src):
                            shutil.copy(abs_src, dest_path)
                            print(f"  Copied from MEDIA_ROOT relative path: {abs_src}")
                        else:
                            abs_src = os.path.join(settings.BASE_DIR, face_src)
                            if os.path.exists(abs_src):
                                shutil.copy(abs_src, dest_path)
                                print(f"  Copied from BASE_DIR relative path: {abs_src}")
                            else:
                                print(f"  Could not find file anywhere: {face_src}")
                                return None

                    # Validate final result
                    if not os.path.exists(dest_path):
                        print(f"  File missing after copy: {dest_path}")
                        return None

                    size = os.path.getsize(dest_path)
                    print(f"📸 Saved → {dest_path} ({size} bytes)")
                    if size < 500:
                        print(f"⚠️ File too small, likely invalid image.")
                        return None

                    return f"faces/{username}/{dest_name}"

                except Exception as e:
                    print(f"💥 Exception in save_default_face for {username}: {e}")
                    return None


            #   Helper: Process CSV rows
            def process_rows(rows):
                created, updated, skipped = 0, 0, 0

                for row in rows:
                    username = (row.get("username") or "").strip()
                    enrollment_no = (row.get("enrollment_no") or "").strip()
                    email = (row.get("email") or "").strip().lower()
                    user_type = (row.get("user_type") or "student").strip().lower()
                    face_image = (
                        row.get("face_image")
                        or row.get("face_path")
                        or row.get("Face")
                        or row.get("image")
                        or row.get("photo")
                        or ""
                    ).strip().strip('"').strip("'")

                    if not username or not enrollment_no:
                        print(f"⚠️ Skipped: Missing username/enrollment_no → {row}")
                        skipped += 1
                        continue
                    
                    try:
                        with transaction.atomic():
                            master, created_flag = MasterUserRecord.objects.update_or_create(
                                enrollment_no=enrollment_no,
                                defaults={
                                    "username": username,
                                    "email": email,
                                    "user_type": user_type,
                                    "uploaded_by": request.user if request.user.is_authenticated else None,
                                },
                            )

                            print(f"🧾 Processing row for {username} | face_image: '{face_image}'")

                            # Save face if exists
                            if face_image:
                                relative_path = save_default_face(username, face_image)
                                if relative_path:
                                    master.face_image = relative_path
                                    master.save(update_fields=["face_image"])
                                    print(f"  Face saved for {username}")
                                else:
                                    print(f"⚠️ Face not saved for {username}")
                                    skipped += 1

                            if created_flag:
                                created += 1
                            else:
                                updated += 1

                    except IntegrityError as e:
                        print(f"⚠️ Integrity error for {username}: {e}")
                        skipped += 1

                return created, updated, skipped

            #   AJAX Save
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                try:
                    data = json.loads(request.body.decode("utf-8"))
                    rows = data.get("rows", [])
                except Exception as e:
                    return JsonResponse({"error": f"Invalid JSON: {e}"}, status=400)

                created, updated, skipped = process_rows(rows)
                return JsonResponse({"created": created, "updated": updated, "skipped": skipped})

            #   CSV Upload
            if request.method == "POST" and request.FILES.get("file"):
                uploaded_file = request.FILES["file"]
                decoded = uploaded_file.read()
                try:
                    decoded_file = decoded.decode("utf-8")
                except UnicodeDecodeError:
                    decoded_file = decoded.decode("latin-1")

                reader = csv.DictReader(io.StringIO(decoded_file))
                rows = list(reader)
                created, updated, skipped = process_rows(rows)

                uploaded_file.seek(0)
                MasterUpload.objects.create(
                    file=uploaded_file,
                    uploaded_by=request.user if request.user.is_authenticated else None,
                    total_rows=len(rows),   
                    created_rows=created,
                    updated_rows=updated,
                )

                messages.success(
                    request,
                    f"  Processed {len(rows)} rows — {created} new, {updated} updated, {skipped} skipped.",
                )
                print("  Returning redirect after upload")
                return redirect("custom_admin:upload_master_data")

            #   Delete Upload
            if request.GET.get("delete"):
                upload_id = request.GET.get("delete")
                upload_obj = get_object_or_404(MasterUpload, pk=upload_id)
                if upload_obj.file and os.path.exists(upload_obj.file.path):
                    os.remove(upload_obj.file.path)
                upload_obj.delete()
                messages.success(request, f"🗑️ Deleted {upload_obj.file.name}")
                print("  Returning redirect after delete")
                return redirect("custom_admin:upload_master_data")

            #   Always return TemplateResponse (prevents NoneType error)
            print("  Rendering upload_master_data.html now")
            return TemplateResponse(
                request,
                "admin/upload_master_data.html",
                {**self.each_context(request), **context, "title": "Upload Master Data"},
            )

    
    def index(self, request, extra_context=None):
        # CSV export (if requested)
        if request.GET.get('export') == 'csv':
            return self.export_attendance_csv()
    
        context = {
            **self.get_dashboard_context(request),
            **self.each_context(request),
        }
        return TemplateResponse(request, "admin/index.html", context)


    # ---------------------------
    # AJAX for real-time updates
    # ---------------------------
    def ajax_dashboard_data(self, request):
        context = self.get_dashboard_context(request)
        data = {
            'today_attendance': context['today_attendance'],
            'top_students': [
                {'username': u.username, 'attendance_percent': round(u.attendance_percent, 2)}
                for u in context['top_students']
            ],
            'top_faculty': [
                {'username': u.username, 'attendance_percent': round(u.attendance_percent, 2)}
                for u in context['top_faculty']
            ],
            'student_counts': context['student_counts'],
            'faculty_counts': context['faculty_counts'],
            'low_attendance_users': [
                {'username': u.username, 'user_type': u.user_type, 'attendance_percent': round(u.attendance_percent, 2)}
                for u in context['low_attendance_users']
            ],
        }
        return JsonResponse(data)

    # ---------------------------
    # Dashboard Context
    # ---------------------------
    def get_dashboard_context(self, request):
        today_total = Attendance.objects.filter(date= date.today()).count()
        total_users = CustomUser.objects.count()
        today_attendance_percent = round((today_total / total_users) * 100, 2) if total_users else 0

        total_students = CustomUser.objects.filter(user_type='student').count()
        total_faculty = CustomUser.objects.filter(user_type='faculty').count()

        # Top 3 students/faculty by attendance %
        top_students = (
            CustomUser.objects.filter(user_type='student')
            .annotate(
                present_days=Count('attendance', filter=Q(attendance__status='Present')),
                total_days=Count('attendance')
            )
            .annotate(attendance_percent=F('present_days') * 100.0 / F('total_days'))
            .order_by('-attendance_percent')[:3]
        )

        top_faculty = (
            CustomUser.objects.filter(user_type='faculty')
            .annotate(
                present_days=Count('attendance', filter=Q(attendance__status='Present')),
                total_days=Count('attendance')
            )
            .annotate(attendance_percent=F('present_days') * 100.0 / F('total_days'))
            .order_by('-attendance_percent')[:3]
        )

        # Monthly Attendance Charts
        student_monthly = (
            Attendance.objects.filter(user__user_type='student')
            .annotate(month=TruncMonth('date'))
            .values('month')
            .annotate(count=Count('id'))
            .order_by('month')
        )
        faculty_monthly = (
            Attendance.objects.filter(user__user_type='faculty')
            .annotate(month=TruncMonth('date'))
            .values('month')
            .annotate(count=Count('id'))
            .order_by('month')
        )

        months = [m['month'].strftime('%b') for m in student_monthly] or ['Jan','Feb','Mar','Apr','May']
        student_counts = [m['count'] for m in student_monthly] or [0,0,0,0,0]
        faculty_counts = [m['count'] for m in faculty_monthly] or [0,0,0,0,0]

        # Low Attendance <75%
        low_attendance_users = (
            CustomUser.objects.annotate(
                present_days=Count('attendance', filter=Q(attendance__status='Present')),
                total_days=Count('attendance')
            )
            .annotate(attendance_percent=F('present_days') * 100.0 / F('total_days'))
            .filter(attendance_percent__lt=75)
        )

        # Recent 5 attendance records
        attendance_by_status = Attendance.objects.select_related('user').order_by('-date')[:5]

        context = {
            **self.each_context(request),
            'total_students': total_students,
            'total_faculty': total_faculty,
            'today_attendance': today_attendance_percent,
            'top_students': top_students,
            'top_faculty': top_faculty,
            'months': months,
            'student_counts': student_counts,
            'faculty_counts': faculty_counts,
            'low_attendance_users': low_attendance_users,
            'attendance_by_status': attendance_by_status,
        }
        return context

    # ---------------------------
    # CSV Export
    # ---------------------------
    def export_attendance_csv(self):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="attendance.csv"'
        import csv
        writer = csv.writer(response)
        writer.writerow(['Username','User Type','Date','Status','Check In','Check Out'])
        for att in Attendance.objects.select_related('user').all():
            writer.writerow([
                att.user.username,
                att.user.user_type,
                att.date,
                att.status,
                att.check_in if att.check_in else '--',
                att.check_out if att.check_out else '--'
            ])
        return response


# ---------------------------
# Create custom admin site instance
# ---------------------------

custom_admin_site = CustomAdminSite(name='custom_admin')


class MasterUserRecordAdmin(admin.ModelAdmin):
    list_display = ("username", "enrollment_no", "user_type", "email", "created_at")
    search_fields = ("username", "enrollment_no")

#   Register with your custom admin site
custom_admin_site.register(MasterUserRecord, MasterUserRecordAdmin)

# ---------------------------
# CustomUser Admin
# ---------------------------
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "email", "enrollment_no", "user_type", "is_approved", "has_face_data")
    list_filter = ("is_approved", "user_type", "has_face_data")
    actions = ["approve_users"]

    def approve_users(self, request, queryset):
        queryset.update(is_approved=True)
    approve_users.short_description = "Approve selected users"


custom_admin_site.register(CustomUser, CustomUserAdmin)


class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "check_in", "check_out", "status")
    search_fields = ("user__username", "user__enrollment_no")

    # Custom URLs
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "all-users-attendance/",
                self.admin_site.admin_view(self.all_users_attendance_view),
                name="all_users_attendance",
            ),
        ]
        return custom_urls + urls

    # Redirect changelist to custom view
    def changelist_view(self, request, extra_context=None):
        url = reverse('admin:all_users_attendance', current_app=self.admin_site.name)
        return redirect(url)
    
    def profile_preview(self, obj):
        """
        Returns a 50px image from:
        1. UserFace table → latest approved face
        2. If not found → default uploaded CSV face in media/faces/<username>/
        3. If not found → default placeholder
        """
        from .models import UserFace

        # CASE 1: UserFace exists
        latest_face = UserFace.objects.filter(user=obj).order_by("-uploaded_at").first()
        if latest_face and latest_face.face_image:
            return format_html("<img src='{}' width='50' height='50' style='border-radius:50%; object-fit:cover;'/>",
                               latest_face.face_image.url)

        # CASE 2: Check default CSV face
        default_path = f"{settings.MEDIA_URL}faces/{obj.username}/{obj.username}_default.jpg"
        full_default_file = os.path.join(settings.MEDIA_ROOT, "faces", obj.username, f"{obj.username}_default.jpg")

        if os.path.exists(full_default_file):
            return format_html("<img src='{}' width='50' height='50' style='border-radius:50%; object-fit:cover;'/>",
                               default_path)

        # CASE 3: Fallback icon
        return format_html(
            "<img src='/static/default_profile.png' width='50' height='50' style='border-radius:50%; object-fit:cover;'/>"
        )

    profile_preview.short_description = "Face"

    def all_users_attendance_view(self, request):
        from calendar import monthrange
        from django.db.models import Count, Q
    
        user_type_filter = request.GET.get("user_type", "")
        search_query = request.GET.get("search", "")
        export_type = request.GET.get("export")  # attendance / leave / monthly
        export_user_id = request.GET.get("user")  # optional user
        month = int(request.GET.get("month", date.today().month))
        year = int(request.GET.get("year", date.today().year))
    
        users = CustomUser.objects.all().order_by("username")
        if user_type_filter:
            users = users.filter(user_type=user_type_filter)
        if search_query:
            users = users.filter(username__icontains=search_query)
        if export_user_id:
            users = users.filter(id=export_user_id)
    
        all_user_data = []
        for user in users:
            # Fetch this month's attendance
            records = Attendance.objects.filter(user=user, date__year=year, date__month=month).order_by("-date")
            leaves = LeaveRequest.objects.filter(user=user, status="Approved")
    
            # Monthly counters
            total_days = monthrange(year, month)[1]
            present_days = records.filter(status__startswith="Present").count()
            absent_days = records.filter(status="Absent").count()
            leave_days = records.filter(status="Leave").count() + leaves.filter(start_date__month=month).count()
            holiday_days = records.filter(status="Holiday").count()
            working_days = total_days - holiday_days
            attendance_percentage = round((present_days / working_days) * 100, 2) if working_days else 0
            
        
    
            # Build structure
            all_user_data.append({
                "user": user,
                "records": records,
                "present_days": present_days,
                "absent_days": absent_days,
                "leave_days": leave_days,
                "holiday_days": holiday_days,
                "attendance_percentage": attendance_percentage,
                "month": month,
                "year": year,
                "leaves": leaves,
            })
    
        # ----------------------------
        # CSV Export
        # ----------------------------
        if export_type in ("attendance", "leave", "monthly"):
            import csv
            response = HttpResponse(content_type="text/csv")
            filename = f"user_{export_user_id}_{export_type}_{month}_{year}.csv"
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            writer = csv.writer(response)
    
            if export_type == "attendance":
                writer.writerow(["Username", "Date", "Status", "Check In", "Check Out"])
                for data in all_user_data:
                    for r in data["records"]:
                        writer.writerow([
                            data["user"].username,
                            r.date.strftime("%Y-%m-%d"),
                            r.status,
                            r.check_in.strftime("%H:%M:%S") if r.check_in else "--",
                            r.check_out.strftime("%H:%M:%S") if r.check_out else "--",
                        ])
            elif export_type == "leave":
                writer.writerow(["Username", "From", "To", "Reason", "Status"])
                for data in all_user_data:
                    for l in data["leaves"]:
                        writer.writerow([
                            l.user.username,
                            l.start_date,
                            l.end_date,
                            l.reason,
                            l.status
                        ])
            elif export_type == "monthly":
                writer.writerow(["Username", "Month", "Year", "Present", "Absent", "Leave", "Holiday", "Attendance %"])
                for data in all_user_data:
                    writer.writerow([
                        data["user"].username,
                        calendar.month_name[data["month"]],
                        data["year"],
                        data["present_days"],
                        data["absent_days"],
                        data["leave_days"],
                        data["holiday_days"],
                        data["attendance_percentage"],
                    ])
            return response
    
        # ----------------------------
        # Render Admin Template
        # ----------------------------
        context = {
            "user_data": all_user_data,
            "user_type_filter": user_type_filter,
            "search_query": search_query,
            "selected_month": month,
            "selected_year": year,
        }
        return TemplateResponse(
            request,
            "admin/all_users_attendance.html",
            {**context, **self.admin_site.each_context(request)},
        )
    
    
# Re    gister admin
custom_admin_site.register(Attendance, AttendanceAdmin)


# # ---------------------------
# # Attendance Admin
# # ---------------------------
# class AttendanceAdmin(admin.ModelAdmin):
#     list_display = ("user", "date", "check_in", "check_out", "status")
#     list_filter = ("status", "date")
#     search_fields = ("user__username", "user__enrollment_no")

#     def get_urls(self):
#         urls = super().get_urls()
#         custom_urls = [
#             path(
#                 "all-users-attendance/",
#                 self.admin_site.admin_view(self.all_users_attendance_view),
#                 name="all_users_attendance",
#             ),
#         ]
#         return custom_urls + urls

#     def all_users_attendance_view(self, request):
#         records = Attendance.objects.select_related("user").order_by("-date")
#         if request.GET.get("export") == "csv":
#             response = HttpResponse(content_type="text/csv")
#             response['Content-Disposition'] = 'attachment; filename="all_users_attendance.csv"'
#             import csv
#             writer = csv.writer(response)
#             writer.writerow(["Username","Enrollment No","User Type","Date","Check In","Check Out","Status"])
#             for r in records:
#                 writer.writerow([
#                     r.user.username,
#                     r.user.enrollment_no or "--",
#                     r.user.user_type,
#                     r.date.strftime("%Y-%m-%d"),
#                     r.check_in.strftime("%H:%M:%S") if r.check_in else "--",
#                     r.check_out.strftime("%H:%M:%S") if r.check_out else "--",
#                     r.status
#                 ])
#             return response
#         return render(request, "admin/all_users_attendance.html", {"records": records})


# custom_admin_site.register(Attendance, AttendanceAdmin)


# ---------------------------
# LeaveRequest Admin
# ---------------------------
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "user_type", "start_date", "end_date", "leave_type", "created_at", "status")
    list_filter = ("status", "leave_type", "user__user_type")
    search_fields = ("user__username", "user__enrollment_no")
    actions = ["approve_leaves", "reject_leaves"]

    def user_type(self, obj):
        return obj.user.user_type

    def approve_leaves(self, request, queryset):
        queryset.update(status="Approved")
    approve_leaves.short_description = "Approve selected leave requests"

    def reject_leaves(self, request, queryset):
        queryset.update(status="Rejected")
    reject_leaves.short_description = "Reject selected leave requests"


custom_admin_site.register(LeaveRequest, LeaveRequestAdmin)


class FaceChangeRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "created_at", "preview_old", "preview_new")
    actions = ["approve_request", "reject_request"]

    def preview_old(self, obj):
        from .models import UserFace
        # Show latest approved face
        latest_face = UserFace.objects.filter(user=obj.user).order_by("-uploaded_at").first()
        if latest_face and latest_face.face_image:
            return format_html("<img src='{}' width='50'/>", latest_face.face_image.url)

        # Fallback: check default faces folder
        user_path = f"{settings.MEDIA_URL}faces/{obj.user.username}/{obj.user.username}_1.jpg"
        return format_html("<img src='{}' width='50'/>", user_path)
    preview_old.short_description = "Old Face"

    def preview_new(self, obj):
        # Only show pending face from request
        if hasattr(obj, "new_image") and obj.new_image:
            return format_html("<img src='{}' width='50'/>", obj.new_image.url)
        elif getattr(obj, "new_face_path", None):
            relative_path = obj.new_face_path.replace(str(settings.MEDIA_ROOT), "").lstrip("/")
            return format_html("<img src='{}{}' width='50'/>", settings.MEDIA_URL, relative_path)

        return "No Image"
    preview_new.short_description = "New Face"

    @admin.action(description="Approve selected face change requests")
    def approve_request(self, request, queryset):
        count = 0
        for obj in queryset:
            try:
                user = obj.user
                user_folder = os.path.join(settings.MEDIA_ROOT, "faces", user.username)
                os.makedirs(user_folder, exist_ok=True)
    
                # Determine source path of new face
                src_path = None
                if hasattr(obj, "new_image") and getattr(obj.new_image, "path", None):
                    src_path = obj.new_image.path
                elif getattr(obj, "new_face_path", None) and os.path.exists(obj.new_face_path):
                    src_path = obj.new_face_path
    
                if not src_path:
                    continue
                
                # Count existing images to create next number
                existing_files = [f for f in os.listdir(user_folder) if f.startswith(user.username) and f.endswith(".jpg")]
                next_index = len(existing_files) + 1
                dest_path = os.path.join(user_folder, f"{user.username}_{next_index}.jpg")
    
                # Copy new face
                shutil.copy(src_path, dest_path)
    
                # Update UserFace to latest approved
                from .models import UserFace
                UserFace.objects.update_or_create(
                    user=user,
                    defaults={"face_image": f"faces/{user.username}/{user.username}_{next_index}.jpg"}
                )
    
                # Mark request as approved
                obj.status = "Approved"
                obj.save()
                count += 1
    
            except Exception as e:
                self.message_user(request, f"Error approving {obj.user.username}: {str(e)}", messages.ERROR)
    
        self.message_user(request, f"{count} face change request(s) approved  ", messages.SUCCESS)
    
    @admin.action(description="Reject selected face change requests")
    def reject_request(self, request, queryset):
        count = 0
        for obj in queryset:
            obj.status = "Rejected"
            obj.save()
            count += 1
        self.message_user(request, f"{count} face change request(s) rejected 🚫", messages.WARNING)

custom_admin_site.register(FaceChangeRequest, FaceChangeRequestAdmin)

class UserFaceAdmin(admin.ModelAdmin):
    list_display = ("user", "face_preview", "uploaded_at", "face_status")
    search_fields = ("user__username",)
    list_filter = ("uploaded_at",)

    def face_preview(self, obj):
        if obj.face_image:
            return format_html("<img src='{}' width='50'/>", obj.face_image.url)
        return "No Face"
    face_preview.short_description = "Face"

    def face_status(self, obj):
        # First-time face captured → status "Captured"
        # If no face yet → status "Pending"
        return "Captured" if obj.face_image else "Pending"
    face_status.short_description = "Status"

# Register the UserFace admin
custom_admin_site.register(UserFace, UserFaceAdmin)