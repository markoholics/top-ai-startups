"""Gmail SMTP transport. Uses an app password, never your normal password.
Reads SMTP_USER and SMTP_PASSWORD from the environment (GitHub secrets in CI)."""
import os
import smtplib
import ssl
from email.message import EmailMessage

from ..config import load


class MailError(Exception):
    pass


class GmailSMTP:
    def __init__(self):
        cfg = load("config.yaml")["email"]
        self.host, self.port = cfg["smtp_host"], int(cfg["smtp_port"])
        self.user = os.environ.get("SMTP_USER", "")
        self.password = os.environ.get("SMTP_PASSWORD", "")

    def _clean(self, message):
        """Never let the password reach a log."""
        text = str(message)
        return text.replace(self.password, "***") if self.password else text

    def send(self, to, subject, text, html=None):
        if not self.user or not self.password:
            raise MailError("SMTP_USER or SMTP_PASSWORD is not set.")
        if not to:
            raise MailError("NOTIFY_TO is not set.")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.user, to, subject
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(self.user, self.password)
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise MailError(self._clean(f"{type(e).__name__}: {e}"))
