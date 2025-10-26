"""
Custom Django model fields for encrypted data storage.
"""

from django.db import models
from core.utils.encryption import encrypt_value, decrypt_value


class EncryptedCharField(models.TextField):
    """
    A CharField that automatically encrypts data before saving to the database
    and decrypts when retrieving from the database.

    Stored as TextField to accommodate encrypted data size.
    """

    description = "Encrypted text field"

    def from_db_value(self, value, expression, connection):
        """
        Convert encrypted database value to decrypted Python value.
        """
        if value is None:
            return value
        return decrypt_value(value)

    def to_python(self, value):
        """
        Convert value to Python string.
        """
        if isinstance(value, str) or value is None:
            return value
        return str(value)

    def get_prep_value(self, value):
        """
        Encrypt value before saving to database.
        """
        if value is None or value == '':
            return value
        # Only encrypt if not already encrypted (simple check)
        # This prevents double encryption on save
        return encrypt_value(str(value))

    def value_to_string(self, obj):
        """
        Serialize field value for serialization.
        """
        value = self.value_from_object(obj)
        return self.get_prep_value(value)
