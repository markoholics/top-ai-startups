"""Email transport adapters. The transport is chosen in config/config.yaml
under email.transport. Adding another sender means adding one file here with a
class that has send(to, subject, text, html), then listing it below."""
from ..config import load
from .gmail_smtp import GmailSMTP

TRANSPORTS = {"gmail_smtp": GmailSMTP}


def get_mailer():
    name = load("config.yaml")["email"]["transport"]
    if name not in TRANSPORTS:
        raise RuntimeError(f"Unknown email transport '{name}'. Known: {', '.join(TRANSPORTS)}")
    return TRANSPORTS[name]()
