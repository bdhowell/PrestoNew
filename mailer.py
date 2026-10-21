"""Outgoing email over SMTP (a local server or SMTP2Go), or a development mock.

MAIL_BACKEND=console (default) logs messages instead of sending them and keeps
the most recent ones in `outbox`. MAIL_BACKEND=smtp sends through SMTP_HOST
(default localhost), SMTP_PORT (default 25), optional SMTP_USERNAME and
SMTP_PASSWORD, and SMTP_SECURITY=none|starttls|ssl (default none). For SMTP2Go
use mail.smtp2go.com, port 587 or 2525, and starttls. MAIL_FROM is required.
"""

import logging
import os
import smtplib
import ssl
from collections import deque
from email.message import EmailMessage

logger = logging.getLogger(__name__)
outbox: deque[EmailMessage] = deque(maxlen=50)


def send_email(to: str, subject: str, text: str) -> None:
    """Synchronous; call from a threadpool in async code."""
    backend = os.environ.get("MAIL_BACKEND", "console")
    if backend not in ("console", "smtp"):
        raise ValueError("MAIL_BACKEND must be console or smtp")
    message = EmailMessage()
    message["From"] = os.environ.get("MAIL_FROM", "wikkit@localhost")
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    if backend == "console":
        outbox.append(message)
        logger.warning(
            "MAIL_BACKEND=console; email not sent:\nFrom: %s\nTo: %s\nSubject: %s\n\n%s",
            message["From"], to, subject, text,
        )
        return
    if not os.environ.get("MAIL_FROM"):
        raise RuntimeError("MAIL_FROM is required when MAIL_BACKEND=smtp")
    security = os.environ.get("SMTP_SECURITY", "none")
    if security not in ("none", "starttls", "ssl"):
        raise ValueError("SMTP_SECURITY must be none, starttls, or ssl")
    host = os.environ.get("SMTP_HOST", "localhost")
    port = int(os.environ.get("SMTP_PORT", "25"))
    context = ssl.create_default_context()
    smtp = (
        smtplib.SMTP_SSL(host, port, context=context, timeout=30)
        if security == "ssl" else smtplib.SMTP(host, port, timeout=30)
    )
    with smtp:
        if security == "starttls":
            smtp.starttls(context=context)
        username = os.environ.get("SMTP_USERNAME")
        if username:
            smtp.login(username, os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(message)
