import re


MAX_ERROR_MESSAGE_LENGTH = 2000

_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"\b(api[\s_-]*key|access[\s_-]*token|refresh[\s_-]*token|password|secret)"
    r"\b\s*[:=]\s*[\"']?[^\"'\s,&;]+[\"']?",
    re.IGNORECASE,
)
_SENSITIVE_HEADER_PATTERN = re.compile(
    r"\b(authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*[^\r\n]+",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?82[- ]?)?0(?:10|2|[3-6]\d|70|80)[- ]?\d{3,4}[- ]?\d{4}(?!\d)"
)


def sanitize_error_message(error_message: str | None) -> str | None:
    if error_message is None:
        return None

    sanitized_message = _URL_PATTERN.sub('[URL REDACTED]', error_message)
    sanitized_message = _BEARER_PATTERN.sub('Bearer [REDACTED]', sanitized_message)
    sanitized_message = _SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: f'{match.group(1)}=[REDACTED]',
        sanitized_message,
    )
    sanitized_message = _SENSITIVE_HEADER_PATTERN.sub(
        lambda match: f'{match.group(1)}=[REDACTED]',
        sanitized_message,
    )
    sanitized_message = _EMAIL_PATTERN.sub('[EMAIL REDACTED]', sanitized_message)
    sanitized_message = _PHONE_PATTERN.sub('[PHONE REDACTED]', sanitized_message)
    sanitized_message = ' '.join(sanitized_message.split())
    return sanitized_message[:MAX_ERROR_MESSAGE_LENGTH] or None
