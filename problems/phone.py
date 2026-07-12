import re

from django.core.exceptions import ValidationError


PHONE_DIGIT_PATTERN = re.compile(r"\D+")


def normalize_russian_phone(value):
    value = (value or "").strip()

    if not value:
        return ""

    digits = PHONE_DIGIT_PATTERN.sub("", value)

    if len(digits) == 10:
        digits = f"7{digits}"
    elif len(digits) == 11 and digits.startswith("8"):
        digits = f"7{digits[1:]}"

    if len(digits) != 11 or not digits.startswith("7"):
        raise ValidationError(
            "Введите российский номер телефона в формате +7XXXXXXXXXX."
        )

    subscriber_number = digits[1:]

    if len(set(subscriber_number)) == 1:
        raise ValidationError("Введите корректный номер телефона.")

    return f"+{digits}"


def validate_russian_phone(value):
    normalize_russian_phone(value)
