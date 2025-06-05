from django.db.models.signals import pre_save
from django.dispatch import receiver
from users.models import AccessCodeGroup


@receiver(pre_save, sender=AccessCodeGroup)
def handle_access_code_group_activation(sender, instance, **kwargs):
    """
    Signal to handle user activation/deactivation when AccessCodeGroup is_active field changes.
    """
    if instance.pk:  # Only for existing instances (updates)
        try:
            # Get the current state from database
            old_instance = AccessCodeGroup.objects.get(pk=instance.pk)

            # Check if is_active field changed
            if old_instance.is_active != instance.is_active:
                if instance.is_active:
                    # Access code group is being activated - reactivate users
                    instance.reactivate_all_users()
                else:
                    # Access code group is being deactivated - deactivate users
                    instance.deactivate_all_users()

        except AccessCodeGroup.DoesNotExist:
            # This shouldn't happen, but handle gracefully
            pass
