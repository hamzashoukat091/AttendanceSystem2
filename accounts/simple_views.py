import os
import uuid
import cv2
import base64
import numpy as np
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from datetime import datetime, timedelta
import logging

from .models import CustomUser, UserFaceEmbedding, Attendance
from .api_service import check_in_user, check_out_user
from .utils import compute_face_embedding, find_best_match

logger = logging.getLogger(__name__)

# Face database path
FACE_DB = os.path.join(settings.MEDIA_ROOT, "faces")
os.makedirs(FACE_DB, exist_ok=True)


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
    """
    Delete all existing face data (images and embeddings) for a user to allow recapture
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    try:
        user = CustomUser.objects.get(api_user_id=user_id)

        # Delete face folder and all images
        user_folder = os.path.join(FACE_DB, user.username)
        if os.path.exists(user_folder):
            import shutil
            shutil.rmtree(user_folder)
            logger.info(f"Deleted face folder for {user.username}")
        
        # Delete all face embeddings from database
        deleted_embeddings = UserFaceEmbedding.objects.filter(user=user).delete()
        logger.info(f"Deleted {deleted_embeddings[0]} embeddings for {user.username}")
        
        # Update user flags
        user.has_face_data = False
        user.face_images_count = 0
        user.save()
        
        return JsonResponse({
            'success': True,
            'message': 'All face data deleted successfully. You can now recapture.'
        })
        
    except CustomUser.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'})
    except Exception as e:
        logger.error(f"Error deleting face data: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def save_face_image(request):
    """
    Save captured face image for a user and compute embedding
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    try:
        import json
        from .models import UserFaceEmbedding
        
        data = json.loads(request.body)
        user_id = data.get('user_id')
        image_data = data.get('image')
        
        if not user_id or not image_data:
            return JsonResponse({'success': False, 'error': 'Missing user_id or image data'})
        
        # Get user
        user = CustomUser.objects.get(api_user_id=user_id)
        
        # Decode image
        img = decode_base64_image(image_data)
        if img is None:
            return JsonResponse({'success': False, 'error': 'Failed to decode image'})
        
        # Create user folder
        user_folder = os.path.join(FACE_DB, user.username)
        os.makedirs(user_folder, exist_ok=True)
        
        # Count existing images
        img_count = len(os.listdir(user_folder))
        
        # Save image
        img_filename = f"{user.username}_{img_count + 1}.jpg"
        img_path = os.path.join(user_folder, img_filename)
        cv2.imwrite(img_path, img)
        
        # Compute face embedding for fast recognition
        embedding = compute_face_embedding(img_path, model_name="SFace")
        
        if embedding:
            # Store embedding in database
            # Use relative path for portability
            relative_path = os.path.join("faces", user.username, img_filename)
            
            UserFaceEmbedding.objects.create(
                user=user,
                image_path=relative_path,
                embedding=embedding,
                model_name="SFace"
            )
            logger.info(f"Saved embedding for {img_filename}")
        else:
            logger.warning(f"Could not compute embedding for {img_filename}, removing dirty capture.")
            if os.path.exists(img_path):
                os.remove(img_path)
            return JsonResponse({'success': False, 'error': 'No face detected. Please ensure your face is clearly visible.'})
        
        # Update user face count
        user.face_images_count = img_count + 1
        if not user.has_face_data:
            user.has_face_data = True
        user.save()
        
        logger.info(f"Saved face image {img_count + 1} for user {user.get_display_name()}")
        
        return JsonResponse({
            'success': True,
            'message': f'Image {img_count + 1} saved successfully',
            'count': img_count + 1,
            'has_embedding': embedding is not None
        })
        
    except CustomUser.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'})
    except Exception as e:
        logger.error(f"Error saving face image: {str(e)}")
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


def attendance_scanner(request):
    """
    Main attendance scanner interface - no login required
    """
    context = {
        'title': 'Face Attendance Scanner'
    }
    return render(request, 'attendance_scanner.html', context)


@csrf_exempt
def recognize_and_mark_attendance(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    try:
        import json
        data = json.loads(request.body)
        image_data = data.get('image')
        action = data.get('action', 'check_in')  # check_in or check_out
        
        if not image_data:
            return JsonResponse({'success': False, 'error': 'No image data provided'})
        
        # Decode image
        img = decode_base64_image(image_data)
        if img is None:
            return JsonResponse({'success': False, 'error': 'Failed to decode image'})
        
        # Save temporary image with unique name to avoid concurrent-request collisions
        temp_image = os.path.join(settings.MEDIA_ROOT, f"temp_scan_{uuid.uuid4().hex}.jpg")
        cv2.imwrite(temp_image, img)
        
        query_embedding = compute_face_embedding(temp_image, model_name="SFace")
        
        if query_embedding is None:
            # Clean up temp file
            if os.path.exists(temp_image):
                os.remove(temp_image)
            return JsonResponse({
                'success': False,
                'error': 'Could not detect face in the image. Please try again with better lighting.'
            })
        
        user_embeddings = {}
        users_with_faces = CustomUser.objects.filter(
            has_face_data=True, api_user_id__isnull=False
        ).prefetch_related('face_embeddings')

        user_map = {}
        for user in users_with_faces:
            embeddings = [e.embedding for e in user.face_embeddings.all()]
            if embeddings:
                user_embeddings[user.api_user_id] = embeddings
                user_map[user.api_user_id] = user.get_display_name()

        DISTANCE_THRESHOLD = 0.33

        logger.info(f"FACE RECOGNITION REQUEST - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Action: {action.upper()}")

        user_id, distance, confidence = find_best_match(
            query_embedding,
            user_embeddings,
            threshold=DISTANCE_THRESHOLD,
            user_map=user_map,
        )
        
        # Clean up temp file
        if os.path.exists(temp_image):
            os.remove(temp_image)
        
        if user_id is None:
            logger.warning("RECOGNITION FAILED - No matching face found above threshold")
            return JsonResponse({
                'success': False,
                'error': 'Face not recognized. Please register first or try again with better lighting.'
            })
        
        # Get the recognized user
        recognized_user = CustomUser.objects.get(api_user_id=user_id)
        now = datetime.now()
        current_time = now.time()

        # Day boundary: 8 AM to 8 AM next day
        attendance_date = now.date() if now.hour >= 8 else (now - timedelta(days=1)).date()
        attendance, _ = Attendance.objects.get_or_create(user=recognized_user, date=attendance_date)

        # Duplicate check — before hitting external API
        if action == 'check_in' and attendance.check_in is not None:
            return JsonResponse({
                'success': True,
                'already_done': True,
                'message': f'{recognized_user.get_display_name()} already checked in at {attendance.check_in.strftime("%H:%M:%S")}.',
                'user': {
                    'username': recognized_user.username,
                    'display_name': recognized_user.get_display_name(),
                    'email': recognized_user.email,
                    'api_id': recognized_user.api_user_id
                },
                'action': action,
                'time': attendance.check_in.strftime('%H:%M:%S'),
                'confidence': f"{confidence:.2f}%",
                'distance': f"{distance:.4f}"
            })

        if action == 'check_out' and attendance.check_out is not None:
            return JsonResponse({
                'success': True,
                'already_done': True,
                'message': f'{recognized_user.get_display_name()} already checked out at {attendance.check_out.strftime("%H:%M:%S")}.',
                'user': {
                    'username': recognized_user.username,
                    'display_name': recognized_user.get_display_name(),
                    'email': recognized_user.email,
                    'api_id': recognized_user.api_user_id
                },
                'action': action,
                'time': attendance.check_out.strftime('%H:%M:%S'),
                'confidence': f"{confidence:.2f}%",
                'distance': f"{distance:.4f}"
            })

        if action == 'check_out' and attendance.check_in is None:
            return JsonResponse({
                'success': False,
                'error': f'{recognized_user.get_display_name()} has not checked in yet. Please check in first.'
            })

        # Post attendance to external API
        if action == 'check_in':
            api_response = check_in_user(recognized_user.api_user_id)
        else:
            api_response = check_out_user(recognized_user.api_user_id)

        if not api_response['success']:
            return JsonResponse({
                'success': False,
                'error': f"API Error: {api_response['message']}"
            })

        if action == 'check_in':
            attendance.check_in = current_time
            attendance.status = 'Checked In'
            attendance.check_in_confidence = round(confidence, 2)
        else:
            attendance.check_out = current_time
            if attendance.check_in:
                attendance.status = 'Present'
            attendance.check_out_confidence = round(confidence, 2)

        attendance.api_message = api_response.get('message', '')
        attendance.save()

        return JsonResponse({
            'success': True,
            'message': f'{recognized_user.get_display_name()} {action.replace("_", " ")} successful!',
            'user': {
                'username': recognized_user.username,
                'display_name': recognized_user.get_display_name(),
                'email': recognized_user.email,
                'api_id': recognized_user.api_user_id
            },
            'action': action,
            'time': current_time.strftime('%H:%M:%S'),
            'confidence': f"{confidence:.2f}%",
            'distance': f"{distance:.4f}"
        })
        
    except CustomUser.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User data error'})
    except Exception as e:
        logger.error(f"Error in face recognition: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Recognition error: {str(e)}'
        })


def view_users(request):
    """View all registered users and their face registration status"""
    users = CustomUser.objects.filter(api_user_id__isnull=False).order_by('username')
    
    context = {
        'users': users,
        'title': 'Registered Users'
    }
    return render(request, 'view_users.html', context)
