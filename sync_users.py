"""
One-time script to sync api_user_id and has_face_data using bulk_update for speed.
"""
import os, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'attendease.settings'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

import django
django.setup()

import requests
from accounts.models import CustomUser, UserFaceEmbedding
from django.db.models import Count

# 1. Fetch API users directly (avoid deepface-heavy import chain)
API_URL = "https://office.digitalperfection.app/api/users"
print("Fetching users from external API...")
resp = requests.get(API_URL, timeout=10)
data = resp.json()
api_users = data.get('users', data) if isinstance(data, dict) else data
print(f"Fetched {len(api_users)} users from API")

api_email_map = {u['email'].lower(): u['id'] for u in api_users}

# 2. Update api_user_id using bulk_update
users_to_update = []
for user in CustomUser.objects.all():
    api_id = api_email_map.get(user.email.lower())
    if api_id and user.api_user_id != api_id:
        user.api_user_id = api_id
        users_to_update.append(user)
        print(f"  {user.username} -> api_id={api_id}")

if users_to_update:
    CustomUser.objects.bulk_update(users_to_update, ['api_user_id'])
print(f"Updated api_user_id for {len(users_to_update)} users")

# 3. Sync has_face_data and face_images_count using aggregation + bulk_update
print()
embedding_counts = dict(
    UserFaceEmbedding.objects.values('user_id')
    .annotate(cnt=Count('id'))
    .values_list('user_id', 'cnt')
)

users_to_sync = []
for user in CustomUser.objects.all():
    emb_count = embedding_counts.get(user.id, 0)
    new_flag = emb_count > 0
    if user.has_face_data != new_flag or user.face_images_count != emb_count:
        user.has_face_data = new_flag
        user.face_images_count = emb_count
        users_to_sync.append(user)

if users_to_sync:
    CustomUser.objects.bulk_update(users_to_sync, ['has_face_data', 'face_images_count'])
print(f"Synced has_face_data + face_images_count for {len(users_to_sync)} users")

# 4. Summary
print()
print("=== Final State ===")
print("Users with has_face_data=True:", CustomUser.objects.filter(has_face_data=True).count())
print("Users with api_user_id set:   ", CustomUser.objects.filter(api_user_id__isnull=False).count())
print("Total embeddings:              ", UserFaceEmbedding.objects.count())
print()
no_api = list(CustomUser.objects.filter(has_face_data=True, api_user_id__isnull=True))
if no_api:
    print("Users with face data but NO api_user_id (cannot post attendance):")
    for u in no_api:
        print(f"  - {u.username} ({u.email})")
else:
    print("All users with face data have api_user_id set.")
