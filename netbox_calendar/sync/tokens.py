"""
Encryption of the tokens kept for calendar connections.

The key is made from NetBox's SECRET_KEY, so a copy of the database alone
doesn't give access to anyone's calendar. Changing SECRET_KEY makes stored
tokens unreadable: the connections then show "Sign-in expired" and users
connect again.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

# Kept apart from other uses of SECRET_KEY.
_PURPOSE = b'netbox-calendar-sync-tokens'


def _fernet():
    digest = hashlib.sha256(_PURPOSE + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


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
