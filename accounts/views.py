import shutil
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.contrib.auth.views import LoginView
from django.template import loader

from .models import Attendance, CustomUser, FaceChangeRequest, LeaveRequest, MasterUserRecord , UserFace
from .forms import RegistrationForm

from django.contrib.auth import authenticate, login
from .forms import CustomLoginForm

from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import password_validation
from django.utils.timezone import localtime

from django.core.exceptions import ValidationError
from django.contrib import messages

from django.views.decorators.csrf import csrf_exempt
import requests
import json
import traceback
from django.http import JsonResponse

import csv

import calendar
from datetime import datetime, timedelta, date, time
from django.conf import settings
from django.contrib import messages
from django.core.files.storage import FileSystemStorage
from django.http import JsonResponse
from django.shortcuts import render, redirect
import csv, io, json, os
from datetime import datetime
from .models import CustomUser, UserFace


import base64, os, numpy as np
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.utils.timezone import now

from .utils import mark_user_attendance

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .face_system import add_face_image, decode_base64_image, recognize_logged_in_user
from deepface import DeepFace

@login_required
@csrf_exempt
def face_view(request):
    user = request.user

    # 1️⃣ Registered / Approved face (from UserFace)
    user_face = UserFace.objects.filter(user=user).first()
    old_face_url = user_face.face_image.url if user_face and user_face.face_image else None

    # 2️⃣ Pending face (from FaceChangeRequest)
    pending_obj = FaceChangeRequest.objects.filter(user=user, status="Pending").order_by("-created_at").first()
    pending_face_url = None
    if pending_obj and pending_obj.new_face_path:
        path = pending_obj.new_face_path
        if os.path.isabs(path):
            relative = os.path.relpath(path, settings.MEDIA_ROOT)
            pending_face_url = settings.MEDIA_URL + relative.replace("\\", "/")
        else:
            pending_face_url = settings.MEDIA_URL + path.replace("\\", "/")

    # 3️⃣ Rejected face (from FaceChangeRequest)
    rejected_obj = FaceChangeRequest.objects.filter(user=user, status="Rejected").order_by("-created_at").first()
    rejected_face_url = None
    if rejected_obj and rejected_obj.new_face_path:
        path = rejected_obj.new_face_path
        if os.path.isabs(path):
            relative = os.path.relpath(path, settings.MEDIA_ROOT)
            rejected_face_url = settings.MEDIA_URL + relative.replace("\\", "/")
        else:
            rejected_face_url = settings.MEDIA_URL + path.replace("\\", "/")

    context = {
        "user": user,
        "old_face": old_face_url,
        "pending_face": pending_face_url,
        "rejected_face": rejected_face_url,
        "has_face": bool(user_face and user_face.face_image),
    }

    return render(request, "face_view.html", context)

    
@login_required
def face_scan(request):
    user = request.user
    today = datetime.today().date()

    try:
        attendance = Attendance.objects.get(user=user, date=today)
    except Attendance.DoesNotExist:
        attendance = None

    # Determine today's status safely
    if not attendance:
        today_status = "Welcome! Please check in."
        disable_verify = False
    else:
        if attendance.check_in and not attendance.check_out:
            today_status = f"Checked in at {attendance.check_in.strftime('%H:%M:%S')}. You can check out now."
            disable_verify = False
        elif attendance.check_in and attendance.check_out:
            today_status = f"Already checked in at {attendance.check_in.strftime('%H:%M:%S')} and checked out at {attendance.check_out.strftime('%H:%M:%S')}."
            disable_verify = True
        else:  # attendance exists but check_in is None
            today_status = "Welcome! Please check in."
            disable_verify = False

    return render(request, "face_scan.html", {
        "today_status": today_status,
        "disable_verify": disable_verify
    })
    
@csrf_exempt
@login_required
def mark_attendance_ajax(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid request"})

    data = json.loads(request.body)
    image_data = data.get("image_data")
    if not image_data:
        return JsonResponse({"status": "error", "message": "No image received"})

    frame = decode_base64_image(image_data)

    # Recognize face → returns username string
    username = recognize_logged_in_user(frame, request.user.username)
    if not username:
        return JsonResponse({"status": "error", "message": "No face detected or unclear."})

    from django.contrib.auth import get_user_model
    User = get_user_model()
    try:
        user_obj = User.objects.get(username=username)
    except User.DoesNotExist:
        return JsonResponse({"status": "error", "message": "User not found"})

    from .utils import mark_user_attendance
    auto_mark_absent(user_obj)
    status, time, check_in_time = mark_user_attendance(user_obj)

    # Safely create/update today's attendance
    attendance, created = Attendance.objects.get_or_create(
        user=user_obj,
        date=date.today(),
        defaults={
            "status": status,
            "check_in": check_in_time,
        }
    )

    # If record exists but only check_in is missing (user already has Absent)
    if not created and not attendance.check_in:
        attendance.check_in = check_in_time
        attendance.status = status
        attendance.save()

    return JsonResponse({
        "status": "success",
        "username": username,
        "type": status,
        "time": time,
        "check_in": check_in_time.strftime("%H:%M:%S") if check_in_time else None
    })

def auto_mark_absent(user):
    today = date.today()
    first_date = user.date_joined.date()

    # Fetch existing attendance dates once
    existing_dates = set(
        Attendance.objects.filter(user=user).values_list('date', flat=True)
    )

    current = first_date

    while current < today:   # Do NOT include today
        if current not in existing_dates:

            # Saturday = 5, Sunday = 6
            if current.weekday() in (5, 6):
                Attendance.objects.get_or_create(
                    user=user,
                    date=current,
                    defaults={"status": "Holiday"}
                )
            else:
                Attendance.objects.get_or_create(
                    user=user,
                    date=current,
                    defaults={"status": "Absent"}
                )

        current += timedelta(days=1)
        
@login_required
def attendance_report(request):
    user = request.user

    #   1. Get all daily attendance records
    records = list(Attendance.objects.filter(user=user).order_by('-date'))

    #   2. Get all approved leave requests for this user
    approved_leaves = LeaveRequest.objects.filter(
        user=user,
        status="Approved"
    )

    #   3. Create a set of leave dates
    leave_dates = set()
    for leave in approved_leaves:
        current_day = leave.start_date
        while current_day <= leave.end_date:
            leave_dates.add(current_day)
            current_day += timedelta(days=1)

    #   4. Create pseudo-attendance records for leave days not in attendance table
    existing_dates = {rec.date for rec in records}
    for leave_date in leave_dates:
        if leave_date not in existing_dates:
            # Create a temporary display record (not saved in DB)
            records.append(
                Attendance(
                    user=user,
                    date=leave_date,
                    status="Leave",
                    check_in=None,
                    check_out=None
                )
            )
        # Add weekends (Saturday, Sunday) as holidays
    all_dates = {rec.date for rec in records}
    if records:
        first_date = min(all_dates)
        last_date = max(all_dates)
        day = first_date
        while day <= last_date:
            if day.weekday() in (5, 6) and day not in all_dates:
                records.append(
                    Attendance(
                        user=user,
                        date=day,
                        status="Holiday"
                    )
                )
            day += timedelta(days=1)


    #   5. Sort all records by date descending
    records.sort(key=lambda x: x.date, reverse=True)

    #   6. Fetch monthly summary
    monthly_records = MonthlyAttendance.objects.filter(user=user).order_by('-year', '-month')

    return render(request, 'attendance_report.html', {
        'records': records,
        'monthly_records': monthly_records,
    })

from django.shortcuts import render
from django.http import HttpResponse
import csv
from datetime import date
from .models import Attendance, MonthlyAttendance

def monthly_attendance_view(request):
    user = request.user
    monthly_records = MonthlyAttendance.objects.filter(user=user).order_by('-year', '-month')
    return render(request, 'user/monthly_attendance.html', {'monthly_records': monthly_records})


@login_required
def download_monthly_attendance_csv(request):
    user = request.user
    records = MonthlyAttendance.objects.filter(user=user).order_by('-year', '-month')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{user.username}_monthly_attendance.csv"'

    writer = csv.writer(response)
    writer.writerow(['Month', 'Year', 'Total Days', 'Present', 'Absent', 'Leave', 'Holiday', 'Percentage'])

    for r in records:
        writer.writerow([r.month, r.year, r.total_days, r.present_days, r.absent_days,
                         r.leave_days, r.holidays, f"{r.percentage}%"])

    return response

@login_required
def download_attendance_csv(request):
    # Step 1: Auto-create missing "Absent" records first
    auto_mark_absent(request.user)

    # Step 2: Fetch all attendance for the logged-in user
    records = Attendance.objects.filter(user=request.user).order_by("-date")

    # Step 3: Prepare CSV response
    response = HttpResponse(
        content_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{request.user.username}_attendance.csv"'
        },
    )

    # Step 4: Write CSV header
    writer = csv.writer(response)
    writer.writerow(["Date", "Check In", "Check Out", "Status"])

    # Step 5: Write attendance rows
    for record in records:
        date_str = record.date.strftime("%Y-%m-%d")

        check_in_str = record.check_in.strftime("%H:%M:%S") if record.check_in else "--"
        check_out_str = record.check_out.strftime("%H:%M:%S") if record.check_out else "--"

        # Ensure consistency with database status field
        if hasattr(record, "status") and record.status:
            status = record.status
        else:
            if record.check_in and record.check_out:
                status = "Present"
            elif record.check_in and not record.check_out:
                status = "Absent (No Check-Out)"
            else:
                status = "Absent"

        writer.writerow([date_str, check_in_str, check_out_str, status])

    return response


# @login_required
# def save_face(request):
#     if request.method == "POST":
#         img_data = request.POST.get("image_data")
#         if img_data:
#             header, encoded = img_data.split(",", 1)
#             img_bytes = base64.b64decode(encoded)
#             nparr = np.frombuffer(img_bytes, np.uint8)
#             img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

#             # Save to pending folder
#             pending_dir = os.path.join(settings.MEDIA_ROOT, "pending_faces")
#             os.makedirs(pending_dir, exist_ok=True)
#             pending_path = os.path.join(pending_dir, f"{request.user.username}_pending.jpg")
#             cv2.imwrite(pending_path, img)

#             # Create or update pending request
#             from .models import FaceChangeRequest
#             FaceChangeRequest.objects.update_or_create(
#                 user=request.user,
#                 defaults={"status": "Pending", "new_face_path": pending_path}
#             )

#             return JsonResponse({"status": "success", "message": "Face submitted for admin approval"})


def help_support(request):
    return render(request, "help_support.html")

@login_required
@csrf_exempt
def face_add(request):
    import cv2
    user = request.user
    user_face = UserFace.objects.filter(user=user).first()
    has_face = bool(user_face and user_face.face_image)

    if request.method == "POST":
        import json, traceback
        data = json.loads(request.body)
        img_data = data.get("image_data")

        if not img_data:
            return JsonResponse({"status": "error", "message": "No image data received."})

        # Save uploaded image temporarily
        faces_dir = os.path.join(settings.MEDIA_ROOT, "faces", user.username)
        os.makedirs(faces_dir, exist_ok=True)
        new_face_path = os.path.join(faces_dir, f"{user.username}_temp.jpg")

        header, encoded = img_data.split(",", 1)
        img_bytes = base64.b64decode(encoded)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        cv2.imwrite(new_face_path, img)

        try:
            if not has_face:
                #   First-time registration: verify with master (default)
                master_face_path = os.path.join(settings.MEDIA_ROOT, "faces", user.username, f"{user.username}.jpg")

                result = DeepFace.verify(img1_path=master_face_path, img2_path=new_face_path, model_name="SFace")

                if result["verified"]:
                    final_face_path = os.path.join(faces_dir, f"{user.username}_new.jpg")
                    shutil.move(new_face_path, final_face_path)

                    UserFace.objects.update_or_create(
                        user=user,
                        defaults={"face_image": f"faces/{user.username}/{user.username}_new.jpg"}
                    )
                    user.has_face_data = True
                    user.save()

                    return JsonResponse({"status": "success", "message": "Face registered successfully!"})
                else:
                    os.remove(new_face_path)
                    return JsonResponse({"status": "error", "message": "Face did not match default face. Try again."})

            else:
                #   Already has a face — verify with default and new face
                master_face_path = os.path.join(settings.MEDIA_ROOT, user_face.face_image.name)
                previous_new_face_path = os.path.join(faces_dir, f"{user.username}_new.jpg")

                verified_with_master = False
                verified_with_new = False

                # Compare with master/default
                try:
                    result1 = DeepFace.verify(img1_path=master_face_path, img2_path=new_face_path, model_name="SFace")
                    verified_with_master = result1["verified"]
                except Exception as e:
                    print(f"Master comparison failed: {e}")

                # Compare with previously added _new image
                if os.path.exists(previous_new_face_path):
                    try:
                        result2 = DeepFace.verify(img1_path=previous_new_face_path, img2_path=new_face_path, model_name="SFace")
                        verified_with_new = result2["verified"]
                    except Exception as e:
                        print(f"New comparison failed: {e}")

                #   If match found — approve and replace _new
                if verified_with_master or verified_with_new:
                    final_face_path = os.path.join(faces_dir, f"{user.username}_new.jpg")
                    shutil.move(new_face_path, final_face_path)

                    UserFace.objects.update_or_create(
                        user=user,
                        defaults={"face_image": f"faces/{user.username}/{user.username}_new.jpg"}
                    )

                    FaceChangeRequest.objects.create(
                        user=user,
                        new_face_path=final_face_path,
                        status="Approved"
                    )

                    return JsonResponse({"status": "success", "message": " Face verified and updated successfully!"})

                #   No match — reject
                os.remove(new_face_path)
                FaceChangeRequest.objects.create(user=user, new_face_path=new_face_path, status="Rejected")

                return JsonResponse({"status": "error", "message": " Face did not match. Please try again."})

        except Exception as e:
            traceback.print_exc()
            if os.path.exists(new_face_path):
                os.remove(new_face_path)
            return JsonResponse({
                "status": "error",
                "message": f"Face verification failed: {str(e)}"
            })

    # GET request — display face info
    old_face_url = user_face.face_image.url if has_face else None
    approved_request = FaceChangeRequest.objects.filter(user=user, status="Approved").last()
    
    return render(request, "face_add.html", {
        "user": user,
        "old_face": old_face_url,
        "approved_request": approved_request,
        "has_face": has_face,
        "timestamp": datetime.now().timestamp(),  # 👈 important line
    })

    

# def face_add(request):
#     user = request.user

#     # Check if user already has a face
#     user_face = UserFace.objects.filter(user=user).first()
#     has_face = True if user_face and user_face.face_image else False

#     if request.method == "POST":
#         import json
#         data = json.loads(request.body)
#         img_data = data.get("image_data")
#         if img_data:
#             # Prepare image path
#             faces_dir = os.path.join(settings.MEDIA_ROOT, "faces", user.username)
#             os.makedirs(faces_dir, exist_ok=True)
#             img_path = os.path.join(faces_dir, f"{user.username}_1.jpg")

#             # Decode base64 image
#             header, encoded = img_data.split(",", 1)
#             img_bytes = base64.b64decode(encoded)
#             nparr = np.frombuffer(img_bytes, np.uint8)
#             img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
#             cv2.imwrite(img_path, img)

#             if not has_face:
#                 # First-time face → save directly to UserFace
#                 UserFace.objects.update_or_create(
#                     user=user,
#                     defaults={"face_image": f"faces/{user.username}/{user.username}_1.jpg"}
#                 )
#                 return JsonResponse({"status": "success", "message": "Face captured successfully!"})
#             else:
#                 # Existing user → create/update pending FaceChangeRequest
#                 FaceChangeRequest.objects.update_or_create(
#                     user=user,
#                     defaults={"status": "Pending", "new_face_path": img_path}
#                 )
#                 return JsonResponse({"status": "success", "message": "Face submitted for admin approval."})

#         return JsonResponse({"status": "error", "message": "No image data received."})

#     # GET request → render template
#     old_face_url = user_face.face_image.url if has_face else None

#     # Check if there’s a pending change request
#     pending_obj = FaceChangeRequest.objects.filter(user=user, status="Pending").first()
#     pending_face_url = pending_obj.new_face_path.replace(settings.MEDIA_ROOT, settings.MEDIA_URL) if pending_obj else None

#     return render(request, "face_add.html", {
#         "user": user,
#         "old_face": old_face_url,
#         "pending_face": pending_face_url,
#         "has_face": has_face,
#     })

# Store in settings.py for safety


# Render the chat page
def chatbot_view(request):
    return render(request, "chatbot.html")

def upload_master_data_view(self, request):
    """
    Handles upload, display, edit, delete, and update of master data CSV files.
    """
    # =============================
    # 1️⃣ Handle New File Upload
    # =============================
    if request.method == "POST" and request.FILES.get("file"):
        file = request.FILES["file"]

        fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, "master_uploads"))
        filename = fs.save(file.name, file)
        file_path = fs.path(filename)

        # Decode safely (UTF-8 or fallback)
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        try:
            decoded_file = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            decoded_file = file_bytes.decode("latin-1")

        reader = csv.DictReader(io.StringIO(decoded_file, newline=''))
        rows = list(reader)

        # Save record of upload
        MasterUserRecord.objects.create(
            uploaded_by=request.user,
            file=f"master_uploads/{filename}",
            total_rows=len(rows),
        )

        # Render preview
        context = {
            **self.each_context(request),
            "rows": rows,
            "filename": filename,
            "columns": reader.fieldnames,
        }
        return render(request, "admin/master_data_preview.html", context)

    # =============================
    # 2️⃣ Handle AJAX Save (Edit/Add/Delete)
    # =============================
    if request.method == "POST" and request.headers.get("X-Requested-With") == "XMLHttpRequest":
        data = json.loads(request.body)
        filename = data.get("filename")
        rows = data.get("rows", [])
        file_path = os.path.join(settings.MEDIA_ROOT, "master_uploads", filename)

        # Rewrite CSV
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["username", "enrollment_no", "email", "user_type", "face_path"])
            writer.writeheader()
            for r in rows:
                writer.writerow(r)

        # Sync changes to DB
        created_count, updated_count = 0, 0
        for r in rows:
            username = (r.get("username") or "").strip()
            enrollment = (r.get("enrollment_no") or "").strip()
            email = (r.get("email") or "").strip()
            user_type = (r.get("user_type") or "student").strip()
            face_path = (r.get("face_path") or "").strip()

            if not enrollment or not email:
                continue

            user, created = CustomUser.objects.update_or_create(
                enrollment_no=enrollment,
                defaults={
                    "username": username,
                    "email": email,
                    "user_type": user_type,
                    "is_approved": True,
                    "has_face_data": bool(face_path),
                },
            )

            if face_path:
                UserFace.objects.update_or_create(
                    user=user,
                    defaults={"face_image": face_path}
                )

            if created:
                created_count += 1
            else:
                updated_count += 1

        return JsonResponse({
            "status": "success",
            "created": created_count,
            "updated": updated_count
        })

    # =============================
    # 3️⃣ Render Recent Uploads List
    # =============================
    uploads = MasterUserRecord.objects.all().order_by("-created_at")[:10]
    context = {
        **self.each_context(request),
        "uploads": uploads,
    }
    return render(request, "admin/upload_master_data.html", context)

import json
import google.generativeai as genai
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

GEMINI_API_KEY = ""
genai.configure(api_key=GEMINI_API_KEY)

@csrf_exempt
def chatbot_api(request):
    if request.method == "POST":
        data = json.loads(request.body)
        question = data.get("question")

        # Giveing the Role to ChatBot
        context_prompt = (
            "You are AttendEase Assistant.\n"
            "Rules:\n"
            "- Answer only related to AttendEase project or its creator.\n"
            "- If unrelated, reply: 'I can only help with AttendEase-related questions.'\n"
            "- Keep answers short & formal.\n"
            "- Use bullet points where possible.\n\n"
            "Examples:\n"
            "Q: What technologies does project use?\n"
            "A:\n"
            "- Backend: Django\n"
            "- Frontend: HTML, CSS, JS\n"
            "- Database: SQLite\n"
            "- AI: OpenCV + Deepface\n\n"
            "Q: How is attendance report generated?\n"
            "A:\n"
            "- Data stored in SQLite\n"
            "- Summarized by date\n"
            "- Exportable in CSV/PDF\n\n"
            "Q: Who created AttendEase?\n"
            "A: Created by developer Yash & Tushya, BCA Sem-5, Study at Ganpat University\n\n"
        )

        try:
            model = genai.GenerativeModel("gemini-2.5-flash")

            response = model.generate_content(
                context_prompt + f"\nQ: {question}\nA:"
            )

            answer = response.text.strip()
            return JsonResponse({"answer": answer})

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Invalid request"}, status=400)


def admin_view(request):
    return render(request, "admin.html")

def index(request):
    template = loader.get_template("index.html")
    return HttpResponse(template.render({}, request))

User = get_user_model()

def register(request):
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            enrollment_no = form.cleaned_data.get("enrollment_no")
            username = form.cleaned_data.get("username")
            password = form.cleaned_data.get("password")
            user_type = form.cleaned_data.get("user_type")

            #   Check if this user exists in the MasterUserRecord (ignore email)
            try:
                master_user = MasterUserRecord.objects.get(
                    enrollment_no=enrollment_no,
                    user_type=user_type
                )

                #   Create a CustomUser if not exists
                user, created = CustomUser.objects.get_or_create(
                    enrollment_no=enrollment_no,
                    defaults={
                        "username": username,
                        "email": master_user.email,  # use master email
                        "user_type": user_type,
                        "is_active": True,
                        "is_approved": True,
                    }
                )

                #   Set password and save
                user.set_password(password)
                user.save()

                #   Sync default face image from master
                if master_user.face_image:
                    UserFace.objects.update_or_create(
                        user=user,
                        defaults={"face_image": master_user.face_image}
                    )
                    user.has_face_data = True
                    user.save()

                messages.success(request, " Verified from master list! Your account is now active.")
                return redirect("userlogin")

            except MasterUserRecord.DoesNotExist:
                messages.error(request, " Not found in authorized list. Contact admin.")
                return redirect("register")

    else:
        form = RegistrationForm()

    return render(request, "register.html", {"form": form})


# def register(request):
#     if request.method == "POST":
#         form = RegistrationForm(request.POST)
#         if form.is_valid():
#             user = form.save(commit=False)
#             user.set_password(form.cleaned_data["password"])
#             user.is_active = True
#             user.is_approved = False
#             user.save()
#             messages.success(request, "Registration request sent. Wait for admin approval.")
#             return redirect('userlogin')
#     else:
#         form = RegistrationForm()

#     return render(request, "register.html", {"form": form}) 

def login_view(request):
    if request.method == "POST":
        form = CustomLoginForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get("username")
            password = form.cleaned_data.get("password")
            user = authenticate(request, username=username, password=password)

            if user is not None:
                # Auto-block any unauthorized or unapproved user
                if not user.is_approved:
                    messages.error(request, "⚠ Your account isn't verified in master list.")
                    return redirect("userlogin")

                login(request, user)
                messages.success(request, f"Welcome, {user.username} 👋")
                return redirect("userdash")
            else:
                messages.error(request, "Invalid username or password.")
    else:
        form = CustomLoginForm()

    return render(request, "userlogin.html", {"form": form})


# def login_view(request):
#     if request.method == "POST":
#         form = CustomLoginForm(request, data=request.POST)
#         if form.is_valid():
#             username = form.cleaned_data.get('username')
#             password = form.cleaned_data.get('password')
#             user = authenticate(request, username=username, password=password)

#             if user is not None:
#                 if user.is_approved: 
#                     login(request, user)
#                     messages.success(request, f"Welcome, {user.username}!")
#                     return redirect('userdash')
#                 else:
#                     messages.error(request, "Your account is pending admin approval.")
#             else:
#                 messages.error(request, "Invalid username or password.")
#     else:
#         form = CustomLoginForm()

#     return render(request, "userlogin.html", {"form": form})



def contact_admin(request):
    return render(request, "contact_admin.html")



@login_required
def userdash_view(request):
    from .models import Attendance, MonthlyAttendance, LeaveRequest
    from datetime import timedelta, date
    from django.utils.timezone import localtime
    from django.utils import timezone
    import calendar

    user = request.user
    today = date.today()

    #   Always define month and year first
    month = int(request.GET.get('month', today.month))
    year = int(request.GET.get('year', today.year))

    #   Update monthly summary before fetching
    from .utils import update_monthly_attendance
    update_monthly_attendance(user, today)

    monthly_data = MonthlyAttendance.objects.filter(user=user, month=month, year=year).first()

    total_days = monthly_data.total_days if monthly_data else 0
    present_days = monthly_data.present_days if monthly_data else 0
    absent_days = monthly_data.absent_days if monthly_data else 0
    leave_days = monthly_data.leave_days if monthly_data else 0
    holidays = monthly_data.holidays if monthly_data else 0
    percentage = monthly_data.percentage if monthly_data else 0.0

    #   Weekly Check-in Trends
    start_of_week = today - timedelta(days=today.weekday())  # Monday
    end_of_week = start_of_week + timedelta(days=6)
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    weekly_records = Attendance.objects.filter(
        user=user,
        date__range=[start_of_week, end_of_week],
        check_in__isnull=False
    )

    weekday_times = {i: [] for i in range(7)}
    for record in weekly_records:
        if record.check_in:
            weekday_index = record.date.weekday()
            checkin_time = record.check_in

            # Some check_in fields are `datetime.time` objects — convert to datetime safely
            if isinstance(checkin_time, time):
                checkin_time = datetime.combine(record.date, checkin_time)
            
            # If naive datetime, make it aware before using localtime
            if timezone.is_naive(checkin_time):
                checkin_time = timezone.make_aware(checkin_time)
            
            try:
                local_time = timezone.localtime(checkin_time)
            except Exception:
                local_time = checkin_time  # fallback, just use original
            
            weekday_times[weekday_index].append(local_time.hour + local_time.minute / 60.0)

    # Average per weekday
    avg_checkin_times = []
    for i in range(7):
        if weekday_times[i]:
            avg_hour = sum(weekday_times[i]) / len(weekday_times[i])
            avg_checkin_times.append(round(avg_hour, 2))
        else:
            avg_checkin_times.append(0)

    def format_time(hour_float):
        if hour_float == 0:
            return "--"
        hour = int(hour_float)
        minute = int((hour_float - hour) * 60)
        ampm = "AM" if hour < 12 else "PM"
        hour_display = hour if hour <= 12 else hour - 12
        return f"{hour_display}:{minute:02d} {ampm}"

    formatted_times = [format_time(t) for t in avg_checkin_times]
    weekly_chart = list(zip(day_names, avg_checkin_times, formatted_times))

    #   Calendar logic with attendance + leave + weekend info
    cal = calendar.Calendar(firstweekday=0)
    month_days = list(cal.itermonthdates(year, month))

    attendance_records = Attendance.objects.filter(
        user=user,
        date__year=year,
        date__month=month
    )

    attendance_map = {}
    for rec in attendance_records:
        status = rec.status or "Absent"
        check_in = rec.check_in.strftime("%H:%M") if rec.check_in else "--"
        check_out = rec.check_out.strftime("%H:%M") if rec.check_out else "--"
        attendance_map[rec.date] = {
            "status": status,
            "check_in": check_in,
            "check_out": check_out,
        }

    leave_reqs = LeaveRequest.objects.filter(user=user, status="Approved")
    for leave in leave_reqs:
        current = leave.start_date
        while current <= leave.end_date:
            if current.month == month and current.year == year:
                attendance_map[current] = {"status": "Leave", "check_in": "--", "check_out": "--"}
            current += timedelta(days=1)

    for d in month_days:
        if d.weekday() in (5, 6) and d not in attendance_map:
            attendance_map[d] = {"status": "Holiday", "check_in": "--", "check_out": "--"}

    weeks = []
    week = []
    for d in month_days:
        if d.month != month:
            week.append({"day": "", "status": "empty", "details": ""})
        else:
            info = attendance_map.get(d, {"status": "Absent", "check_in": "--", "check_out": "--"})
            tooltip = f"{info['status']} | In: {info['check_in']} | Out: {info['check_out']}"
            week.append({
                "day": d.day,
                "status": info["status"],
                "details": tooltip,
                "is_today": (d == today),
            })
        if len(week) == 7:
            weeks.append(week)
            week = []
            
        #   Today's Attendance Summary
    try:
        today_record = Attendance.objects.filter(user=user, date=today).first()
        if today_record:
            if today_record.check_in and today_record.check_out:
                check_in_str = today_record.check_in.strftime("%I:%M %p")
                check_out_str = today_record.check_out.strftime("%I:%M %p")
                today_status = f"➡️ Checked-in at {check_in_str} | ⬅️ Checked-out at {check_out_str}"
            elif today_record.check_in:
                check_in_str = today_record.check_in.strftime("%I:%M %p")
                today_status = f"➡️ Checked-in at {check_in_str}"
            else:
                today_status = "  Not yet checked in."
        else:
            today_status = "📅 No attendance record for today."
    except Exception:
        today_status = "⚠️ Unable to fetch today's status."


    #   Build context
    context = {
        "today_status": today_status,
        "user": user,
        "cal_year": year,
        "cal_month": month,
        "cal_month_name": calendar.month_name[month],
        "today_day": today.day,
        "today_month": today.month,
        "today_year": today.year,
        "total_days": total_days,
        "present_days": present_days,
        "absent_days": absent_days,
        "leave_days": leave_days,
        "holidays": holidays,
        "percentage": round(percentage, 2),
        "weekly_chart": weekly_chart,
        "weeks": weeks,
    }

    #   AJAX calendar update
    if request.GET.get("ajax") == "1":
        from django.template.loader import render_to_string
        html = render_to_string("calendar_cells.html", context)
        return HttpResponse(html)

    return render(request, "userdash.html", context)


def logout(request):
    return render(request, "userlogin.html")

def userprofile_view(request):
    return render(request, "user_profile.html" , {"user": request.user})

@login_required
def change_password(request):
    if request.method == "POST":
        new_password = request.POST.get("newPassword")
        confirm_password = request.POST.get("confirmNewPassword")

        if new_password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return redirect("change_password")

        try:
            password_validation.validate_password(new_password, request.user)
        except ValidationError as e:
            for err in e.messages:
                messages.error(request, err)
            return redirect("change_password")

        # Update password
        request.user.set_password(new_password)
        request.user.save()
        update_session_auth_hash(request, request.user)

        messages.success(request, "Password updated successfully.")
        return redirect("change_password")  # or wherever you want

    return render(request, 'user_profile.html')


@login_required
def leave_request_view(request):
    if request.method == "POST":
        start_date = request.POST.get("start_date")
        end_date = request.POST.get("end_date")
        leave_type = request.POST.get("leave_type")
        reason = request.POST.get("reason")

        LeaveRequest.objects.create(
            user=request.user,
            start_date=start_date,
            end_date=end_date,
            leave_type=leave_type,
            reason=reason,
            status="Pending"
        )

        messages.success(request, "Leave request submitted successfully  ")
        return redirect("leave_request")  

    leave_requests = LeaveRequest.objects.filter(user=request.user).order_by("-created_at")
    return render(request, "leave_request.html", {"leave_requests": leave_requests})
