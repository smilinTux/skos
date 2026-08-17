"""systemd_tier_a: Tier A does not mean running, so go and check that it is.

Card 04ad64d7. The ``SERVICE_UNIT_STANDARD`` Tier A drop-in carries the
comment "must never permanently die" and then implements that promise with
``Restart=on-failure``. A deliberate ``systemctl stop`` is not a failure, so
the single most likely way a service actually goes away, something stopped
it, walks straight through the entire Tier A guarantee. Measured: skgateway
was cleanly stopped on 2026-08-15 at 00:41:00 (``Result=success``, exit 0)
and stayed down until a human started it by hand. Nothing restarted it,
nothing complained, and nothing was ever going to.

So Tier A today means "will be restarted if it CRASHES". The fleet reads it
as "will be running". This adapter closes that gap by measuring the second
claim directly.

STATE BASED, NOT EVENT BASED
---------------------------
A stopped unit emits nothing. There is no journal line to subscribe to, no
``Restart=`` to fire, no failure to catch, which is precisely why the
existing machinery misses it. So this adapter periodically compares ACTUAL
state against a DESIRED state set, on a schedule, and reports the
difference. A watchdog that waits to be told will wait forever.

WHERE THE DESIRED SET COMES FROM
--------------------------------
From the units that actually carry the Tier A marker, never from a
hand-maintained list in this repo (a list would rot the first time an
operator added a Tier A service, and it would rot silently, which is the
only kind of rot that matters here).

Derivation, per scope (``user`` then ``system``):

  1. ``systemctl [--user] list-unit-files --type=service`` enumerates every
     INSTALLED service unit file. This is deliberately not
     ``systemctl show '*.service'``: that glob only covers units currently
     LOADED in memory, and systemd garbage-collects an inactive unit that
     nothing references. Measured on this node: four ``enabled`` user units
     were absent from the glob and present in ``list-unit-files``. An
     enumeration that cannot see a stopped unit is useless to a watchdog
     whose whole subject is stopped units.
  2. ``systemctl [--user] show <every unit>`` reads ``UnitFileState``,
     ``ActiveState``, ``SubState``, ``Type``, ``RemainAfterExit``,
     ``FragmentPath`` and ``DropInPaths`` in one call. systemd itself is
     the authority on which files compose a unit.
  3. A unit is Tier A when the MARKER string appears anywhere in its
     fragment file or in any of its drop-in files, as reported by systemd in
     step 2.

That third step is what survives the marker moving. The marker is looked for
in whatever files systemd says compose the unit, so it can move between
``restart-storm.conf`` and ``override.conf``, move out of
``~/.config/systemd/user`` into ``/etc/systemd/system``, or be renamed to
some other drop-in file entirely, and this adapter still finds it. What it
cannot survive is the marker TEXT changing, and it must not survive that
quietly: if the derivation yields ZERO Tier A units anywhere, this adapter
raises, which ``collect_safe`` turns into a ``SourceUnavailable`` line in
the digest. An empty desired set is reported as a gap, never as an all-clear.

WHAT EARNS A `problem`, AND WHAT DELIBERATELY DOES NOT
------------------------------------------------------
``enabled + not running`` is the signal. ``disabled + inactive`` is not.

Verified on this node: ``shadowcopy-backfill.service`` and
``shadowcopy-monitor.service`` are both inactive AND disabled, which is a
human having deliberately turned them off. Reporting a deliberate choice as
a problem every single morning trains the reader to skim past the whole
section, and a watchdog nobody reads is worse than no watchdog, because it
also carries the belief that someone is watching. So only
``UnitFileState`` in ``enabled``/``enabled-runtime`` puts a unit in the
desired-running set: ``disabled``, ``masked``, ``static``, ``indirect``,
``generated`` and friends are all out. (``static`` in particular has no
``[Install]`` section at all; it cannot be enabled, so its resting state
carries no operator intent to violate.)

One more deliberate exclusion: a ``Type=oneshot`` unit with
``RemainAfterExit=no`` is a run-to-completion job whose NORMAL resting state
is inactive. Flagging those would manufacture a problem every morning for
every Tier A oneshot. ``Type=oneshot`` WITH ``RemainAfterExit=yes`` stays in
scope (it holds ``active`` after its run, e.g. ``skchat-coturn.service`` on
this node), because for that shape inactive really does mean gone.

Severities, and why the discipline matters: a Tier A unit that is enabled
and not running is a ``problem``. A non-Tier-A unit being down is not this
adapter's business at all. Anything that stops this adapter from SEEING
systemd (not Linux, no ``systemctl``, every scope unreadable, or an empty
Tier A set) is a ``SourceUnavailable`` gap, which is ``notable``, never
silence and never a false all-clear. ``problem`` files a GTD item under WD-8
and can escalate to a staged coord card under WD-9, so a flaky detector here
does not merely annoy a reader, it manufactures work every morning.

THE ALERT PATH IS NOT ASSUMED TO WORK
-------------------------------------
``sk-alert`` has never once successfully fired from a scheduler in this
fleet (an unreachable PATH plus a piped message it cannot read), so this
adapter deliberately does not invent a notification of its own. It emits
ordinary ``WatchdogEvent``s onto the same port every other source uses, so
the finding travels the digest path that is already exercised end to end:
collect, assemble, render, publish, DM. A watchdog whose alarm silently
no-ops is the same failure this card exists to fix, one layer up.

THE SEAM
--------
``default_unit_reader(scope)`` is the ONLY place this module talks to
systemd, and it is injectable (``SystemdTierAAdapter(unit_reader=...)``,
resolved at call time so ``monkeypatch.setattr`` on the module global also
works). No test in this repo ever runs a real ``systemctl`` or touches a
real unit.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from ..events import WatchdogEvent, WatchdogLink, source_unavailable
from ..port import Window, WatchdogSourceAdapter, registry

#: The exact text a Tier A drop-in carries. Written by SERVICE_UNIT_STANDARD
#: as `# SERVICE_UNIT_STANDARD Tier A: backoff only (must never permanently
#: die).` The Tier B line is deliberately NOT matched: Tier B is "backoff +
#: limiter (leaf app)", a service that is allowed to give up.
TIER_A_MARKER = "SERVICE_UNIT_STANDARD Tier A"

#: The systemd scopes to read, in order. Overridable via
#: SKWATCHDOG_SYSTEMD_SCOPES (comma separated) for a node where one scope is
#: not readable at all and its permanent SourceUnavailable line would be pure
#: noise rather than information.
DEFAULT_SCOPES = ("user", "system")

#: UnitFileState values that mean "a human asked for this to be running".
#: Everything else (disabled, masked, static, indirect, generated, linked,
#: transient, bad, or empty) carries no such intent. See module docstring.
ENABLED_STATES = frozenset({"enabled", "enabled-runtime"})

#: ActiveState values that mean the unit is up, or on its way up. `reloading`
#: and `refreshing` are transient healthy states, not outages.
RUNNING_STATES = frozenset({"active", "activating", "reloading", "refreshing"})

#: The properties one `systemctl show` call fetches per unit.
SHOW_PROPERTIES = (
    "Id", "UnitFileState", "ActiveState", "SubState", "Type",
    "RemainAfterExit", "FragmentPath", "DropInPaths",
)

#: systemctl subprocess timeout. Generous: the digest is not in a hurry, but
#: a wedged D-Bus must never hang the run forever.
SYSTEMCTL_TIMEOUT_S = 30.0


@dataclass
class UnitState:
    """One installed service unit, as this adapter needs to see it."""
    unit: str
    scope: str
    unit_file_state: str = ""
    active_state: str = ""
    sub_state: str = ""
    type: str = ""
    remain_after_exit: bool = False
    tier_a: bool = False
    files: list = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        """A human asked for this unit to be running. NOT the same question
        as "is it running"; that gap is this whole adapter."""
        return self.unit_file_state in ENABLED_STATES

    @property
    def running(self) -> bool:
        return self.active_state in RUNNING_STATES

    @property
    def run_to_completion(self) -> bool:
        """A oneshot that does not stay active: inactive is its normal
        resting state, so its being inactive is never a finding."""
        return self.type == "oneshot" and not self.remain_after_exit


# --------------------------------------------------------------------------
# parsing helpers (pure, no I/O beyond the explicit file reads)
# --------------------------------------------------------------------------

def parse_show_blocks(text: str) -> list[dict]:
    """Parse `systemctl show a.service b.service --property=...` output.

    systemd separates per-unit blocks with a blank line and emits `Key=Value`
    lines in an arbitrary order, so this parses by block rather than by
    position. A line without `=` is skipped rather than raising: a future
    systemd that adds a stray line must not blank the whole read.
    """
    blocks: list[dict] = []
    current: dict = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if current:
                blocks.append(current)
                current = {}
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        current[key.strip()] = value.strip()
    if current:
        blocks.append(current)
    return blocks


def parse_unit_file_names(text: str) -> list[str]:
    """Unit names out of `systemctl list-unit-files --type=service` output.

    Template units (`foo@.service`, no instance) are dropped on purpose, and
    for a hard practical reason as well as a semantic one. Semantically a
    bare template is not a runnable unit, so "is it running" has no answer.
    Practically, `systemctl show` STOPS at the first bare template name in
    its argument list and silently returns only the blocks before it:
    measured on this node, passing all 194 user unit names returned 27
    blocks. Feeding a template into the show call does not error, it TRUNCATES
    the read, which would quietly shrink the desired set to whatever sorted
    before the first template.
    """
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("UNIT FILE"):
            continue
        name = line.split()[0]
        if not name.endswith(".service"):
            continue
        if name.endswith("@.service"):
            continue
        out.append(name)
    return out


def _split_paths(value: str) -> list[str]:
    return [p for p in (value or "").split() if p]


def carries_tier_a_marker(paths: Iterable[str]) -> bool:
    """True when the Tier A marker appears in any of the given unit files.

    `paths` is what systemd itself reported as the unit's fragment plus its
    drop-ins, which is what makes this survive the marker moving between
    files or directories. An unreadable or vanished file is skipped rather
    than raised on: one unit file this process cannot read must not blank the
    whole scope.
    """
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if TIER_A_MARKER in text:
            return True
    return False


def unit_states_from_show(text: str, scope: str) -> list[UnitState]:
    """Fold one `systemctl show` read into UnitState records, resolving the
    Tier A marker per unit from the fragment + drop-in files systemd named."""
    states: list[UnitState] = []
    for block in parse_show_blocks(text):
        unit = block.get("Id") or ""
        if not unit:
            continue
        files = _split_paths(block.get("FragmentPath", "")) + \
            _split_paths(block.get("DropInPaths", ""))
        states.append(UnitState(
            unit=unit,
            scope=scope,
            unit_file_state=block.get("UnitFileState", ""),
            active_state=block.get("ActiveState", ""),
            sub_state=block.get("SubState", ""),
            type=block.get("Type", ""),
            remain_after_exit=(block.get("RemainAfterExit", "no") == "yes"),
            tier_a=carries_tier_a_marker(files),
            files=files,
        ))
    return states


# --------------------------------------------------------------------------
# the one seam: everything that touches real systemd lives below
# --------------------------------------------------------------------------

def _systemctl(scope: str, args: list[str]) -> str:
    """Run one systemctl command in `scope` and return stdout.

    The ONLY place in this module that shells out. Raises on anything that
    means "systemd could not be read", which `collect()` turns into a gap
    rather than a false all-clear.
    """
    if not sys.platform.startswith("linux"):
        raise RuntimeError(f"not a Linux host (sys.platform={sys.platform!r}), no systemd to read")
    binary = shutil.which("systemctl")
    if not binary:
        raise RuntimeError("systemctl is not on PATH: cannot read unit state")
    cmd = [binary] + (["--user"] if scope == "user" else []) + args
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        cmd, capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT_S)
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(
            f"{' '.join(cmd)} exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
    return proc.stdout


def default_unit_reader(scope: str) -> list[UnitState]:
    """Read every installed service unit in one systemd scope.

    Two systemctl calls: enumerate installed unit FILES (so a stopped,
    garbage-collected unit is still seen, see module docstring), then one
    batched `show` for their state and composing file paths.
    """
    names = parse_unit_file_names(_systemctl(scope, [
        "list-unit-files", "--type=service", "--no-legend", "--no-pager", "--plain"]))
    if not names:
        return []
    shown = _systemctl(scope, ["show", *names, "--property=" + ",".join(SHOW_PROPERTIES)])
    return unit_states_from_show(shown, scope)


def _configured_scopes() -> tuple[str, ...]:
    raw = os.environ.get("SKWATCHDOG_SYSTEMD_SCOPES", "")
    scopes = tuple(s.strip() for s in raw.split(",") if s.strip())
    return scopes or DEFAULT_SCOPES


# --------------------------------------------------------------------------
# the adapter
# --------------------------------------------------------------------------

@registry.register
class SystemdTierAAdapter(WatchdogSourceAdapter):
    """Reconcile every Tier A unit's ACTUAL state against "should be running"."""

    name = "systemd_tier_a"

    def __init__(self, unit_reader: Optional[Callable[[str], list]] = None) -> None:
        self._unit_reader = unit_reader

    def collect(self, window: Window) -> list[WatchdogEvent]:
        # Resolved at call time (module global, not a bound default) so a
        # test's monkeypatch.setattr on `default_unit_reader` is honored.
        reader = self._unit_reader or default_unit_reader

        date = window.until[:10]
        out: list[WatchdogEvent] = []
        tier_a: list[UnitState] = []
        scopes = _configured_scopes()
        failures: list[str] = []

        for scope in scopes:
            try:
                units = reader(scope)
            except Exception as exc:  # noqa: BLE001 - one bad scope must not blank the other
                failures.append(scope)
                out.append(source_unavailable(
                    f"{self.name}:{scope}", ts=window.until,
                    error=f"could not read the {scope} scope: {exc}"))
                continue
            tier_a.extend(u for u in units if getattr(u, "tier_a", False))

        if len(failures) == len(scopes):
            # Every scope unreadable: systemd is not visible at all. Raise so
            # collect_safe degrades the WHOLE source to one SourceUnavailable
            # line. Never a quiet "no Tier A unit is down".
            raise RuntimeError(
                f"systemd was unreadable in every configured scope ({', '.join(scopes)})")

        if not tier_a:
            # The derivation found no Tier A unit anywhere. Either this node
            # genuinely runs none, or the marker text moved out from under
            # this adapter. Both are "we are not actually watching anything",
            # and that is a gap, never an all-clear. See module docstring.
            raise RuntimeError(
                f"no unit carries the {TIER_A_MARKER!r} marker in "
                f"{', '.join(scopes)}: the desired-state set is empty, so nothing is being checked")

        watched = 0
        for unit in sorted(tier_a, key=lambda u: (u.scope, u.unit)):
            if unit.run_to_completion:
                continue  # inactive is this shape's normal resting state
            if not unit.enabled:
                continue  # deliberately off: disabled/masked/static, not a finding
            watched += 1
            if unit.running:
                continue
            state = unit.active_state or "unknown"
            if unit.sub_state:
                state = f"{state}/{unit.sub_state}"
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="TierAUnitDown",
                object=f"{unit.unit}@{unit.scope}", severity="problem",
                summary=(f"Tier A unit {unit.unit} ({unit.scope} scope) is enabled but "
                         f"not running (state {state}). Tier A promises it must never "
                         f"permanently die, and Restart=on-failure does not cover a "
                         f"deliberate stop, so nothing is going to bring it back."),
                link=WatchdogLink(
                    uri=f"skworld://skos/watchdog/systemd/{unit.scope}/{unit.unit}", http=""),
                ref=f"{self.name}:{unit.scope}:{unit.unit}:down:{date}",
                meta={"scope": unit.scope, "unit": unit.unit,
                      "active_state": unit.active_state, "sub_state": unit.sub_state,
                      "unit_file_state": unit.unit_file_state, "type": unit.type},
            ))

        if not any(e.kind == "TierAUnitDown" for e in out):
            # One quiet line rather than silence, mirroring the scheduler
            # adapter: "nothing to report" must be visible too, so a reader can
            # tell a clean reconcile from an adapter that never ran.
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="TierAAllRunning",
                object="systemd", severity="info",
                summary=(f"{watched} Tier A unit(s) enabled and running across "
                         f"{', '.join(scopes)}; none stopped."),
                link=WatchdogLink(uri="skworld://skos/watchdog/systemd", http=""),
                ref=f"{self.name}:summary:{date}",
            ))
        return out


__all__ = [
    "SystemdTierAAdapter", "UnitState", "TIER_A_MARKER", "default_unit_reader",
    "parse_show_blocks", "parse_unit_file_names", "unit_states_from_show",
    "carries_tier_a_marker", "ENABLED_STATES", "RUNNING_STATES",
]
