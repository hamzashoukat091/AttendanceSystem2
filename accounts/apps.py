from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        import accounts.signal
        self._warmup_deepface()
        self._start_retry_worker()

    def _start_retry_worker(self):
        """Start the attendance API retry worker in a background thread at app startup."""
        import threading

        def _launch():
            try:
                from accounts.retry_worker import start_retry_worker
                start_retry_worker()
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to start retry worker: {e}")

        threading.Thread(target=_launch, daemon=True, name="retry-worker-launcher").start()

    def _warmup_deepface(self):
        """
        Pre-load DeepFace + SFace model in a background thread at startup.
        Without this, the very first recognition request pays a 10-30s penalty
        while TensorFlow initializes and loads model weights into memory.
        """
        import threading

        def _load():
            try:
                import os
                import numpy as np
                import cv2
                from django.conf import settings
                from deepface import DeepFace

                logger.info("DeepFace warm-up: starting model pre-load...")

                # Create a tiny blank image — enough to trigger model initialization
                # without needing a real face photo
                dummy_path = os.path.join(settings.MEDIA_ROOT, "_warmup_dummy.jpg")
                blank = np.zeros((112, 112, 3), dtype=np.uint8)
                cv2.imwrite(dummy_path, blank)

                try:
                    # This call will fail (no face in blank image) but it forces
                    # DeepFace to import, initialize TensorFlow, and load SFace + MTCNN weights
                    DeepFace.represent(
                        img_path=dummy_path,
                        model_name="SFace",
                        detector_backend="mtcnn",
                        enforce_detection=True
                    )
                except Exception:
                    pass  # Expected — blank image has no face

                try:
                    os.remove(dummy_path)
                except Exception:
                    pass

                logger.info("DeepFace warm-up: SFace model ready.")
            except Exception as e:
                logger.warning(f"DeepFace warm-up failed (non-fatal): {e}")

        t = threading.Thread(target=_load, daemon=True, name="deepface-warmup")
        t.start()

