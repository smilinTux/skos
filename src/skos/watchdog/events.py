"""The WatchdogEvent type: one shape for every skwatchdog source.

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section 6.2. Neither
existing shape fits: the fleet condition shape (`{type, status, object}`) is
boolean health with no narrative, no timestamp, no link; the fleet event
record is node-scoped, has no severity, no stable cross-source ref, and no
link. WatchdogEvent normalizes every source into one shape so the collector,
the digest, and the C-9 Digest tab in skworld-app all agree on one contract.

    from skos.watchdog.events import WatchdogEvent, WatchdogLink

    ev = WatchdogEvent(
        ts="2026-08-10T06:12:03Z", source="fleet", kind="ServiceCrashLoop",
        object="skchat-daemon@dot41", severity="problem",
        summary="skchat daemon on .41 restarted 4 times between 06:02 and 06:11.",
        link=WatchdogLink(uri="skworld://skchat/ops/daemon", http="https://atlas.skworld.io/"),
        ref="fleet:dot41:2026-08-10T06:12:03Z:ServiceCrashLoop:skchat-daemon",
    )

This module owns only the type. It never reads a source, never writes state,
and never calls a model.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

#: The three severities an adapter may assign, always deterministically, never
#: by a model (spec 6.2: "assigned deterministically by the adapter").
SEVERITIES = ("info", "notable", "problem")


class WatchdogEventError(ValueError):
    pass


@dataclass
class WatchdogLink:
    """Both link forms always present (spec section 8). `uri` is the
    `skworld://<moduleId>/<path>` shell-resolvable form; `http` is the
    working fallback until a resolver exists outside the Flutter shell, and
    is the load-bearing field until then."""
    uri: str = ""
    http: str = ""

    def to_dict(self) -> dict:
        return {"uri": self.uri, "http": self.http}

    @classmethod
    def from_dict(cls, d: dict | None) -> "WatchdogLink":
        if not d:
            return cls()
        return cls(uri=str(d.get("uri") or ""), http=str(d.get("http") or ""))


@dataclass
class WatchdogEvent:
    """One normalized event from any watchdog source.

    Fields mirror spec section 6.2 exactly:
      ts       ISO8601 UTC timestamp string.
      source   fleet | scheduler | itil | coord | autocode | atlas | git |
               skingest | chat.skchat | chat.telegram | email | grading |
               browser (or a synthetic source name for a broken adapter).
      kind     a short event-type tag, adapter-defined (e.g. ServiceCrashLoop).
      object   the thing the event is about (a stable, human-readable id).
      severity info | notable | problem (see SEVERITIES).
      summary  one human sentence, already complete, no further rendering
               needed for the info/notable/problem case.
      link     WatchdogLink (uri + http), see WatchdogLink docstring.
      ref      the stable identity and dedupe key everywhere downstream
               (digest de-duplication, GTD source_ref, card dedupe).
      meta     adapter-specific extra fields, opaque to the digest assembler.
    """
    ts: str
    source: str
    kind: str
    object: str
    severity: str
    summary: str
    link: WatchdogLink = field(default_factory=WatchdogLink)
    ref: str = ""
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise WatchdogEventError(
                f"invalid severity {self.severity!r}; expected one of {SEVERITIES}"
            )
        if isinstance(self.link, dict):
            self.link = WatchdogLink.from_dict(self.link)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["link"] = self.link.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "WatchdogEvent":
        return cls(
            ts=str(d.get("ts") or ""),
            source=str(d.get("source") or ""),
            kind=str(d.get("kind") or ""),
            object=str(d.get("object") or ""),
            severity=str(d.get("severity") or "info"),
            summary=str(d.get("summary") or ""),
            link=WatchdogLink.from_dict(d.get("link")),
            ref=str(d.get("ref") or ""),
            meta=dict(d.get("meta") or {}),
        )


def source_unavailable(source: str, *, ts: str, error: str) -> WatchdogEvent:
    """The one synthetic event every adapter degrades to on any exception
    (spec 6.3): a broken source becomes a visible line in the digest instead
    of a missing digest. Severity is `notable`, never `problem`: the watchdog
    itself failing to read a source is not, by itself, the fleet being on
    fire, and never `info` either, since it is worth a human's eye."""
    return WatchdogEvent(
        ts=ts,
        source=source,
        kind="SourceUnavailable",
        object=source,
        severity="notable",
        summary=f"source {source} was unavailable: {error}",
        ref=f"{source}:source-unavailable:{ts}",
    )
