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

from django.core.management.base import BaseCommand, CommandError

from core.storage.constants import StorageBackendChoice
from files.models import File
from files.utils import (
    get_storage_backend_name,
    get_users_from_identifiers,
    migrate_file_to_target_storage,
)
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
            user_objects = get_users_from_identifiers(users)

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
                self.style.SUCCESS(f'  No files to migrate (all already on {get_storage_backend_name(target_backend)})')
            )
            return 0, 0

        self.stdout.write(f'  Files to migrate: {total_files}')

        files_migrated = 0
        files_failed = 0

        for file_instance in files_to_migrate:
            try:
                if not dry_run:
                    filename = migrate_file_to_target_storage(
                        file_instance, target_backend
                    )
                    logger.info(
                        f'Migrated file {file_instance.id} ({filename}) to {get_storage_backend_name(target_backend)}'
                    )

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
                self.style.SUCCESS(f'  Updated user storage preference to: {get_storage_backend_name(target_backend)}')
            )

        return files_migrated, files_failed

