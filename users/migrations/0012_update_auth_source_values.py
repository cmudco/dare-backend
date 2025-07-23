# Generated migration to update auth_source values from SocraticBooks to SocraticBots

from django.db import migrations

def update_auth_source_values(apps, schema_editor):
    """Update auth_source from SocraticBooks to SocraticBots"""
    User = apps.get_model('users', 'User')
    AccessCodeGroup = apps.get_model('users', 'AccessCodeGroup')
    
    # Update users
    User.objects.filter(auth_source='SocraticBooks').update(auth_source='SocraticBots')
    
    # Update access code groups scope
    AccessCodeGroup.objects.filter(scope='DARE + SocraticBooks').update(scope='DARE + SocraticBots')

def reverse_auth_source_values(apps, schema_editor):
    """Reverse the migration"""
    User = apps.get_model('users', 'User')
    AccessCodeGroup = apps.get_model('users', 'AccessCodeGroup')
    
    # Reverse users
    User.objects.filter(auth_source='SocraticBots').update(auth_source='SocraticBooks')
    
    # Reverse access code groups scope
    AccessCodeGroup.objects.filter(scope='DARE + SocraticBots').update(scope='DARE + SocraticBooks')

class Migration(migrations.Migration):

    dependencies = [
        ('users', '0011_rename_socratic_books_to_bots'),
    ]

    operations = [
        migrations.RunPython(update_auth_source_values, reverse_auth_source_values),
    ]
