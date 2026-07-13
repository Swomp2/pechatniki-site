import logging
import re


PHONE_PATTERN = re.compile(r"(?:\+7|8)[\s\-()]*\d[\d\s\-()]{8,}\d")
FERNET_VALUE_PATTERN = re.compile(r"fernet:[A-Za-z0-9_.-]+:[A-Za-z0-9_=:-]+")
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(secret|token|key|password)\b\s*=\s*[^,\s]+"
)
VOTER_COOKIE_PATTERN = re.compile(r"np_problem_voter=[A-Za-z0-9_-]{16,}")
ATTACHMENT_TOKEN_PATH_PATTERN = re.compile(
    r"(/вложения/[0-9a-f-]+/(?:view|download)/)[^\s?]+"
)
ATTACHMENT_TOKEN_SEGMENT_PATTERN = re.compile(
    r"(/(?:view|download)/)[A-Za-z0-9_.:-]{24,}"
)
PUBLIC_PHOTO_TOKEN_PATH_PATTERN = re.compile(
    (
        r"(/вложения/фото/"
        r"|/%D0%B2%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D1%8F"
        r"/%D1%84%D0%BE%D1%82%D0%BE/"
        r"|/\\u0432\\u043b\\u043e\\u0436\\u0435\\u043d\\u0438\\u044f"
        r"/\\u0444\\u043e\\u0442\\u043e/)[^\s?]+"
    ),
    re.IGNORECASE,
)
PUBLIC_PHOTO_TOKEN_SEGMENT_PATTERN = re.compile(
    r"(/фото/)[A-Za-z0-9_.:-]{24,}"
)


class SensitiveValueFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        message = PHONE_PATTERN.sub("[phone redacted]", message)
        message = FERNET_VALUE_PATTERN.sub("[encrypted value redacted]", message)
        message = SECRET_ASSIGNMENT_PATTERN.sub(r"\1=[secret redacted]", message)
        message = VOTER_COOKIE_PATTERN.sub(
            "np_problem_voter=[voter cookie redacted]",
            message,
        )
        message = ATTACHMENT_TOKEN_PATH_PATTERN.sub(
            r"\1[attachment token redacted]",
            message,
        )
        message = ATTACHMENT_TOKEN_SEGMENT_PATTERN.sub(
            r"\1[attachment token redacted]",
            message,
        )
        message = PUBLIC_PHOTO_TOKEN_PATH_PATTERN.sub(
            r"\1[photo token redacted]",
            message,
        )
        message = PUBLIC_PHOTO_TOKEN_SEGMENT_PATTERN.sub(
            r"\1[photo token redacted]",
            message,
        )
        record.msg = message
        record.args = ()
        return True
