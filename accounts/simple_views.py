import os
import cv2
import base64
import threading
import numpy as np
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from datetime import datetime
import logging

from .models import CustomUser, UserFaceEmbedding
from .api_service import check_in_user, check_out_user
from .utils import compute_face_embedding, find_best_match

logger = logging.getLogger(__name__)

# Face database path
FACE_DB = os.path.join(settings.MEDIA_ROOT, "faces")
os.makedirs(FACE_DB, exist_ok=True)

# ---------------------------------------------------------------------------
# In-memory embedding cache
# Rebuilt from DB on first recognition request and after any face-data change.
# Keyed by api_user_id; values are pre-averaged embedding vectors.
# ---------------------------------------------------------------------------
_embedding_cache = None   # {api_user_id: [avg_embedding]}
_user_map_cache = None    # {api_user_id: display_name}
_user_obj_cache = None    # {api_user_id: CustomUser}
_cache_lock = threading.Lock()


def _invalidate_embedding_cache():
    global _embedding_cache, _user_map_cache, _user_obj_cache
    with _cache_lock:
        _embedding_cache = None
        _user_map_cache = None
        _user_obj_cache = None


def _get_embedding_cache():
    global _embedding_cache, _user_map_cache, _user_obj_cache
    with _cache_lock:
        if _embedding_cache is not None:
            return _embedding_cache, _user_map_cache, _user_obj_cache

        user_embeddings = {}
        user_map = {}
        user_obj_map = {}
        users_with_faces = CustomUser.objects.filter(
            has_face_data=True, api_user_id__isnull=False
        ).prefetch_related('face_embeddings')

        for user in users_with_faces:
            embeddings = [e.embedding for e in user.face_embeddings.all()]
            if embeddings:
                avg_embedding = np.mean(embeddings, axis=0).tolist()
                user_embeddings[user.api_user_id] = [avg_embedding]
                user_map[user.api_user_id] = user.get_display_name()
                user_obj_map[user.api_user_id] = user

        _embedding_cache = user_embeddings
        _user_map_cache = user_map
        _user_obj_cache = user_obj_map
        logger.info(f"Embedding cache built: {len(user_embeddings)} users")
        return _embedding_cache, _user_map_cache, _user_obj_cache


def decode_base64_image(base64_string):
    """Decode base64 image string to OpenCV format"""
    try:
        if ',' in base64_string:
            base64_string = base64_string.split(',')[1]
        
        img_data = base64.b64decode(base64_string)
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        logger.error(f"Error decoding base64 image: {str(e)}")
        return None


def home(request):
    """Main landing page with options"""
    return render(request, 'simple_home.html')


def api_users(request):
    """Return list of all users with face data, for the name selection overlay."""
    users = CustomUser.objects.filter(
        has_face_data=True,
        api_user_id__isnull=False
    ).order_by('username').values('api_user_id', 'username')
    return JsonResponse({'users': [
        {'api_user_id': u['api_user_id'],
         'display_name': u['username'].replace('_', ' ').title()}
        for u in users
    ]})


def select_user_for_registration(request):
    """Select user for face registration from dropdown"""
    # Get all users with API IDs
    all_users = CustomUser.objects.filter(api_user_id__isnull=False).order_by('first_name', 'last_name', 'email')
    
    selected_user = None
    user_id = request.GET.get('user_id', '').strip()
    
    if user_id:
        try:
            selected_user = CustomUser.objects.get(api_user_id=user_id)
        except CustomUser.DoesNotExist:
            pass
    
    context = {
        'all_users': all_users,
        'selected_user': selected_user,
        'title': 'Select User for Face Registration'
    }
    return render(request, 'select_user.html', context)


def register_face(request, user_id):
    """
    Face registration interface - capture 25+ images
    """
    try:
        user = CustomUser.objects.get(api_user_id=user_id)
    except CustomUser.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)
    
    user_folder = os.path.join(FACE_DB, user.username)
    current_count = len(os.listdir(user_folder)) if os.path.exists(user_folder) else 0
    
    # Check if user already has face data registered
    has_existing_face = user.has_face_data and current_count > 0
    
    context = {
        'user': user,
        'current_count': current_count,
        'required_count': 25,
        'has_existing_face': has_existing_face,
        'title': f'Register Face for {user.username}'
    }
    return render(request, 'register_face.html', context)


@csrf_exempt
def delete_face_data(request, user_id):
    """Delete all existing face data for a user to allow re-enrollment."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    try:
        user = CustomUser.objects.get(api_user_id=user_id)
        user_folder = os.path.join(FACE_DB, user.username)
        if os.path.exists(user_folder):
            import shutil
            shutil.rmtree(user_folder)
        UserFaceEmbedding.objects.filter(user=user).delete()
        user.has_face_data = False
        user.face_images_count = 0
        user.save()
        _invalidate_embedding_cache()
        return JsonResponse({'success': True})
    except CustomUser.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'})
    except Exception as e:
        logger.error(f"Error deleting face data: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})



@csrf_exempt
def save_faces_batch(request):
    """
    Batch face registration endpoint.
    Accepts all captured frames at once and processes them synchronously.
    Returns aggregate counts: saved, embedded, failed.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})

    try:
        import json
        from .models import UserFaceEmbedding

        data = json.loads(request.body)
        user_id = data.get('user_id')
        images = data.get('images')

        if not user_id:
            return JsonResponse({'success': False, 'error': 'Missing user_id'})
        if not images or not isinstance(images, list):
            return JsonResponse({'success': False, 'error': 'Missing or invalid images list'})
        if len(images) == 0:
            return JsonResponse({'success': False, 'error': 'No images provided'})

        # Cap at 25 to prevent abuse
        images = images[:25]

        user = CustomUser.objects.get(api_user_id=user_id)

        user_folder = os.path.join(FACE_DB, user.username)
        os.makedirs(user_folder, exist_ok=True)

        saved_count = 0
        embedded_count = 0
        failed_count = 0

        # Compute the starting offset once so filenames stay unique even if some frames are deleted
        existing_at_start = len([f for f in os.listdir(user_folder) if f.lower().endswith('.jpg')])

        for idx, image_data in enumerate(images):
            frame_num = idx + 1

            img = decode_base64_image(image_data)
            if img is None:
                logger.warning(f"Batch [{user.username}] frame {frame_num}: failed to decode")
                failed_count += 1
                continue

            img_filename = f"{user.username}_{existing_at_start + frame_num}.jpg"
            img_path = os.path.join(user_folder, img_filename)

            if not cv2.imwrite(img_path, img):
                logger.error(f"Batch [{user.username}] frame {frame_num}: cv2.imwrite failed")
                failed_count += 1
                continue

            saved_count += 1

            embedding = compute_face_embedding(img_path, model_name="SFace")

            if embedding:
                relative_path = os.path.join("faces", user.username, img_filename)
                try:
                    UserFaceEmbedding.objects.create(
                        user=user,
                        image_path=relative_path,
                        embedding=embedding,
                        model_name="SFace"
                    )
                    embedded_count += 1
                    logger.info(f"Batch [{user.username}] frame {frame_num}: embedding saved")
                except Exception as db_err:
                    logger.warning(f"Batch [{user.username}] frame {frame_num}: DB error: {db_err}")
            else:
                logger.warning(f"Batch [{user.username}] frame {frame_num}: no face detected, removing image")
                if os.path.exists(img_path):
                    os.remove(img_path)
                saved_count -= 1
                failed_count += 1

        MIN_EMBEDDINGS = 10
        if embedded_count < MIN_EMBEDDINGS:
            logger.warning(f"Batch [{user.username}]: only {embedded_count} embeddings, below minimum {MIN_EMBEDDINGS}")
            return JsonResponse({
                'success': True,
                'saved_count': saved_count,
                'embedded_count': embedded_count,
                'failed_count': failed_count,
                'below_minimum': True,
                'minimum_required': MIN_EMBEDDINGS
            })

        user.has_face_data = True
        user.face_images_count = UserFaceEmbedding.objects.filter(user=user).count()
        user.save()

        _invalidate_embedding_cache()
        logger.info(f"Batch registration complete [{user.username}]: saved={saved_count}, embedded={embedded_count}, failed={failed_count}")

        return JsonResponse({
            'success': True,
            'saved_count': saved_count,
            'embedded_count': embedded_count,
            'failed_count': failed_count,
            'below_minimum': False
        })

    except CustomUser.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'})
    except Exception as e:
        logger.error(f"Error in save_faces_batch: {str(e)}", exc_info=True)
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def recognize_and_mark_attendance(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})

    try:
        import json
        data = json.loads(request.body)
        image_data = data.get('image')
        action = data.get('action', 'check_in')  # check_in or check_out
        selected_user_id = data.get('selected_user_id')

        if not image_data:
            return JsonResponse({'success': False, 'error': 'No image data provided'})

        if not selected_user_id:
            return JsonResponse({'success': False, 'error': 'No user selected'})

        # Decode image
        img = decode_base64_image(image_data)
        if img is None:
            return JsonResponse({'success': False, 'error': 'Failed to decode image'})

        # Pass numpy array directly — no temp file needed
        query_embedding = compute_face_embedding(img, model_name="SFace", fast_mode=True)

        if query_embedding is None:
            return JsonResponse({
                'success': False,
                'error': 'Could not detect face in the image. Please try again with better lighting.'
            })

        user_embeddings, user_map, user_obj_map = _get_embedding_cache()

        DISTANCE_THRESHOLD = 0.40

        logger.info(f"FACE RECOGNITION REQUEST - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Action: {action.upper()}")
        logger.info(f"Selected user ID: {selected_user_id}")

        user_id, distance, confidence, log_match = find_best_match(
            query_embedding,
            user_embeddings,
            threshold=DISTANCE_THRESHOLD,
            user_map=user_map,
        )

        if user_id is None:
            log_match()
            logger.warning("RECOGNITION FAILED - No matching face found above threshold")
            return JsonResponse({
                'success': False,
                'error': 'Face not recognized. Please try again with better lighting.'
            })

        # Validate that recognized user matches the selected user
        if str(user_id) != str(selected_user_id):
            log_match()
            recognized_name = user_map.get(user_id, str(user_id))
            selected_name = user_map.get(int(selected_user_id), str(selected_user_id))
            logger.warning(
                f"MISMATCH - Recognized: {recognized_name} (id={user_id}), "
                f"Selected: {selected_name} (id={selected_user_id})"
            )
            return JsonResponse({
                'success': False,
                'error': 'Face does not match selected user. Please select your own name and try again.'
            })

        log_match()

        recognized_user = user_obj_map.get(user_id)
        if recognized_user is None:
            return JsonResponse({'success': False, 'error': 'User data error'})

        # Fire the external API call in a background thread so the user gets
        # an instant response right after face recognition passes.
        api_fn = check_in_user if action == 'check_in' else check_out_user
        api_uid = recognized_user.api_user_id
        display_name = recognized_user.get_display_name()

        def _post_attendance():
            result = api_fn(api_uid)
            if result['success']:
                logger.info(f"Async attendance API OK for {display_name}: {result.get('message')}")
            else:
                logger.error(f"Async attendance API FAILED for {display_name}: {result.get('message')}")

        threading.Thread(target=_post_attendance, daemon=True).start()

        action_label = 'Checked In' if action == 'check_in' else 'Checked Out'
        return JsonResponse({
            'success': True,
            'status': 'success',
            'message': f'{display_name} {action_label} successfully',
            'user': {
                'username': recognized_user.username,
                'display_name': display_name,
                'api_id': api_uid
            },
            'action': action,
            'confidence': f"{confidence:.2f}%",
            'distance': f"{distance:.4f}"
        })

    except Exception as e:
        logger.error(f"Error in face recognition: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Recognition error: {str(e)}'
        })


