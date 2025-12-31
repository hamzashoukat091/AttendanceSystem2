import os
from datetime import datetime, timedelta
from django.conf import settings
from .models import Attendance
import logging
import numpy as np

logger = logging.getLogger(__name__)


def compute_face_embedding(image_path, model_name="SFace"):
    """
    Compute face embedding for a given image using DeepFace.
    
    Args:
        image_path: Absolute path to the face image
        model_name: DeepFace model to use (default: SFace)
    
    Returns:
        list: 512D embedding vector as list, or None if failed
    """
    try:
        from deepface import DeepFace
        
        # Use DeepFace.represent() to extract embeddings
        # This is much faster than DeepFace.verify() during recognition
        result = DeepFace.represent(
            img_path=image_path,
            model_name=model_name,
            detector_backend="opencv",
            enforce_detection=False
        )
        
        # DeepFace.represent returns a list of dicts (one per detected face)
        if result and len(result) > 0:
            embedding = result[0]["embedding"]
            logger.info(f"Computed embedding for {image_path}: {len(embedding)}D vector")
            return embedding
        else:
            logger.warning(f"No face detected in {image_path}")
            return None
            
    except Exception as e:
        logger.error(f"Error computing embedding for {image_path}: {str(e)}")
        return None


def cosine_similarity(embedding1, embedding2):
    """
    Calculate cosine similarity between two embeddings.
    
    Args:
        embedding1: First embedding vector (list or numpy array)
        embedding2: Second embedding vector (list or numpy array)
    
    Returns:
        float: Cosine similarity score (0-1, higher = more similar)
    """
    try:
        # Convert to numpy arrays
        vec1 = np.array(embedding1)
        vec2 = np.array(embedding2)
        
        # Compute cosine similarity
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        similarity = dot_product / (norm1 * norm2)
        return float(similarity)
        
    except Exception as e:
        logger.error(f"Error calculating cosine similarity: {str(e)}")
        return 0.0


def cosine_distance(embedding1, embedding2):
    """
    Calculate cosine distance between two embeddings.
    
    Cosine distance = 1 - cosine_similarity
    Lower distance = more similar faces
    
    Args:
        embedding1: First embedding vector
        embedding2: Second embedding vector
    
    Returns:
        float: Cosine distance (0-2, lower = more similar)
    """
    similarity = cosine_similarity(embedding1, embedding2)
    return 1.0 - similarity


def find_best_match(query_embedding, user_embeddings, threshold=0.45):
    """
    Find the best matching user for a query face embedding.
    
    Args:
        query_embedding: The face embedding to match (512D vector)
        user_embeddings: Dict of {user_id: [embeddings_list]} from database
        threshold: Maximum cosine distance for a valid match (default: 0.45)
    
    Returns:
        tuple: (user_id, distance, confidence) or (None, None, None) if no match
    """
    from .models import CustomUser
    
    best_user_id = None
    best_distance = float('inf')
    all_matches = []  # Store all matches for logging
    
    logger.info("="*80)
    logger.info(f"FACE MATCHING ANALYSIS - Threshold: {threshold}")
    logger.info(f"Comparing against {len(user_embeddings)} users with registered faces")
    logger.info("="*80)
    
    for user_id, embeddings_list in user_embeddings.items():
        user_best_distance = float('inf')
        
        for idx, stored_embedding in enumerate(embeddings_list):
            distance = cosine_distance(query_embedding, stored_embedding)
            
            if distance < user_best_distance:
                user_best_distance = distance
            
            if distance < best_distance:
                best_distance = distance
                best_user_id = user_id
        
        # Calculate confidence for this user
        user_confidence = (1.0 - user_best_distance) * 100
        
        # Get username for logging
        try:
            user = CustomUser.objects.get(id=user_id)
            username = user.username
            display_name = user.get_display_name()
        except:
            username = f"User_{user_id}"
            display_name = username
        
        all_matches.append({
            'user_id': user_id,
            'username': username,
            'display_name': display_name,
            'distance': user_best_distance,
            'confidence': user_confidence,
            'embeddings_count': len(embeddings_list)
        })
    
    # Sort matches by confidence (descending)
    all_matches.sort(key=lambda x: x['confidence'], reverse=True)
    
    # Log top 10 matches
    logger.info("\nTOP 10 MATCHING RESULTS:")
    logger.info("-" * 80)
    logger.info(f"{'Rank':<6} {'Username':<20} {'Display Name':<25} {'Distance':<10} {'Confidence':<12} {'Pass?'}")
    logger.info("-" * 80)
    
    for rank, match in enumerate(all_matches[:10], 1):
        passed = "[PASS]" if match['distance'] <= threshold else "[FAIL]"
        logger.info(
            f"{rank:<6} {match['username']:<20} {match['display_name']:<25} "
            f"{match['distance']:<10.4f} {match['confidence']:>6.2f}%     {passed}"
        )
    
    # Check if best match meets threshold
    if best_distance <= threshold:
        confidence = (1.0 - best_distance) * 100  # Convert to percentage
        logger.info("\n" + "="*80)
        logger.info(f"[MATCH FOUND]")
        logger.info(f"  User: {all_matches[0]['display_name']} ({all_matches[0]['username']})")
        logger.info(f"  Distance: {best_distance:.4f} (threshold: {threshold})")
        logger.info(f"  Confidence: {confidence:.2f}%")
        logger.info(f"  Embeddings checked: {all_matches[0]['embeddings_count']}")
        logger.info("="*80 + "\n")
        return best_user_id, best_distance, confidence
    else:
        logger.info("\n" + "="*80)
        logger.info(f"[NO MATCH] - Best distance {best_distance:.4f} exceeds threshold {threshold}")
        if all_matches:
            logger.info(f"  Closest was: {all_matches[0]['display_name']} with {all_matches[0]['confidence']:.2f}% confidence")
        logger.info("="*80 + "\n")
        return None, None, None


def mark_user_attendance(user):
    now_time = datetime.now()
    
    # LOGIC: Day starts at 8:00 AM and ends at 8:00 AM next day.
    if now_time.hour < 8:
        attendance_date = (now_time - timedelta(days=1)).date()
    else:
        attendance_date = now_time.date()

    # Get or create today's record
    attendance, created = Attendance.objects.get_or_create(user=user, date=attendance_date)

    # 🟢 Case 1 — First check-in
    if not attendance.check_in:
        attendance.check_in = now_time
        attendance.status = "Checked In"  #   mark as Checked In
        attendance.save()
        return "check_in", now_time.strftime("%H:%M:%S"), attendance.check_in

    # 🟢 Case 2 — Then check-out
    elif not attendance.check_out:
        attendance.check_out = now_time
        attendance.status = "Present"  #   change status to Present
        attendance.save()
        return "check_out", now_time.strftime("%H:%M:%S"), attendance.check_in

    # 🟢 Case 3 — Already marked fully
    else:
        return "completed", None, attendance.check_in

    
from datetime import date, timedelta
from django.db.models import Q
from .models import Attendance, MonthlyAttendance, LeaveRequest

def update_monthly_attendance(user, target_date=None):
    if target_date is None:
        target_date = date.today()

    year = target_date.year
    month = target_date.month

    # Get all attendance records for this month
    records = Attendance.objects.filter(
        user=user,
        date__year=year,
        date__month=month
    )

    # --- Counters ---
    total_days = 0
    present_days = 0
    absent_days = 0
    leave_days = 0
    holidays = 0

    # Get leave ranges (approved)
    approved_leaves = LeaveRequest.objects.filter(
        user=user, status="Approved"
    )

    leave_dates = set()
    for leave in approved_leaves:
        current = leave.start_date
        while current <= leave.end_date:
            if current.year == year and current.month == month:
                leave_dates.add(current)
            current += timedelta(days=1)

    # Count all days in the month
    from calendar import monthrange
    total_days = monthrange(year, month)[1]

    # Loop through each day of month
    for day in range(1, total_days + 1):
        current_date = date(year, month, day)
        weekday = current_date.weekday()  # 0=Mon ... 6=Sun

        # Weekends = Holiday
        if weekday in (5, 6):  # Saturday or Sunday
            holidays += 1
            continue

        # Leave days
        if current_date in leave_dates:
            leave_days += 1
            continue

        # Attendance record check
        att = records.filter(date=current_date).first()
        if att:
            if att.status in ["Present", "Checked In"]:
                present_days += 1
            elif att.status in ["Absent", "Absent (No Check-Out)"]:
                absent_days += 1
            elif att.status == "Leave":
                leave_days += 1
            elif att.status == "Holiday":
                holidays += 1
        else:
            absent_days += 1

    # Compute percentage safely
    working_days = total_days - holidays
    if working_days > 0:
        percentage = round((present_days / working_days) * 100, 2)
    else:
        percentage = 0.0

    # Save or update MonthlyAttendance record
    MonthlyAttendance.objects.update_or_create(
        user=user,
        month=month,
        year=year,
        defaults={
            "total_days": total_days,
            "present_days": present_days,
            "absent_days": absent_days,
            "leave_days": leave_days,
            "holidays": holidays,
            "percentage": percentage,
        },
    )