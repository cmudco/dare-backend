"""
Management command to migrate user files between storage backends.

Usage:
    # Migrate specific user to SyftBox
    python manage.py migrate_storage --user user@example.com --to syftbox

    # Migrate multiple users back to local storage
    python manage.py migrate_storage --user 1 --user 2 --to local

    # Migrate all users to SyftBox (with dry-run)
    python manage.py migrate_storage --all --to syftbox --dry-run

    # Migrate specific users from SyftBox to local
    python manage.py migrate_storage --user user1@example.com --user user2@example.com --to local
"""
import logging
from pathlib import Path
from typing import List

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.storage.constants import StorageBackendChoice
from files.models import File
from users.models import User

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Migrate user files between storage backends (local ↔ SyftBox)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user',
            action='append',
            dest='users',
            help='User ID or email to migrate (can be specified multiple times)',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            dest='all_users',
            help='Migrate all users',
        )
        parser.add_argument(
            '--to',
            type=str,
            required=True,
            choices=['local', 'syftbox'],
            help='Target storage backend (local or syftbox)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            help='Simulate migration without making changes',
        )
        parser.add_argument(
            '--skip-user-preference',
            action='store_true',
            dest='skip_user_preference',
            help='Only migrate files, do not update user storage preference',
        )

    def handle(self, *args, **options):
        users = options.get('users', [])
        all_users = options.get('all_users', False)
        target_backend_str = options['to']
        dry_run = options.get('dry_run', False)
        skip_user_preference = options.get('skip_user_preference', False)

        # Determine target backend
        target_backend = (
            StorageBackendChoice.SYFTBOX
            if target_backend_str == 'syftbox'
            else StorageBackendChoice.LOCAL
        )

        # Validate arguments
        if not users and not all_users:
            raise CommandError('You must specify either --user or --all')

        if users and all_users:
            raise CommandError('Cannot specify both --user and --all')

        # Get users to migrate
        if all_users:
            user_objects = User.objects.all()
            self.stdout.write(
                self.style.WARNING(f'Migrating ALL users to {target_backend_str} storage')
            )
        else:
            user_objects = self._get_users_from_identifiers(users)

        if not user_objects.exists():
            raise CommandError('No users found to migrate')

        # Display migration plan
        total_users = user_objects.count()
        self.stdout.write(
            self.style.NOTICE(f'\nMigration Plan:')
        )
        self.stdout.write(f'  Users to migrate: {total_users}')
        self.stdout.write(f'  Target backend: {target_backend_str}')
        self.stdout.write(f'  Update user preference: {not skip_user_preference}')
        self.stdout.write(f'  Dry run: {dry_run}\n')

        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - No changes will be made\n')
            )

        # Migrate each user
        total_files_migrated = 0
        total_files_failed = 0
        users_migrated = 0
        users_failed = 0

        for user in user_objects:
            try:
                files_migrated, files_failed = self._migrate_user(
                    user, target_backend, dry_run, skip_user_preference
                )
                total_files_migrated += files_migrated
                total_files_failed += files_failed
                users_migrated += 1
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'Failed to migrate user {user.email}: {str(e)}')
                )
                users_failed += 1
                logger.exception(f'Error migrating user {user.id}')

        # Display summary
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.NOTICE('Migration Summary:'))
        self.stdout.write(f'  Users migrated: {users_migrated}/{total_users}')
        self.stdout.write(f'  Users failed: {users_failed}')
        self.stdout.write(f'  Total files migrated: {total_files_migrated}')
        self.stdout.write(f'  Total files failed: {total_files_failed}')

        if dry_run:
            self.stdout.write(
                self.style.WARNING('\nDRY RUN - No changes were made')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS('\nMigration completed!')
            )

    def _get_users_from_identifiers(self, identifiers: List[str]):
        """Get User queryset from list of IDs or emails."""
        from django.db.models import Q

        user_queries = Q()
        for identifier in identifiers:
            # Try as ID first
            if identifier.isdigit():
                user_queries |= Q(id=int(identifier))
            else:
                # Treat as email
                user_queries |= Q(email=identifier)

        return User.objects.filter(user_queries)

    def _migrate_user(
        self,
        user: User,
        target_backend: int,
        dry_run: bool,
        skip_user_preference: bool
    ) -> tuple[int, int]:
        """
        Migrate all files for a user to the target storage backend.

        Returns:
            Tuple of (files_migrated, files_failed)
        """
        self.stdout.write(
            self.style.NOTICE(f'\nMigrating user: {user.email} (ID: {user.id})')
        )

        # Get user's files that need migration
        files_to_migrate = File.active_objects.filter(
            user=user
        ).exclude(
            storage_backend=target_backend
        )

        total_files = files_to_migrate.count()

        if total_files == 0:
            self.stdout.write(
                self.style.SUCCESS(f'  No files to migrate (all already on {self._get_backend_name(target_backend)})')
            )
            return 0, 0

        self.stdout.write(f'  Files to migrate: {total_files}')

        files_migrated = 0
        files_failed = 0

        for file_instance in files_to_migrate:
            try:
                if not dry_run:
                    self._migrate_file(file_instance, target_backend)

                files_migrated += 1
                self.stdout.write(
                    self.style.SUCCESS(f'    ✓ Migrated: {file_instance.name or file_instance.file.name}')
                )
            except Exception as e:
                files_failed += 1
                self.stdout.write(
                    self.style.ERROR(f'    ✗ Failed: {file_instance.name or file_instance.file.name} - {str(e)}')
                )
                logger.exception(f'Error migrating file {file_instance.id}')

        # Update user's storage preference if requested
        if not skip_user_preference and not dry_run:
            user.storage_backend = target_backend
            user.save(update_fields=['storage_backend'])
            self.stdout.write(
                self.style.SUCCESS(f'  Updated user storage preference to: {self._get_backend_name(target_backend)}')
            )

        return files_migrated, files_failed

    def _migrate_file(self, file_instance: File, target_backend: int):
        """
        Migrate a single file to the target storage backend.

        This involves:
        1. Reading the file from current storage
        2. Saving to new storage
        3. Updating the file's storage_backend field
        4. Deleting from old storage (optional, for safety we keep it)
        """
        # Read file content from current storage
        file_instance.file.open('rb')
        file_content = file_instance.file.read()
        file_instance.file.close()

        # Get the filename
        filename = Path(file_instance.file.name).name

        with transaction.atomic():
            # Update storage backend BEFORE saving
            # This ensures DynamicStorageFileField routes to the correct storage
            file_instance.storage_backend = target_backend
            file_instance.save(update_fields=['storage_backend'])

            # Delete old file (from old storage)
            old_file_name = file_instance.file.name
            file_instance.file.delete(save=False)

            # Save to new storage (DynamicStorageFileField will route to correct backend)
            file_instance.file.save(filename, ContentFile(file_content), save=True)

        logger.info(
            f'Migrated file {file_instance.id} ({filename}) to {self._get_backend_name(target_backend)}'
        )

    def _get_backend_name(self, backend: int) -> str:
        """Get human-readable backend name."""
        return 'SyftBox' if backend == StorageBackendChoice.SYFTBOX else 'Local'
