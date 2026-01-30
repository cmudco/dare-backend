# Generated migration for platform_role and default_role fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0022_add_avatar_fields'),
    ]

    operations = [
        # Add platform_role to User model
        migrations.AddField(
            model_name='user',
            name='platform_role',
            field=models.CharField(
                choices=[
                    ('SUPERADMIN', 'Super Admin'),
                    ('RESEARCHER', 'Researcher'),
                    ('CREATOR', 'Creator'),
                    ('USER', 'User')
                ],
                default='USER',
                max_length=20,
                verbose_name='Platform Role',
                help_text="User's role across DARE and SocraticBots platforms"
            ),
        ),
        # Add default_role to AccessCodeGroup model
        migrations.AddField(
            model_name='accesscodegroup',
            name='default_role',
            field=models.CharField(
                choices=[
                    ('SUPERADMIN', 'Super Admin'),
                    ('RESEARCHER', 'Researcher'),
                    ('CREATOR', 'Creator'),
                    ('USER', 'User')
                ],
                default='USER',
                max_length=20,
                verbose_name='Default Role',
                help_text='Role assigned to users who register with this access code'
            ),
        ),
    ]
