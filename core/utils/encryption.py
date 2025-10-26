"""
Encryption utilities for sensitive data storage.

Uses AES-256 encryption with Django's SECRET_KEY as the encryption key.
"""

import base64
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from Crypto.Random import get_random_bytes
from django.conf import settings
import hashlib


def get_encryption_key():
    """
    Derive a 32-byte encryption key from Django's SECRET_KEY.
    Uses SHA-256 to ensure consistent key length.
    """
    return hashlib.sha256(settings.SECRET_KEY.encode()).digest()


def encrypt_value(plaintext: str) -> str:
    """
    Encrypt a plaintext string using AES-256-CBC.

    Args:
        plaintext: The string to encrypt

    Returns:
        Base64-encoded encrypted string with IV prepended
    """
    if not plaintext:
        return ""

    key = get_encryption_key()
    iv = get_random_bytes(AES.block_size)
    cipher = AES.new(key, AES.MODE_CBC, iv)

    # Pad and encrypt
    padded_data = pad(plaintext.encode('utf-8'), AES.block_size)
    encrypted_data = cipher.encrypt(padded_data)

    # Prepend IV to encrypted data and base64 encode
    return base64.b64encode(iv + encrypted_data).decode('utf-8')


def decrypt_value(encrypted_text: str) -> str:
    """
    Decrypt an AES-256-CBC encrypted string.

    Args:
        encrypted_text: Base64-encoded encrypted string with IV prepended

    Returns:
        Decrypted plaintext string
    """
    if not encrypted_text:
        return ""

    key = get_encryption_key()

    # Base64 decode
    encrypted_data = base64.b64decode(encrypted_text.encode('utf-8'))

    # Extract IV and ciphertext
    iv = encrypted_data[:AES.block_size]
    ciphertext = encrypted_data[AES.block_size:]

    # Decrypt
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted_padded = cipher.decrypt(ciphertext)

    # Unpad and decode
    decrypted_data = unpad(decrypted_padded, AES.block_size)
    return decrypted_data.decode('utf-8')
