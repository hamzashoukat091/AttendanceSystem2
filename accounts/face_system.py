import os

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

    # -------------------------------
    # Update has_face_data for first image
    # -------------------------------
    from .models import CustomUser  # make sure import is here or at top
    user = CustomUser.objects.get(username=username)
    if not user.has_face_data:
        user.has_face_data = True
        user.save()
        print(f"[Database] Updated has_face_data for user: {username}")

    return img_path
# -----------------------------
# Recognize Logged-in User Face Only
# -----------------------------
import os
from deepface import DeepFace
from django.conf import settings
from .models import CustomUser

def recognize_logged_in_user(frame, username, threshold=0.45):
    import cv2
    """
    Recognize only the logged-in user's approved face images.
    Ignores pending/unapproved images.
    Returns username if match found, else None.
    """
    # Paths
    FACE_DB = os.path.join(settings.MEDIA_ROOT, "faces")
    PENDING_DB = os.path.join(settings.MEDIA_ROOT, "pending_faces")
    user_folder = os.path.join(FACE_DB, username)
    
    # Check if user has face data
    if not os.path.exists(user_folder) or len(os.listdir(user_folder)) == 0:
        return None

    # Save current frame temporarily
    temp_image = os.path.join(settings.MEDIA_ROOT, "temp.jpg")
    cv2.imwrite(temp_image, frame)

    best_match = None
    best_distance = 10.0

    # Loop through user's approved images
    for img_file in os.listdir(user_folder):
        db_img_path = os.path.join(user_folder, img_file)
        try:
            result = DeepFace.verify(
                temp_image,
                db_img_path,
                model_name="SFace",
                detector_backend="mtcnn",
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

    # Ignore pending images (new uploads waiting for admin approval)
    if os.path.exists(PENDING_DB):
        pending_files = [
            f for f in os.listdir(PENDING_DB) 
            if f.startswith(username)
        ]
        if pending_files:
            print(f"[Info] User has pending unapproved images: {pending_files}")

    if best_match and best_distance < threshold:
        return best_match

    print("[Recognize] No match or unclear face for logged-in user.")
    return None
# -----------------------------
# Decode Base64 Image
# -----------------------------
import base64

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
