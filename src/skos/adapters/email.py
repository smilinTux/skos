"""Email → GTD pull adapter: Gmail `1 Action`/`2 Waiting` labels → unified GTD.

The heavy lifting (multi-box capture, LLM triage, digest, bidirectional reply/
done/attachments) lives in `skos.mail`; this adapter exposes the *capture* half on
the gtd-ingest port so `skos ingest email` behaves like the calendar/telegram ones.
"""
from __future__ import annotations

from ..gtd_ingest import GtdSourceAdapter


class EmailAdapter(GtdSourceAdapter):
    name = "email"

    def poll(self):
        from .. import mail
        return mail.email_captures()
