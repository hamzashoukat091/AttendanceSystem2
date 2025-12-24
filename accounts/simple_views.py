"""
Simplified face recognition and attendance system views
No login required - direct face registration and attendance marking
"""
import os
import cv2
import base64
import numpy as np
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from deepface import DeepFace
from datetime import datetime
import logging

from .models import CustomUser, Attendance
from .api_service import check_in_user, check_out_user

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
    """Search for users by email for face registration - privacy-focused"""
    users = []
    search_query = request.GET.get('email', '').strip()
    search_performed = bool(search_query)
    
    if search_query:
        # Only show exact email match for privacy
        users = CustomUser.objects.filter(
            email__iexact=search_query,
            api_user_id__isnull=False
        )
    
    context = {
        'users': users,
        'search_query': search_query,
        'search_performed': search_performed,
        'title': 'Search by your office app email'
    }
    return render(request, 'select_user.html', context)


def register_face(request, user_id):
    """
    Face registration interface - capture 25+ images
    """
    try:
        user = CustomUser.objects.get(id=user_id)
    except CustomUser.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)
    
    user_folder = os.path.join(FACE_DB, user.username)
    current_count = len(os.listdir(user_folder)) if os.path.exists(user_folder) else 0
    
    context = {
        'user': user,
        'current_count': current_count,
        'required_count': 25,
        'title': f'Register Face for {user.username}'
    }
    return render(request, 'register_face.html', context)


@csrf_exempt
def save_face_image(request):
    """
    Save captured face image for a user
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    try:
        import json
        data = json.loads(request.body)
        user_id = data.get('user_id')
        image_data = data.get('image')
        
        if not user_id or not image_data:
            return JsonResponse({'success': False, 'error': 'Missing user_id or image data'})
        
        # Get user
        user = CustomUser.objects.get(id=user_id)
        
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
        img_path = os.path.join(user_folder, f"{user.username}_{img_count + 1}.jpg")
        cv2.imwrite(img_path, img)
        
        # Update user face count
        user.face_images_count = img_count + 1
        if not user.has_face_data:
            user.has_face_data = True
        user.save()
        
        logger.info(f"Saved face image {img_count + 1} for user {user.username}")
        
        return JsonResponse({
            'success': True,
            'message': f'Image {img_count + 1} saved successfully',
            'count': img_count + 1
        })
        
    except CustomUser.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'})
    except Exception as e:
        logger.error(f"Error saving face image: {str(e)}")
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
    """
    Recognize face and mark attendance by calling external API
    """
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
        
        # Save temporary image
        temp_image = os.path.join(settings.MEDIA_ROOT, "temp_scan.jpg")
        cv2.imwrite(temp_image, img)
        
        # Try to recognize face against all registered users
        recognized_user = None
        best_match_distance = 10.0
        
        for user in CustomUser.objects.filter(has_face_data=True):
            user_folder = os.path.join(FACE_DB, user.username)
            
            if not os.path.exists(user_folder):
                continue
            
            # Check against user's face images
            for img_file in os.listdir(user_folder):
                db_img_path = os.path.join(user_folder, img_file)
                
                try:
                    result = DeepFace.verify(
                        temp_image,
                        db_img_path,
                        model_name="SFace",
                        detector_backend="opencv",
                        enforce_detection=False
                    )
                    
                    if result['verified'] and result['distance'] < best_match_distance:
                        best_match_distance = result['distance']
                        recognized_user = user
                        break  # Found match for this user
                        
                except Exception as e:
                    logger.debug(f"Verification failed for {db_img_path}: {str(e)}")
                    continue
            
            if recognized_user:
                break  # Stop searching once we find a match
        
        # Clean up temp file
        if os.path.exists(temp_image):
            os.remove(temp_image)
        
        if not recognized_user:
            return JsonResponse({
                'success': False,
                'error': 'Face not recognized. Please register first.'
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
        
        # Also save to local database for backup/reporting
        today = datetime.now().date()
        attendance, created = Attendance.objects.get_or_create(
            user=recognized_user,
            date=today
        )
        
        current_time = datetime.now().time()
        
        if action == 'check_in':
            attendance.check_in = current_time
            attendance.status = 'Checked In'
        else:
            attendance.check_out = current_time
            if attendance.check_in:
                attendance.status = 'Present'
        
        attendance.save()
        
        return JsonResponse({
            'success': True,
            'message': f'{recognized_user.username} {action.replace("_", " ")} successful!',
            'user': {
                'username': recognized_user.username,
                'email': recognized_user.email,
                'api_id': recognized_user.api_user_id
            },
            'action': action,
            'time': current_time.strftime('%H:%M:%S')
        })
        
    except Exception as e:
        logger.error(f"Error in face recognition: {str(e)}")
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
