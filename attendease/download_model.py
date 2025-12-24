import os
import requests

MODEL_PATH = "media/deepface_models/.deepface/weights/face_recognition_sface_2021dec.onnx"
URL = "https://github.com/deepinsight/insightface/releases/download/v2.0.0/sface.onnx"

def download_sface():
    if not os.path.exists(MODEL_PATH):
        print("Downloading SFace model...")
        response = requests.get(URL)
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            f.write(response.content)
        print("Download complete!")
