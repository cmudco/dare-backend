# Data migration to set platform_role=SUPERADMIN for existing superusers

from django.db import migrations


def set_superadmin_roles(apps, schema_editor):
    """Set platform_role to SUPERADMIN for all existing superusers."""
    User = apps.get_model('users', 'User')
    updated_count = User.objects.filter(is_superuser=True).update(platform_role='SUPERADMIN')
    if updated_count:
        print(f"  Set platform_role=SUPERADMIN for {updated_count} superuser(s)")


def reverse_superadmin_roles(apps, schema_editor):
    """Reverse: Set platform_role back to USER for superusers."""
    User = apps.get_model('users', 'User')
    User.objects.filter(is_superuser=True, platform_role='SUPERADMIN').update(platform_role='USER')


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0023_add_platform_role_fields'),
    ]

    operations = [
        migrations.RunPython(set_superadmin_roles, reverse_superadmin_roles),
    ]
