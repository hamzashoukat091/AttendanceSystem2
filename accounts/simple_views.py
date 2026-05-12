import os
import cv2
import base64
import json
import threading
import numpy as np
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from datetime import datetime, time as _time, timedelta
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

    # Fast path: no lock needed for a read if already built
    with _cache_lock:
        if _embedding_cache is not None:
            return _embedding_cache, _user_map_cache, _user_obj_cache

    # Slow path: rebuild outside the lock so other threads aren't blocked
    # during what can be a multi-second DB query.
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

    # Re-acquire lock to assign — only write if another thread hasn't beaten us to it
    with _cache_lock:
        if _embedding_cache is None:
            _embedding_cache = user_embeddings
            _user_map_cache = user_map
            _user_obj_cache = user_obj_map
            logger.info(f"Embedding cache built: {len(user_embeddings)} users")
        return _embedding_cache, _user_map_cache, _user_obj_cache


# ---------------------------------------------------------------------------
# Daily attendance ledger  (8 am → next 8 am = one "day")
# Stored in MEDIA_ROOT/attendance_logs/YYYY-MM-DD.json and mirrored in memory.
# Used to detect duplicates instantly without waiting for the external API.
# ---------------------------------------------------------------------------
_daily_records: dict = {}       # {str(api_user_id): {"check_in": "HH:MM:SS", ...}}
_daily_records_day = None       # date — which attendance-day is currently loaded
_daily_records_lock = threading.Lock()


def _attendance_day():
    """Return the date that represents the current 8am–8am attendance window."""
    now = datetime.now()
    if now.time() >= _time(8, 0):
        return now.date()
    return (now - timedelta(days=1)).date()


def _attendance_filepath(day):
    log_dir = os.path.join(settings.MEDIA_ROOT, 'attendance_logs')
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f"{day}.json")


def _ensure_daily_records():
    """Reload from disk if the attendance day has rolled over. Must be called under _daily_records_lock."""
    global _daily_records, _daily_records_day
    today = _attendance_day()
    if _daily_records_day == today:
        return
    _daily_records_day = today
    filepath = _attendance_filepath(today)
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                _daily_records = json.load(f).get('records', {})
        except Exception as e:
            logger.error(f"Failed to load daily attendance file: {e}")
            _daily_records = {}
    else:
        _daily_records = {}
    logger.info(f"Daily attendance ledger loaded for {today}: {len(_daily_records)} users")


def _save_daily_records():
    """Persist current in-memory records to disk. Must be called under _daily_records_lock."""
    today = _attendance_day()
    filepath = _attendance_filepath(today)
    tmp = filepath + '.tmp'
    try:
        with open(tmp, 'w') as f:
            json.dump({'day': str(today), 'records': _daily_records}, f, indent=2)
        os.replace(tmp, filepath)
    except Exception as e:
        logger.error(f"Failed to write daily attendance file: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass


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


def attendance_status(request):
    """Return today's attendance ledger so the UI can disable already-checked-in users."""
    with _daily_records_lock:
        _ensure_daily_records()
        records_snapshot = dict(_daily_records)
    return JsonResponse({'records': records_snapshot})


from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods


@login_required(login_url='/admin/login/')
@require_http_methods(['GET', 'POST'])
def manual_attendance_override(request):
    """Hidden staff-only page to mark attendance without face recognition."""
    if not request.user.is_staff:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Access denied.')

    users = CustomUser.objects.filter(
        api_user_id__isnull=False
    ).order_by('username').values('api_user_id', 'username')

    result = None

    if request.method == 'POST':
        api_user_id = request.POST.get('api_user_id', '').strip()
        action      = request.POST.get('action', '').strip()

        # Validate inputs
        error = None
        if not api_user_id:
            error = 'Please select a user.'
        elif action not in ('check_in', 'check_out'):
            error = 'Invalid action.'

        if not error:
            try:
                target_user = CustomUser.objects.get(api_user_id=api_user_id)
            except CustomUser.DoesNotExist:
                error = 'User not found.'

        if not error:
            uid_str      = str(target_user.api_user_id)
            display_name = target_user.get_display_name()
            action_label = 'Checked In' if action == 'check_in' else 'Checked Out'

            # Update local daily ledger (same path as face recognition)
            with _daily_records_lock:
                _ensure_daily_records()
                now_str = datetime.now().strftime('%I:%M %p')
                if uid_str not in _daily_records:
                    _daily_records[uid_str] = {}
                _daily_records[uid_str][action] = now_str
                _save_daily_records()

            # Call external API synchronously so admin sees the real result
            api_fn     = check_in_user if action == 'check_in' else check_out_user
            api_result = api_fn(target_user.api_user_id)

            logger.info(
                f"MANUAL OVERRIDE: {request.user.username} → {display_name} ({action})"
            )

            result = {
                'success': True,
                'message': f'{display_name} {action_label} at {now_str}.',
                'api_ok': api_result.get('success', False),
                'api_msg': api_result.get('message', ''),
            }
        else:
            result = {'success': False, 'message': error}

    return render(request, 'override_attendance.html', {
        'users': users,
        'result': result,
    })


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

        # Load cache first (in-memory, instant after first hit) so we have names for all log lines
        user_embeddings, user_map, user_obj_map = _get_embedding_cache()
        selected_name = user_map.get(int(selected_user_id), f"ID:{selected_user_id}")

        logger.info(f"→ {action.upper()} | {selected_name} (ID: {selected_user_id}) | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Pass numpy array directly — no temp file needed
        query_embedding = compute_face_embedding(img, model_name="SFace", fast_mode=True)

        if query_embedding is None:
            logger.warning(f"No face detected for selected user: {selected_name} (ID: {selected_user_id})")
            return JsonResponse({
                'success': False,
                'error': 'Could not detect face in the image. Please try again with better lighting.'
            })

        DISTANCE_THRESHOLD = 0.45

        user_id, distance, confidence, log_match = find_best_match(
            query_embedding,
            user_embeddings,
            threshold=DISTANCE_THRESHOLD,
            user_map=user_map,
        )

        if user_id is None:
            log_match()
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

        api_uid = recognized_user.api_user_id
        display_name = recognized_user.get_display_name()
        uid_str = str(api_uid)
        action_label = 'Checked In' if action == 'check_in' else 'Checked Out'

        # Check local daily ledger — instant duplicate detection, no API call needed.
        with _daily_records_lock:
            _ensure_daily_records()
            user_record = _daily_records.get(uid_str, {})

            if action in user_record:
                marked_at = user_record[action]
                logger.info(f"DUPLICATE {action.upper()} blocked for {display_name} (already at {marked_at})")
                return JsonResponse({
                    'success': True,
                    'status': 'warning',
                    'message': f'{display_name} is already {action_label} at {marked_at}',
                    'user': {
                        'username': recognized_user.username,
                        'display_name': display_name,
                        'api_id': api_uid
                    },
                    'action': action,
                    'confidence': f"{confidence:.2f}%",
                    'distance': f"{distance:.4f}"
                })

            # Block check-out if the user has not checked in yet today
            if action == 'check_out' and 'check_in' not in user_record:
                logger.warning(f"CHECK-OUT BLOCKED — {display_name} (id={api_uid}) has no check-in for today")
                return JsonResponse({
                    'success': False,
                    'status': 'not_checked_in',
                    'error': 'Not checked in yet. Please check in first.'
                })

            # Record the attendance locally before returning
            now_str = datetime.now().strftime('%I:%M %p')
            if uid_str not in _daily_records:
                _daily_records[uid_str] = {}
            _daily_records[uid_str][action] = now_str
            _save_daily_records()

        # Fire the external API call in a background thread — user gets instant response.
        api_fn = check_in_user if action == 'check_in' else check_out_user

        def _post_attendance():
            import time as _time_mod
            IMMEDIATE_RETRIES = 5
            RETRY_DELAYS = [5, 10, 20, 30, 60]  # seconds between attempts

            result = None
            for attempt in range(1, IMMEDIATE_RETRIES + 1):
                result = api_fn(api_uid)
                if result['success']:
                    logger.info(f"Async attendance API OK for {display_name} (attempt {attempt}): {result.get('message')}")
                    return
                logger.warning(
                    f"Async attendance attempt {attempt}/{IMMEDIATE_RETRIES} FAILED for {display_name}: {result.get('message')}"
                )
                if attempt < IMMEDIATE_RETRIES:
                    _time_mod.sleep(RETRY_DELAYS[attempt - 1])

            # All immediate retries exhausted — enqueue for long-term retry worker
            logger.error(f"All {IMMEDIATE_RETRIES} immediate attempts failed for {display_name} ({action}). Queuing for background retry.")
            try:
                from django.utils import timezone as dj_tz
                from .models import PendingAttendanceSync
                PendingAttendanceSync.objects.create(
                    user_id=api_uid,
                    attendance_type=action,
                    scheduled_time=dj_tz.now(),
                    last_error=(result.get('message', '') if result else 'Unknown')[:500],
                )
                logger.info(f"Queued retry for {display_name} ({action})")
            except Exception as enqueue_err:
                logger.error(f"CRITICAL: Failed to enqueue retry for {display_name}: {enqueue_err}")

        threading.Thread(target=_post_attendance, daemon=True).start()

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


