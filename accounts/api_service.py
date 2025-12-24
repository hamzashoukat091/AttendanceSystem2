"""
API Service for interacting with external Perfect Office API
"""
import requests
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

# API Configuration
API_BASE_URL = "https://perfectofficeapp.proxipreview.com/api"
API_USERS_ENDPOINT = f"{API_BASE_URL}/users"
API_ATTENDANCE_ENDPOINT = f"{API_BASE_URL}/attendance/store"


def fetch_users_from_api() -> Optional[List[Dict]]:
    """
    Fetch all users from external API
    
    Returns:
        List of user dictionaries or None on error
    """
    try:
        response = requests.get(API_USERS_ENDPOINT, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        if data.get('success') and data.get('statusCode') == 200:
            users = data.get('data', {}).get('users', [])
            logger.info(f"Successfully fetched {len(users)} users from API")
            return users
        else:
            logger.error(f"API returned unsuccessful response: {data}")
            return None
            
    except requests.RequestException as e:
        logger.error(f"Error fetching users from API: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching users: {str(e)}")
        return None


def post_attendance(user_id: int, attendance_type: str) -> Dict:
    """
    Post attendance (check-in or check-out) to external API
    
    Args:
        user_id: External API user ID
        attendance_type: Either 'check_in' or 'check_out'
        
    Returns:
        Dictionary with success status and message
    """
    try:
        payload = {
            "user_id": str(user_id),
            "type": attendance_type
        }
        
        response = requests.post(
            API_ATTENDANCE_ENDPOINT,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        response.raise_for_status()
        
        data = response.json()
        
        if data.get('success') and data.get('statusCode') == 200:
            message = data.get('data', {}).get('message', 'Success')
            logger.info(f"Attendance posted successfully for user {user_id}: {message}")
            return {
                'success': True,
                'message': message
            }
        else:
            logger.error(f"API returned unsuccessful response: {data}")
            return {
                'success': False,
                'message': 'Failed to post attendance'
            }
            
    except requests.RequestException as e:
        logger.error(f"Error posting attendance to API: {str(e)}")
        return {
            'success': False,
            'message': f'Network error: {str(e)}'
        }
    except Exception as e:
        logger.error(f"Unexpected error posting attendance: {str(e)}")
        return {
            'success': False,
            'message': f'Error: {str(e)}'
        }


def check_in_user(user_id: int) -> Dict:
    """Convenience method for check-in"""
    return post_attendance(user_id, 'check_in')


def check_out_user(user_id: int) -> Dict:
    """Convenience method for check-out"""
    return post_attendance(user_id, 'check_out')
