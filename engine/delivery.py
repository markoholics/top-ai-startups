"""Send a digest and record what happened.

An event counts as sent only when a digest holding it has delivered = true.
So a failed send leaves its events unsent, and the next run includes them again.
"""
import logging

log = logging.getLogger("jobsignals")


def deliver(store, mailer, digest, to):
    """store needs: log_digest(event_ids, subject) -> id, finish_digest(id, delivered, error).
    Returns True when the email was delivered."""
    digest_id = store.log_digest(digest.event_ids, digest.subject)      # delivered = false until proven
    try:
        mailer.send(to, digest.subject, digest.text, digest.html)
    except Exception as e:                                               # a failed send must never be silent
        log.error("Email failed. %d events stay unsent and will be retried next run. %s",
                  len(digest.event_ids), e)
        store.finish_digest(digest_id, False, str(e))
        return False
    store.finish_digest(digest_id, True, None)
    return True
