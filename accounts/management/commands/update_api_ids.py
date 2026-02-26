"""
Django management command to update api_user_id values by matching users by name
from the new API endpoint, without touching face data or requiring re-registration.

Usage:
    python manage.py update_api_ids           # Apply changes
    python manage.py update_api_ids --dry-run # Preview only
"""
import requests
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

User = get_user_model()

NEW_API_USERS_URL = "https://office.digitalperfection.app/api/users"


def normalize_name(name):
    """Lowercase and collapse whitespace for reliable name matching."""
    return ' '.join(name.lower().split())


def fetch_new_api_users():
    """Fetch users from the new API endpoint."""
    try:
        response = requests.get(NEW_API_USERS_URL, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Try standard envelope format first
        if isinstance(data, dict):
            if data.get('success') and data.get('statusCode') == 200:
                users = data.get('data', {}).get('users', [])
                return users
            # Some APIs return data directly under 'data'
            if 'data' in data and isinstance(data['data'], list):
                return data['data']
            # Flat list under 'users'
            if 'users' in data and isinstance(data['users'], list):
                return data['users']

        # Raw list response
        if isinstance(data, list):
            return data

        return None
    except requests.RequestException as e:
        return None


class Command(BaseCommand):
    help = 'Update api_user_id for existing DB users by matching names against the new API'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without saving to the database',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('\n[DRY RUN] No changes will be saved.\n'))

        # ── Fetch from new API ──────────────────────────────────────────────
        self.stdout.write('Fetching users from new API...')
        api_users = fetch_new_api_users()

        if not api_users:
            self.stdout.write(self.style.ERROR(
                f'Failed to fetch users from {NEW_API_USERS_URL}\n'
                'Check that the URL is reachable and returns a valid response.'
            ))
            return

        self.stdout.write(self.style.SUCCESS(f'Fetched {len(api_users)} users from new API.'))

        # Build lookup: normalized name → api user dict
        api_lookup = {}
        for api_user in api_users:
            name = api_user.get('name', '').strip()
            if name:
                key = normalize_name(name)
                if key in api_lookup:
                    self.stdout.write(self.style.WARNING(
                        f'  WARNING: Duplicate name in API response: "{name}" — only first entry used'
                    ))
                else:
                    api_lookup[key] = api_user

        # ── Fetch DB users that have an api_user_id ─────────────────────────
        db_users = User.objects.filter(api_user_id__isnull=False).order_by('username')

        if not db_users.exists():
            self.stdout.write(self.style.WARNING('No DB users with api_user_id found. Nothing to update.'))
            return

        # ── BEFORE table ────────────────────────────────────────────────────
        sep = '=' * 70
        self.stdout.write('\n' + sep)
        self.stdout.write('BEFORE: Current api_user_id values in SQLite DB')
        self.stdout.write(sep)
        self.stdout.write(f'  {"Username":<22} | {"Full Name":<25} | Old API ID')
        self.stdout.write(f'  {"-"*22} | {"-"*25} | ----------')
        for u in db_users:
            full_name = f'{u.first_name} {u.last_name}'.strip() or u.username
            self.stdout.write(f'  {u.username:<22} | {full_name:<25} | {u.api_user_id}')

        # ── Match & update ──────────────────────────────────────────────────
        results = []         # (username, full_name, old_id, new_id, status)
        unmatched_db = []    # DB users with no name match in API
        pending_updates = [] # (user_obj, new_id) — saved after full scan

        matched_api_keys = set()

        for u in db_users:
            full_name = f'{u.first_name} {u.last_name}'.strip() or u.username
            key = normalize_name(full_name)
            old_id = u.api_user_id

            if key in api_lookup:
                matched_api_keys.add(key)
                new_id = api_lookup[key].get('id')

                if new_id is None:
                    results.append((u.username, full_name, old_id, None, 'NO ID IN API'))
                    continue

                if old_id == new_id:
                    results.append((u.username, full_name, old_id, new_id, 'NO CHANGE'))
                else:
                    results.append((u.username, full_name, old_id, new_id, 'UPDATED'))
                    pending_updates.append((u, new_id))
            else:
                unmatched_db.append((u.username, full_name, old_id))

        # ── Two-pass save (avoids UNIQUE constraint collisions) ─────────────
        # IDs can "swap" between users, so saving one-by-one causes conflicts.
        # Fix: NULL out all changing IDs first, then write the new values.
        if not dry_run and pending_updates:
            with transaction.atomic():
                # Pass 1: clear all api_user_ids that will change
                for u, _ in pending_updates:
                    u.api_user_id = None
                    u.save(update_fields=['api_user_id'])
                # Pass 2: set all new api_user_ids
                for u, new_id in pending_updates:
                    u.api_user_id = new_id
                    u.save(update_fields=['api_user_id'])

        # ── AFTER table ─────────────────────────────────────────────────────
        self.stdout.write('\n' + sep)
        if dry_run:
            self.stdout.write('AFTER: What api_user_id values WOULD be (dry run)')
        else:
            self.stdout.write('AFTER: Updated api_user_id values in SQLite DB')
        self.stdout.write(sep)
        self.stdout.write(
            f'  {"Username":<22} | {"Full Name":<25} | {"Old ID":<10} | {"New ID":<10} | Status'
        )
        self.stdout.write(
            f'  {"-"*22} | {"-"*25} | {"-"*10} | {"-"*10} | ------'
        )

        updated_count = 0
        no_change_count = 0

        for username, full_name, old_id, new_id, status in results:
            new_id_str = str(new_id) if new_id is not None else 'N/A'
            line = f'  {username:<22} | {full_name:<25} | {str(old_id):<10} | {new_id_str:<10} | {status}'
            if status == 'UPDATED':
                self.stdout.write(self.style.SUCCESS(line))
                updated_count += 1
            elif status == 'NO CHANGE':
                self.stdout.write(line)
                no_change_count += 1
            else:
                self.stdout.write(self.style.WARNING(line))

        # ── Unmatched DB users ───────────────────────────────────────────────
        if unmatched_db:
            self.stdout.write('\n' + sep)
            self.stdout.write(self.style.WARNING(
                'UNMATCHED DB users (full name not found in new API — ID unchanged)'
            ))
            self.stdout.write(sep)
            self.stdout.write(f'  {"Username":<22} | {"Full Name":<25} | Current API ID')
            self.stdout.write(f'  {"-"*22} | {"-"*25} | --------------')
            for username, full_name, old_id in unmatched_db:
                self.stdout.write(self.style.WARNING(
                    f'  {username:<22} | {full_name:<25} | {old_id}'
                ))

        # ── Unmatched API users ──────────────────────────────────────────────
        unmatched_api = [
            u for u in api_users
            if normalize_name(u.get('name', '').strip()) not in matched_api_keys
            and u.get('name', '').strip()
        ]
        if unmatched_api:
            self.stdout.write('\n' + sep)
            self.stdout.write('API users with no matching DB user (by name)')
            self.stdout.write(sep)
            self.stdout.write(f'  {"Name":<30} | {"Email":<30} | API ID')
            self.stdout.write(f'  {"-"*30} | {"-"*30} | ------')
            for u in unmatched_api:
                self.stdout.write(
                    f'  {u.get("name",""):<30} | {u.get("email",""):<30} | {u.get("id","")}'
                )

        # ── Summary ──────────────────────────────────────────────────────────
        self.stdout.write('\n' + sep)
        action = 'Would update' if dry_run else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'Summary: {action}={updated_count}  No Change={no_change_count}  '
            f'Unmatched DB={len(unmatched_db)}  Unmatched API={len(unmatched_api)}'
        ))
        self.stdout.write(sep)

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\nThis was a dry run. No changes were saved.\n'
                'Run without --dry-run to apply changes.'
            ))
