import logging
import re


PHONE_PATTERN = re.compile(r"(?:\+7|8)[\s\-()]*\d[\d\s\-()]{8,}\d")
FERNET_VALUE_PATTERN = re.compile(r"fernet:[A-Za-z0-9_.-]+:[A-Za-z0-9_=:-]+")
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(secret|token|key|password)\b\s*=\s*[^,\s]+"
)


class SensitiveValueFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        message = PHONE_PATTERN.sub("[phone redacted]", message)
        message = FERNET_VALUE_PATTERN.sub("[encrypted value redacted]", message)
        message = SECRET_ASSIGNMENT_PATTERN.sub(r"\1=[secret redacted]", message)
        record.msg = message
        record.args = ()
        return True
