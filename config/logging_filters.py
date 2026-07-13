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
        record.msg = message
        record.args = ()
        return True
