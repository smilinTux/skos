"""model_fidelity: an HTTP 200 is not evidence that the thing you think is
answering is answering.

Card 99c33052. On 2026-08-16 node .100 was hard down for about one hour and
forty five minutes and NOTHING in this fleet reported it, because ``sk-default``
kept returning HTTP 200 the whole time. The 200s were real. They were served
from NVIDIA's cloud, because skgateway had quietly failed over, and every
health check in the path was asking "did I get a response" rather than "who
answered". A silent failover hid a hardware outage for four hours.

The signal that a cloud 200 cannot fake is the identity of the responder.
skgateway now stamps ``x-sk-model-served`` on every completion and its
``skgateway-catalog-verify`` job probes each configured ROLE, compares the
model that actually answered against the model the role resolves to, and
writes the comparison to disk. This adapter is the CONSUMER of that comparison.

ONE PROBER, NOT TWO
-------------------
This adapter deliberately does not probe anything. It makes no HTTP request,
starts no completion, and never talks to skgateway. skgateway owns the probe
and stays the single prober, because two probes of the same fact drift, and a
drift between two liveness signals is worse than one signal: both keep
answering confidently and nothing compares them. This fleet has produced four
separate instances of exactly that failure in a single week. So the contract
here is strictly read-a-file, and the file is the artifact skgateway already
publishes.

    ``~/.skcapstone/gateway/catalog-verify.json``   written by the
                                                    ``skgateway-catalog-verify``
                                                    job, ``0 7 * * *`` on
                                                    noroc2027, so roughly daily.

THE ARTIFACT, AND THE THREE NULL-ISH STATES THAT ARE NOT EACH OTHER
------------------------------------------------------------------
``artifact_version 1``::

    {artifact_version, finished_at, endpoint, checked, error, drift,
     role_fidelity: {alarm, entries[], mismatches[]} | null,
     liveness: {dead_count, dead[]},
     failover: {live_count, alarm}}

Three different things in this document look like absence and mean entirely
different things. Collapsing any two of them would either manufacture a false
accusation or manufacture a false all-clear, so each is handled separately and
on purpose:

  ``role_fidelity: null``   NO RESULT EXISTS. Nothing was assessed. This is
                            NOT an empty ``entries`` list: an empty list means
                            the probe ran and found nothing wrong, whereas
                            null means the probe produced no answer at all.
                            Null is therefore a GAP, and it is reported as one.
  ``faithful: null``        THAT ROLE ERRORED, so fidelity was not assessable
                            for it. It is ordinary liveness, not a
                            substitution. Reporting an errored probe as "a
                            different model answered" is a false accusation
                            against a backend that simply did not reply, and a
                            watchdog that cries substitution is worse than one
                            that says plainly "I could not tell".
  ``checked: false``        A REAL, DELIBERATE STATE, always carrying a
                            populated ``error``. An unreachable gateway still
                            writes an artifact rather than leaving a stale one
                            in place, precisely so there is no state where the
                            job runs, fails to check, and leaves something
                            readable as clean. So ``checked: false`` is a gap,
                            never an all-clear, and the rest of the document
                            (liveness, failover, drift) is not evidence about
                            anything when it is false.

STALENESS IS THIS ADAPTER'S JOB, AND IT IS THE POINT OF THE CARD
----------------------------------------------------------------
The artifact deliberately carries NO freshness verdict of its own. That
omission is correct: a producer asserting its own freshness is a
self-certifying gate, and this fleet's whole failure mode is a component that
reports on itself and is believed. ``finished_at`` is ALWAYS present, including
on the ``checked: false`` path, so ageing it is always possible and is the
consumer's job.

The rule: an artifact older than :data:`DEFAULT_MAX_AGE_H` hours (26h,
overridable via ``SKWATCHDOG_MODEL_FIDELITY_MAX_AGE_H``) is STALE, and a stale
artifact is a gap that short-circuits everything else. 26h is the producer's
daily cadence (24h) plus a two hour grace, so a digest that runs a little
earlier than usual still accepts yesterday's on-time artifact, while a single
MISSED producer run is reported the next morning rather than being papered over
with day-old numbers. A stale document is not evidence about the present:
rendering "5 roles faithful" out of a three day old file is precisely the false
all-clear that let .100 sit dark, so a stale artifact never reaches the
interpretation code at all.

WHAT EARNS A `problem`, AND WHAT DELIBERATELY DOES NOT
------------------------------------------------------
``problem`` files a GTD item under WD-8 and can escalate to a staged coord card
under WD-9, so anything that fires every morning does not merely annoy a
reader, it manufactures work every morning and trains the reader to skim the
whole section.

  problem   any entry with ``faithful is False``: a role was answered by a
            model it does not resolve to. That is the .100 signature exactly.
            Also ``role_fidelity.alarm`` true, which is honoured even when no
            individual entry explains it (the producer knows something this
            reader did not parse; disagreeing silently with the producer is how
            two sources of truth are born).
  notable   an errored role (``faithful is None``), and ``failover.alarm``.
  gap       artifact missing, unreadable, unparseable, an unknown
            ``artifact_version``, an unparseable or absent ``finished_at``,
            stale, ``checked: false``, or ``role_fidelity: null``. Every one of
            these is a ``SourceUnavailable`` line, which is ``notable``, never
            silence and never a false all-clear.
  nothing   ``drift`` and ``liveness.dead_count``. The real artifact on this
            fleet carries ``dead_count: 9`` and ``drift: true`` on a day when
            ``role_fidelity.alarm`` and ``failover.alarm`` are both false: nine
            third-party cloud models timing out is the steady state, and drift
            between the advertised catalog and what answers is a catalog
            hygiene fact, not an outage. Raising either as a ``problem`` would
            put an alarm in the digest every single day, and a daily alarm is
            an alarm nobody reads. Both are carried in ``meta`` and named in
            the summary sentence so they stay VISIBLE without carrying a
            severity.

REFUSING AN UNKNOWN VERSION
---------------------------
Following ``skos.watchdog.rubric``'s ``SCHEMA_VERSION`` convention, which the
producer also followed: a document whose ``artifact_version`` this reader does
not know is REFUSED outright rather than best-effort parsed. A future shape
change must never be silently misread as today's shape, because misreading it
would most likely land on "nothing wrong here".

THE SEAM
--------
``default_artifact_path()`` is the ONLY place this module resolves the
operator's real artifact, it is injectable
(``ModelFidelityAdapter(artifact_path=...)``) and it is resolved at call time
so ``monkeypatch.setattr`` on the module global also works. No test in this
repo ever reads the live path, ever invokes node, and ever touches skgateway.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from ..events import WatchdogEvent, WatchdogLink, source_unavailable
from ..port import Window, WatchdogSourceAdapter, registry

#: The ONE artifact shape this reader understands. See "REFUSING AN UNKNOWN
#: VERSION" above: a document declaring anything else is a gap, never a guess.
SUPPORTED_ARTIFACT_VERSION = 1

#: Producer cadence (24h, `0 7 * * *`) plus a two hour grace. See "STALENESS"
#: above for why the grace is small and why one missed run must be reported.
DEFAULT_MAX_AGE_H = 26.0

#: The artifact's own path, relative to the skcapstone home.
ARTIFACT_RELPATH = ("gateway", "catalog-verify.json")


def default_artifact_path() -> Path:
    """Where skgateway's ``catalog-verify`` job writes its comparison.

    ``SKWATCHDOG_MODEL_FIDELITY_ARTIFACT`` points this somewhere else outright;
    otherwise it follows ``SKCAPSTONE_HOME`` exactly like the scheduler
    adapter's ledger lookup, so the two never disagree about where fleet state
    lives.
    """
    env = os.environ.get("SKWATCHDOG_MODEL_FIDELITY_ARTIFACT")
    if env:
        return Path(env).expanduser()
    home = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone")))
    return home.joinpath(*ARTIFACT_RELPATH)


def max_age_s() -> float:
    """The staleness threshold in seconds. An unparseable or non-positive
    override falls back to the default rather than disabling the check: a
    typo in an env var must never be a way to turn the freshness gate off."""
    raw = os.environ.get("SKWATCHDOG_MODEL_FIDELITY_MAX_AGE_H", "")
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        hours = DEFAULT_MAX_AGE_H
    if not hours > 0:
        hours = DEFAULT_MAX_AGE_H
    return hours * 3600.0


def _epoch(iso_ts) -> Optional[float]:
    """ISO8601 to epoch seconds, or None when it cannot be read. None is
    never treated as fresh; see `collect`."""
    try:
        text = str(iso_ts).strip()
    except Exception:  # noqa: BLE001 - defensive against an exotic JSON value
        return None
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _fmt_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "an unknown time"
    seconds = max(0.0, float(seconds))
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


class ArtifactUnusable(RuntimeError):
    """The whole document cannot be trusted to say anything about now.

    Raised out of ``collect()`` so ``collect_safe`` degrades this ONE source to
    a single ``SourceUnavailable`` line. Deliberately not caught and turned
    into a partial read: when the artifact is missing, stale, or explicitly
    unchecked, there is no half of it that is still evidence.
    """


@registry.register
class ModelFidelityAdapter(WatchdogSourceAdapter):
    """Consume skgateway's role-fidelity artifact. Probe nothing."""

    name = "model_fidelity"

    def __init__(self, artifact_path: Optional[Union[str, Path]] = None) -> None:
        self._artifact_path = artifact_path

    # ----------------------------------------------------------------
    # reading
    # ----------------------------------------------------------------

    def _path(self) -> Path:
        if self._artifact_path is not None:
            return Path(self._artifact_path)
        # Module global, resolved at call time, so a test's
        # monkeypatch.setattr on `default_artifact_path` is honored.
        return default_artifact_path()

    def _load(self, path: Path) -> dict:
        """Read + version-check the artifact. Every failure here is a gap."""
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ArtifactUnusable(
                f"skgateway's role-fidelity artifact is missing at {path}: the "
                f"skgateway-catalog-verify job has not written one, so nothing "
                f"is checking that the model answering a role is the model the "
                f"role resolves to. A missing artifact is not an all-clear.")
        except OSError as exc:
            raise ArtifactUnusable(
                f"could not read the role-fidelity artifact at {path}: {exc}")

        try:
            doc = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ArtifactUnusable(
                f"the role-fidelity artifact at {path} did not parse as JSON "
                f"({exc}); it says nothing about model fidelity either way.")

        if not isinstance(doc, dict):
            raise ArtifactUnusable(
                f"the role-fidelity artifact at {path} is a "
                f"{type(doc).__name__}, not an object.")

        version = doc.get("artifact_version")
        if version != SUPPORTED_ARTIFACT_VERSION:
            raise ArtifactUnusable(
                f"refusing role-fidelity artifact_version {version!r} at "
                f"{path}: this reader understands version "
                f"{SUPPORTED_ARTIFACT_VERSION} only. Guessing at an unknown "
                f"shape would most likely guess 'nothing wrong'.")
        return doc

    # ----------------------------------------------------------------
    # collecting
    # ----------------------------------------------------------------

    def collect(self, window: Window) -> list[WatchdogEvent]:
        path = self._path()
        doc = self._load(path)
        date = window.until[:10]
        link = WatchdogLink(uri="skworld://skgateway/catalog-verify",
                            http=f"file://{path}")

        # --- freshness, before anything in the document is believed ---
        finished_at = doc.get("finished_at")
        produced = _epoch(finished_at)
        if produced is None:
            raise ArtifactUnusable(
                f"the role-fidelity artifact at {path} has no readable "
                f"finished_at ({finished_at!r}), so its age cannot be "
                f"established and its contents cannot be trusted to describe "
                f"now.")
        now_s = _epoch(window.until)
        # An unreadable window bound is the watchdog's own problem, not the
        # artifact's; fall back to wall clock rather than skipping the check.
        if now_s is None:
            now_s = datetime.now(timezone.utc).timestamp()
        age_s = max(0.0, now_s - produced)   # clamp: clock skew is not the future
        limit_s = max_age_s()
        if age_s > limit_s:
            raise ArtifactUnusable(
                f"skgateway's role-fidelity artifact is STALE: written "
                f"{finished_at} ({_fmt_age(age_s)} ago, threshold "
                f"{_fmt_age(limit_s)}). Its numbers describe a moment that has "
                f"passed, so nothing in it is evidence that the model "
                f"answering a role right now is the model the role resolves "
                f"to. A stale artifact is a gap, never an all-clear.")

        # --- the deliberate unchecked state ---
        if doc.get("checked") is not True:
            err = doc.get("error") or "no error was recorded"
            endpoint = doc.get("endpoint") or "the gateway"
            raise ArtifactUnusable(
                f"the role-fidelity job ran but could not check {endpoint}: "
                f"{err}. checked=false is a real state the producer writes on "
                f"purpose so an unreachable gateway is never mistaken for a "
                f"clean one; nothing was verified this run.")

        out: list[WatchdogEvent] = []
        drift = bool(doc.get("drift"))
        dead_count, dead = self._liveness(doc)
        live_count, failover_alarm = self._failover(doc)

        # --- failover: notable, never problem (see module docstring) ---
        if failover_alarm:
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="FailoverAlarm",
                object="skgateway-failover", severity="notable",
                summary=(f"skgateway raised its failover alarm: {live_count} "
                         f"live failover backend(s). Fidelity itself is "
                         f"reported separately; this is the capacity to fail "
                         f"over at all."),
                link=link, ref=f"{self.name}:failover:alarm:{date}",
                meta={"live_count": live_count, "dead_count": dead_count},
            ))

        # --- role fidelity ---
        rf = doc.get("role_fidelity", "__absent__")
        if rf is None or rf == "__absent__":
            # NOT an empty entries list. Null means no result exists, so
            # nothing was assessed and the fidelity question is unanswered.
            # A per-aspect gap rather than a raise, mirroring the systemd
            # adapter's per-scope gap: liveness and failover above were still
            # read from a fresh, checked document and are still worth saying.
            out.append(source_unavailable(
                f"{self.name}:role_fidelity", ts=window.until,
                error=("the artifact carries role_fidelity: null, so no "
                       "fidelity result exists for this run. That is not an "
                       "empty result list; nothing was compared, so no role "
                       "is known to be answered by the model it resolves to.")))
            return out
        if not isinstance(rf, dict) or "entries" not in rf:
            out.append(source_unavailable(
                f"{self.name}:role_fidelity", ts=window.until,
                error=(f"role_fidelity is {type(rf).__name__} and does not "
                       f"carry an entries list, which artifact_version "
                       f"{SUPPORTED_ARTIFACT_VERSION} promises.")))
            return out

        entries = rf.get("entries")
        if entries is None:
            entries = []
        if not isinstance(entries, list):
            out.append(source_unavailable(
                f"{self.name}:role_fidelity", ts=window.until,
                error=f"role_fidelity.entries is {type(entries).__name__}, not a list."))
            return out

        alarm = bool(rf.get("alarm"))
        mismatches = rf.get("mismatches") or []
        faithful, errored, substituted = [], [], []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            role = str(entry.get("role") or "an unnamed role")
            verdict = entry.get("faithful")
            if verdict is False:
                substituted.append(entry)
                out.append(self._substitution_event(entry, role, window, date, link))
            elif verdict is None:
                errored.append(entry)
                out.append(self._errored_event(entry, role, window, date, link))
            else:
                faithful.append(entry)

        if (alarm or mismatches) and not substituted:
            # The producer says something is wrong and no entry explains it.
            # Honour the producer rather than quietly overruling it: a
            # consumer that disagrees in silence is how one fact becomes two.
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="RoleFidelityAlarm",
                object="role-fidelity", severity="problem",
                summary=(f"skgateway raised its role-fidelity alarm "
                         f"(alarm={alarm}, {len(mismatches)} recorded "
                         f"mismatch(es)) but no individual role entry is "
                         f"marked unfaithful. The producer saw something this "
                         f"reader could not attribute to a role; read the "
                         f"artifact directly."),
                link=link, ref=f"{self.name}:role-fidelity:alarm:{date}",
                meta={"alarm": alarm, "mismatches": mismatches,
                      "entries": len(entries)},
            ))

        if not any(e.severity == "problem" for e in out):
            note = ""
            if errored:
                note = (f" {len(errored)} role(s) errored, so fidelity was not "
                        f"assessable for them.")
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="RoleFidelityHealthy",
                object="role-fidelity", severity="info",
                summary=(f"{len(faithful)} of {len(entries)} gateway role(s) "
                         f"were answered by the model they resolve to "
                         f"(x-sk-model-served matched)."
                         f"{note} Artifact {_fmt_age(age_s)} old; "
                         f"{dead_count} catalog model(s) unreachable, "
                         f"catalog drift {'yes' if drift else 'no'}."),
                link=link, ref=f"{self.name}:summary:{date}",
                meta={"faithful": len(faithful), "errored": len(errored),
                      "entries": len(entries), "drift": drift,
                      "dead_count": dead_count, "dead": dead,
                      "live_count": live_count, "age_s": round(age_s, 1),
                      "finished_at": finished_at},
            ))
        return out

    # ----------------------------------------------------------------
    # per-entry events
    # ----------------------------------------------------------------

    def _substitution_event(self, entry: dict, role: str, window: Window,
                            date: str, link: WatchdogLink) -> WatchdogEvent:
        """`faithful: False`. The .100 signature: a 200 came back, and somebody
        other than the configured model produced it."""
        expected = entry.get("expected") or "an unknown model"
        served = entry.get("served") or "an unidentified model"
        backend = entry.get("backend") or "an unknown backend"
        return WatchdogEvent(
            ts=window.until, source=self.name, kind="RoleModelSubstituted",
            object=role, severity="problem",
            summary=(f"gateway role {role} resolves to {expected} on backend "
                     f"{backend} but {served} answered it. The request "
                     f"succeeded, which is exactly why nothing else noticed: a "
                     f"200 served by a substitute looks identical to a healthy "
                     f"one until you read x-sk-model-served."),
            link=link, ref=f"{self.name}:{role}:substituted:{date}",
            meta={"role": role, "backend": backend, "expected": expected,
                  "served": served},
        )

    def _errored_event(self, entry: dict, role: str, window: Window,
                       date: str, link: WatchdogLink) -> WatchdogEvent:
        """`faithful: null`. The role errored, so fidelity was NOT assessable.

        Notable, never a problem, and the wording never claims a substitution.
        Calling an errored probe "a different model answered" would be a false
        accusation against a backend that simply did not reply.
        """
        error = str(entry.get("error") or "no detail recorded")
        expected = entry.get("expected") or "an unknown model"
        backend = entry.get("backend") or "an unknown backend"
        return WatchdogEvent(
            ts=window.until, source=self.name, kind="RoleProbeErrored",
            object=role, severity="notable",
            summary=(f"gateway role {role} (expected {expected} on {backend}) "
                     f"errored during the fidelity probe: {error}. Fidelity "
                     f"was not assessable for this role, so no claim is made "
                     f"either way about which model would have answered."),
            link=link, ref=f"{self.name}:{role}:errored:{date}",
            meta={"role": role, "backend": backend, "expected": expected,
                  "error": error},
        )

    # ----------------------------------------------------------------
    # the two sections carried but never escalated on their own
    # ----------------------------------------------------------------

    @staticmethod
    def _liveness(doc: dict) -> tuple[int, list]:
        block = doc.get("liveness")
        if not isinstance(block, dict):
            return 0, []
        dead = block.get("dead")
        dead = dead if isinstance(dead, list) else []
        try:
            count = int(block.get("dead_count", len(dead)))
        except (TypeError, ValueError):
            count = len(dead)
        return count, dead

    @staticmethod
    def _failover(doc: dict) -> tuple[int, bool]:
        block = doc.get("failover")
        if not isinstance(block, dict):
            return 0, False
        try:
            live = int(block.get("live_count", 0))
        except (TypeError, ValueError):
            live = 0
        return live, bool(block.get("alarm"))


__all__ = [
    "ModelFidelityAdapter", "ArtifactUnusable", "default_artifact_path",
    "max_age_s", "SUPPORTED_ARTIFACT_VERSION", "DEFAULT_MAX_AGE_H",
]
