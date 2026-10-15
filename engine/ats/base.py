from dataclasses import dataclass
from typing import Optional


@dataclass
class Job:
    ats_job_id: str
    title: str
    location: str
    url: str
    updated_at: Optional[str] = None
    company_name: Optional[str] = None   # when the ATS reports it, used to check a guessed slug


class BoardNotFound(Exception):
    """The token does not exist on this ATS."""


class BoardError(Exception):
    """The ATS failed or sent something unexpected. Not the same as 'not found'."""
