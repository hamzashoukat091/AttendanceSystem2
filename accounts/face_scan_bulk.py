

# @csrf_exempt
# def mark_attendance_ajax(request):
#     if request.method == "POST":
#         import json
#         data = json.loads(request.body)
#         image_data = data.get("image_data")
#         if not image_data:
#             return JsonResponse({"status": "error", "message": "No image received"})

#         frame = decode_base64_image(image_data)
#         username = recognize_face(frame)
#         if username:
#             status, time = mark_user_attendance(username)
#             return JsonResponse({"status": "success", "username": username, "type": status, "time": time})
#         else:
#             return JsonResponse({"status": "error", "message": "No face detected or unclear. Please try again."})

#     return JsonResponse({"status": "error", "message": "Invalid request"})

# @login_required
# def attendance_report(request):
#     from .models import Attendance
#     records = Attendance.objects.filter(user=request.user).order_by("-date")
#     return render(request, "attendance_report.html", {"records": records})

# @login_required
# def face_add(request):
#     return render(request, "face_add.html")


# @login_required
# def save_face(request):
#     if request.method == "POST":
#         img_data = request.POST.get("image_data")
#         if img_data:
#             header, encoded = img_data.split(",", 1)
#             img_bytes = base64.b64decode(encoded)
#             nparr = np.frombuffer(img_bytes, np.uint8)
#             img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

#             # Save and encode face
#             add_face_image(request.user.username, img)
#             return redirect("userdash")
#     return JsonResponse({"status": "error", "message": "No image received."})



import os
import pickle
import numpy as np
from datetime import datetime
from django.conf import settings
from deepface import DeepFace
from .models import Attendance, CustomUser

# -----------------------------
# Paths & Directories
# -----------------------------
FACE_DB = os.path.join(settings.MEDIA_ROOT, "faces")
os.makedirs(FACE_DB, exist_ok=True)

TEMP_IMAGE = os.path.join(settings.MEDIA_ROOT, "temp.jpg")

# Optional embeddings file (if you want to save embeddings)
EMBEDDINGS_FILE = os.path.join(settings.MEDIA_ROOT, "face_embeddings.pkl")

# -----------------------------
# Add Face Image
# -----------------------------
def add_face_image(username, img):
    import cv2
    """
    Saves user's face image and updates embeddings.
    img = OpenCV frame (BGR)
    """
    user_folder = os.path.join(FACE_DB, username)
    os.makedirs(user_folder, exist_ok=True)

    # Save image with incremental name
    img_count = len(os.listdir(user_folder))
    img_path = os.path.join(user_folder, f"{username}_{img_count+1}.jpg")
    cv2.imwrite(img_path, img)

    print(f"[Face Added] Saved image for user: {username}")
    return img_path

# -----------------------------
# Recognize Face
# -----------------------------
def recognize_face(frame, threshold=0.45):
    import cv2
    """
    Recognize face from frame using DeepFace.verify against stored images.
    Returns username if match found.
    """
    # Save frame temporarily
    cv2.imwrite(TEMP_IMAGE, frame)

    best_match = None
    best_distance = 10.0

    for username in os.listdir(FACE_DB):
        user_folder = os.path.join(FACE_DB, username)
        if not os.path.isdir(user_folder):
            continue

        for img_file in os.listdir(user_folder):
            db_img_path = os.path.join(user_folder, img_file)
            try:
                result = DeepFace.verify(
                    TEMP_IMAGE,
                    db_img_path,
                    model_name="SFace",
                    detector_backend="mtcnn",  # more reliable than opencv
                    enforce_detection=True
                )
                distance = result["distance"]
                verified = result["verified"]

                if verified and distance < best_distance:
                    best_distance = distance
                    best_match = username

            except Exception as e:
                print(f"[Verify Error] {db_img_path}: {e}")
                continue

    if best_match and best_distance < threshold:
        print(f"[Recognize] Best match: {best_match} (distance={best_distance:.3f})")
        return best_match

    print("[Recognize] No face detected or match unclear.")
    return None

# -----------------------------
# Mark Attendance
# -----------------------------
def mark_user_attendance(user):
    """
    Records attendance for a given user.
    Returns status ('checkin', 'checkout', 'done') and timestamp.
    """
    today = datetime.today().date()
    now_time = datetime.now().time()

    try:
        attendance = Attendance.objects.get(user__username=user, date=today)
    except Attendance.DoesNotExist:
        attendance = None

    if not attendance:
        # First check-in
        Attendance.objects.create(user=CustomUser.objects.get(username=user), date=today, check_in=now_time)
        return "checkin", now_time.strftime("%H:%M:%S")
    elif attendance and not attendance.check_out:
        # Check-out
        attendance.check_out = now_time
        attendance.save()
        return "checkout", now_time.strftime("%H:%M:%S")
    else:
        # Already checked in & out
        return "done", (attendance.check_out or attendance.check_in).strftime("%H:%M:%S")

# -----------------------------
# Decode Base64 Image
# -----------------------------
import base64
import numpy as np

def decode_base64_image(data_url):
    import cv2
    """
    Converts base64 string from JS to OpenCV BGR image.
    """
    header, encoded = data_url.split(",", 1)
    data = base64.b64decode(encoded)
    nparr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    return img
