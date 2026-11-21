"""
Encryption of the tokens kept for calendar connections.

The key is made from NetBox's SECRET_KEY, so a copy of the database alone
doesn't give access to anyone's calendar. Keys made from SECRET_KEY_FALLBACKS
still read older tokens, so SECRET_KEY can be rotated the Django way: set
the new key, move the old one to SECRET_KEY_FALLBACKS, and tokens are
written with the new key from then on. A token no key can read shows its
connection as "Sign-in expired", and the user connects again.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

# Kept apart from other uses of SECRET_KEY.
_PURPOSE = b'netbox-calendar-sync-tokens'


def _key(secret):
    digest = hashlib.sha256(_PURPOSE + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fernet():
    """Encrypts with SECRET_KEY, decrypts with it and every fallback key."""
    secrets = [settings.SECRET_KEY, *getattr(settings, 'SECRET_KEY_FALLBACKS', ())]
    return MultiFernet([_key(secret) for secret in secrets if secret])


def encrypt(text):
    """`text` encrypted, as a string for a TextField. Empty stays empty."""
    if not text:
        return ''
    return _fernet().encrypt(text.encode()).decode()


def decrypt(text):
    """The plain text of encrypt()'s output, or '' when it can't be read."""
    if not text:
        return ''
    try:
        return _fernet().decrypt(text.encode()).decode()
    except (InvalidToken, ValueError):
        return ''
