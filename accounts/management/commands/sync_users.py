"""
Django management command to sync users from external API
Usage: python manage.py sync_users
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from accounts.api_service import fetch_users_from_api

User = get_user_model()


class Command(BaseCommand):
    help = 'Sync users from external Perfect Office API'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        self.stdout.write(self.style.WARNING('Fetching users from API...'))
        
        # Fetch users from API
        api_users = fetch_users_from_api()
        
        if not api_users:
            self.stdout.write(self.style.ERROR('Failed to fetch users from API'))
            return
        
        self.stdout.write(self.style.SUCCESS(f'Fetched {len(api_users)} users from API'))
        
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        for api_user in api_users:
            api_id = api_user.get('id')
            name = api_user.get('name')
            email = api_user.get('email')
            
            if not api_id or not name or not email:
                self.stdout.write(self.style.WARNING(f'Skipping incomplete user data: {api_user}'))
                skipped_count += 1
                continue
            
            # Generate username from name (remove spaces, make lowercase)
            username = name.replace(' ', '_').lower()
            
            if dry_run:
                self.stdout.write(f'  Would process: {name} ({email}) [API ID: {api_id}]')
                continue
            
            # Check if user already exists by API ID
            try:
                user = User.objects.get(api_user_id=api_id)
                # Update existing user
                user.username = username
                user.email = email
                user.first_name = name.split()[0] if ' ' in name else name
                user.last_name = ' '.join(name.split()[1:]) if ' ' in name else ''
                user.save()
                
                self.stdout.write(self.style.WARNING(f'  Updated: {name} ({email})'))
                updated_count += 1
                
            except User.DoesNotExist:
                # Create new user
                try:
                    # Check if username already exists
                    if User.objects.filter(username=username).exists():
                        # Append API ID to make it unique
                        username = f"{username}_{api_id}"
                    
                    user = User.objects.create_user(
                        username=username,
                        email=email,
                        first_name=name.split()[0] if ' ' in name else name,
                        last_name=' '.join(name.split()[1:]) if ' ' in name else '',
                        api_user_id=api_id,
                        is_approved=True,  # Auto-approve API users
                    )
                    
                    self.stdout.write(self.style.SUCCESS(f'  Created: {name} ({email})'))
                    created_count += 1
                    
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  Error creating user {name}: {str(e)}'))
                    skipped_count += 1
        
        # Summary
        self.stdout.write(self.style.SUCCESS('\n' + '='*50))
        self.stdout.write(self.style.SUCCESS('Sync Summary:'))
        self.stdout.write(self.style.SUCCESS(f'  Created: {created_count}'))
        self.stdout.write(self.style.WARNING(f'  Updated: {updated_count}'))
        if skipped_count > 0:
            self.stdout.write(self.style.ERROR(f'  Skipped: {skipped_count}'))
        self.stdout.write(self.style.SUCCESS('='*50))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nThis was a dry run. No changes were made.'))
            self.stdout.write(self.style.WARNING('Run without --dry-run to apply changes.'))
