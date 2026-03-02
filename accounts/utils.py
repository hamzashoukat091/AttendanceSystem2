import os
from django.conf import settings
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
            enforce_detection=True
        )
        
        # DeepFace.represent returns a list of dicts (one per detected face)
        if result and len(result) > 0:
            embedding = result[0]["embedding"]
            return embedding
        else:
            logger.warning(f"No face detected in {image_path}")
            return None
            
    except ValueError:
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


def find_best_match(query_embedding, user_embeddings, threshold=0.33):
    from .models import CustomUser
    
    best_user_id = None
    best_distance = float('inf')
    all_matches = []
    
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
    
    # Log top 5 matches
    logger.info("TOP 5 MATCHING RESULTS:")
    logger.info("-" * 85)
    logger.info(f"{'Rank':<6} {'Username':<20} {'Display Name':<25} {'Distance':<10} {'Confidence':<12} {'Pass'}")
    logger.info("-" * 85)
    
    for rank, match in enumerate(all_matches[:5], 1):
        passed = "[PASS]" if match['distance'] <= threshold else "[FAIL]"
        logger.info(
            f"{rank:<6} {match['username']:<20} {match['display_name']:<25} "
            f"{match['distance']:<10.4f} {match['confidence']:>6.2f}%     {passed}"
        )
    
    # Count how many users passed the threshold
    passing_count = sum(1 for m in all_matches if m['distance'] <= threshold)
    
    if passing_count > 2:
        logger.info("="*80)
        logger.warning(f"  [AMBIGUOUS MATCH] - {passing_count} users passed the threshold (> 2).")
        logger.warning("  Rejecting result to prevent false positive.")
        logger.info("="*80)
        return None, None, None

    # Check if best match meets threshold
    if best_distance <= threshold:
        confidence = (1.0 - best_distance) * 100  # Convert to percentage
        logger.info("="*80)
        logger.info(f"  [MATCH FOUND]")
        logger.info(f"  User: {all_matches[0]['display_name']} ({all_matches[0]['username']})")
        logger.info(f"  Distance: {best_distance:.4f} (threshold: {threshold})")
        logger.info(f"  Confidence: {confidence:.2f}%")
        logger.info("="*80)
        return best_user_id, best_distance, confidence
    else:
        logger.info("="*80)
        logger.info(f"  [NO MATCH] - Best distance {best_distance:.4f} exceeds threshold {threshold}")
        if all_matches:
            logger.info(f"  Closest was: {all_matches[0]['display_name']} with {all_matches[0]['confidence']:.2f}% confidence")
        logger.info("="*80)
        return None, None, None


