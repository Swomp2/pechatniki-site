import re
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test.signals import setting_changed


ENCRYPTED_VALUE_PREFIX = "fernet"
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _load_fernet():
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError as exc:
        raise ImproperlyConfigured(
            "cryptography is required for encrypted model fields."
        ) from exc

    return Fernet, InvalidToken


def is_encrypted_value(value):
    return isinstance(value, str) and value.startswith(f"{ENCRYPTED_VALUE_PREFIX}:")


def parse_key_definition(raw_value):
    if ":" not in raw_value:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEYS entries must use the format key-id:fernet-key."
        )

    key_id, key = raw_value.split(":", 1)
    key_id = key_id.strip()
    key = key.strip()

    if not key_id or not KEY_ID_PATTERN.match(key_id):
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEYS key ids may contain only letters, digits, '.', '_' and '-'."
        )

    if not key:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEYS contains an empty key.")

    Fernet, _ = _load_fernet()

    try:
        Fernet(key.encode("ascii"))
    except Exception as exc:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEYS contains an invalid Fernet key."
        ) from exc

    return key_id, key


@lru_cache(maxsize=1)
def get_field_encryption_keys():
    raw_keys = getattr(settings, "FIELD_ENCRYPTION_KEYS", [])
    parsed_keys = []

    for raw_key in raw_keys:
        raw_key = raw_key.strip()

        if raw_key:
            parsed_keys.append(parse_key_definition(raw_key))

    return tuple(parsed_keys)


def clear_field_encryption_key_cache(**kwargs):
    if kwargs.get("setting") == "FIELD_ENCRYPTION_KEYS":
        get_field_encryption_keys.cache_clear()


setting_changed.connect(clear_field_encryption_key_cache)


def get_active_key():
    keys = get_field_encryption_keys()

    if not keys:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEYS is required.")

    return keys[0]


def encrypt_text(value):
    if value in (None, ""):
        return value

    value = str(value)

    if is_encrypted_value(value):
        return value

    Fernet, _ = _load_fernet()
    key_id, key = get_active_key()
    token = Fernet(key.encode("ascii")).encrypt(value.encode("utf-8")).decode("ascii")

    return f"{ENCRYPTED_VALUE_PREFIX}:{key_id}:{token}"


def decrypt_text(value):
    if value in (None, ""):
        return value

    value = str(value)

    if not is_encrypted_value(value):
        # Existing plaintext values are allowed so a migration can encrypt them
        # in place after the field type changes.
        return value

    parts = value.split(":", 2)

    if len(parts) != 3 or parts[0] != ENCRYPTED_VALUE_PREFIX:
        raise ImproperlyConfigured("Encrypted field value has an unsupported format.")

    stored_key_id, token = parts[1], parts[2]
    Fernet, InvalidToken = _load_fernet()
    keys = get_field_encryption_keys()

    if not keys:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEYS is required.")

    preferred_keys = [item for item in keys if item[0] == stored_key_id]
    fallback_keys = [item for item in keys if item[0] != stored_key_id]

    for _, key in preferred_keys + fallback_keys:
        try:
            return Fernet(key.encode("ascii")).decrypt(
                token.encode("ascii"),
            ).decode("utf-8")
        except InvalidToken:
            continue

    raise ImproperlyConfigured(
        "Encrypted field value cannot be decrypted with configured keys."
    )


class EncryptedTextField(models.TextField):
    description = "Text encrypted with Fernet before storing it in the database"

    def from_db_value(self, value, expression, connection):
        return decrypt_text(value)

    def to_python(self, value):
        return decrypt_text(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt_text(value)

    def value_to_string(self, obj):
        value = self.value_from_object(obj)
        return "" if value is None else str(value)
