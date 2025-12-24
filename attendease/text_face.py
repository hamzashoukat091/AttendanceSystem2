import os
from django.conf import settings
from django.http import JsonResponse
from deepface import DeepFace

def verify_faces(request):
    try:
        # Pick two sample images from your face DB
        img1 = os.path.join(settings.MEDIA_ROOT, "faces", "FCA@123", "FCA@123_2.jpg")
        img2 = os.path.join(settings.MEDIA_ROOT, "faces", "rk", "rk_1.jpg")

        if not os.path.exists(img1) or not os.path.exists(img2):
            return JsonResponse({"status": "error", "message": "Sample images not found in MEDIA_ROOT/faces/FCA@123/ and MEDIA_ROOT/rk/rk_1.jpg/"})

        result = DeepFace.verify(img1, img2, model_name="SFace", detector_backend="opencv")
        return JsonResponse({
            "status": "success",
            "verified": result["verified"],
            "distance": result["distance"]
        })
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)})
