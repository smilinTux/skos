"""systemd_tier_a (card 04ad64d7): Tier A does not mean running.

NO TEST IN THIS FILE MAY REACH A REAL `systemctl` OR TOUCH A REAL UNIT. That
is enforced, not merely intended: the `no_real_systemctl` autouse fixture
below nails shut `subprocess.run` for the whole module (the single door
`adapters/systemd_tier_a.py` has to the outside world), and
`test_the_guard_against_a_real_systemctl_is_actually_live` proves the guard is
armed rather than assuming it. Tests drive the adapter through its injected
`unit_reader` seam instead, and the marker-scanning code is exercised against
real files written into `tmp_path`, which requires no systemd at all.

The distinction this adapter exists to get right, and that the tests here
cover from both sides:

    enabled + inactive  -> a problem. A human asked for it to run and it is
                            not running, and Tier A's Restart=on-failure will
                            never bring it back from a deliberate stop.
    disabled + inactive -> silence. A human deliberately turned it off.
                            Reporting that every morning is how a watchdog
                            teaches its reader to ignore it.
"""
import pytest

from skos.watchdog.adapters import systemd_tier_a as ta
from skos.watchdog.adapters.systemd_tier_a import (
    SystemdTierAAdapter, UnitState, carries_tier_a_marker, parse_show_blocks,
    parse_unit_file_names, unit_states_from_show,
)
from skos.watchdog.port import Window, collect_safe

WINDOW = Window(since="2026-08-15T07:45:00Z", until="2026-08-16T07:45:00Z")

TIER_A_DROPIN = (
    "# SERVICE_UNIT_STANDARD Tier A: backoff only (must never permanently die).\n"
    "[Service]\nRestartSteps=8\nRestartMaxDelaySec=5min\n"
)
TIER_B_DROPIN = (
    "# SERVICE_UNIT_STANDARD Tier B: backoff + limiter (leaf app).\n"
    "[Service]\nRestartSec=10\n"
)


# --------------------------------------------------------------------------
# the guard
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_real_systemctl(monkeypatch):
    """Block the module's one door to the real system, for EVERY test here.

    `adapters/systemd_tier_a.py` reaches systemd through exactly one call,
    `subprocess.run` inside `_systemctl`. Patching it to raise means any test
    that forgets to inject its `unit_reader` seam fails loudly instead of
    quietly interrogating this developer's live units (and, worse, passing or
    failing depending on whose box it ran on). `shutil.which` is pinned too so
    the "systemctl missing" branch can never short-circuit ahead of the guard
    and make it look satisfied when it was simply never reached.
    """
    def _boom(*args, **kwargs):
        raise AssertionError(
            f"a test tried to shell out to the real systemctl: {args!r}")

    monkeypatch.setattr(ta.subprocess, "run", _boom)
    monkeypatch.setattr(ta.shutil, "which", lambda name: "/fake/bin/systemctl")


def test_the_guard_against_a_real_systemctl_is_actually_live():
    """A guard nobody checks is a guard nobody knows is broken.

    This asserts two things at once: the autouse fixture is armed, and
    `_systemctl` really does route through the patched `subprocess.run` rather
    than some second path that would slip past it. If a future refactor gives
    this module another way out to the shell, this test goes red.
    """
    with pytest.raises(AssertionError, match="real systemctl"):
        ta._systemctl("user", ["list-unit-files"])

    # and the seam's default, which is what an un-injected adapter uses
    with pytest.raises(AssertionError, match="real systemctl"):
        ta.default_unit_reader("user")


def test_an_adapter_with_no_injected_reader_degrades_instead_of_shelling_out():
    """collect_safe folds the guard's AssertionError into a gap line, which is
    also the shape a genuinely unreadable systemd produces in production."""
    events = collect_safe(SystemdTierAAdapter(), WINDOW)
    assert len(events) == 1
    assert events[0].kind == "SourceUnavailable"


# --------------------------------------------------------------------------
# helpers for building a fake systemd view
# --------------------------------------------------------------------------

def _unit(name, *, scope="user", enabled=True, active="active", sub="running",
          tier_a=True, type_="simple", remain=False):
    return UnitState(
        unit=name, scope=scope,
        unit_file_state="enabled" if enabled else "disabled",
        active_state=active, sub_state=sub, type=type_,
        remain_after_exit=remain, tier_a=tier_a,
    )


def _reader(**by_scope):
    """A fake unit_reader: {scope: [UnitState] or an Exception to raise}."""
    def read(scope):
        value = by_scope.get(scope, [])
        if isinstance(value, Exception):
            raise value
        return list(value)
    return read


def _collect(**by_scope):
    return SystemdTierAAdapter(unit_reader=_reader(**by_scope)).collect(WINDOW)


# --------------------------------------------------------------------------
# the core distinction: enabled+inactive fires, disabled+inactive does not
# --------------------------------------------------------------------------

def test_enabled_and_inactive_tier_a_unit_is_a_problem():
    """The measured case: skgateway cleanly stopped 2026-08-15 00:41:00,
    Result=success, and nothing brought it back."""
    events = _collect(user=[_unit("skgateway.service", active="inactive", sub="dead")],
                      system=[])
    problems = [e for e in events if e.severity == "problem"]
    assert len(problems) == 1
    ev = problems[0]
    assert ev.source == "systemd_tier_a"
    assert ev.kind == "TierAUnitDown"
    assert ev.object == "skgateway.service@user"
    assert "skgateway.service" in ev.summary
    assert "inactive/dead" in ev.summary
    assert ev.ref == "systemd_tier_a:user:skgateway.service:down:2026-08-16"
    assert ev.link.uri.endswith("/systemd/user/skgateway.service")
    assert ev.meta["unit_file_state"] == "enabled"


def test_disabled_and_inactive_tier_a_unit_stays_quiet():
    """A human deliberately turned it off. Reporting a deliberate choice as a
    problem every morning is how a watchdog trains its reader to skim past it.

    (On the node this card was written against, shadowcopy-backfill.service
    and shadowcopy-monitor.service are exactly this shape: inactive AND
    disabled. They happen to carry the Tier B marker there, so they are doubly
    out of scope; this test pins the enablement rule on its own so it holds
    even for a unit that IS Tier A.)
    """
    events = _collect(
        user=[
            _unit("shadowcopy-monitor.service", enabled=False, active="inactive", sub="dead"),
            _unit("skgateway.service", active="active"),
        ],
        system=[])
    assert [e.severity for e in events] == ["info"]
    assert events[0].kind == "TierAAllRunning"
    assert not any("shadowcopy" in e.summary for e in events)


def test_masked_and_static_tier_a_units_are_not_findings():
    """Neither state carries an operator's "please be running": masked is a
    deliberate hard-off, and static has no [Install] section to enable at all."""
    masked = _unit("masked.service", active="inactive")
    masked.unit_file_state = "masked"
    static = _unit("static.service", active="inactive")
    static.unit_file_state = "static"
    events = _collect(user=[masked, static, _unit("up.service")], system=[])
    assert [e.kind for e in events] == ["TierAAllRunning"]


def test_active_tier_a_unit_stays_quiet_but_says_so():
    events = _collect(user=[_unit("skgateway.service"), _unit("sknoded.service")],
                      system=[_unit("containerd.service", scope="system")])
    assert len(events) == 1
    assert events[0].kind == "TierAAllRunning"
    assert events[0].severity == "info"
    assert "3 Tier A unit(s)" in events[0].summary


@pytest.mark.parametrize("state", ["activating", "reloading", "refreshing"])
def test_transient_healthy_states_are_not_outages(state):
    events = _collect(user=[_unit("skgateway.service", active=state, sub="start")], system=[])
    assert [e.kind for e in events] == ["TierAAllRunning"]


def test_a_failed_tier_a_unit_is_also_a_problem():
    """`failed` is not in the running set either. Restart=on-failure may have
    given up (Tier A caps backoff but a start-limit hit still parks a unit)."""
    events = _collect(user=[_unit("skcot.service", active="failed", sub="failed")], system=[])
    assert [e.kind for e in events] == ["TierAUnitDown"]
    assert events[0].meta["active_state"] == "failed"


def test_a_run_to_completion_oneshot_is_never_a_finding():
    """Type=oneshot with RemainAfterExit=no rests inactive by design. Flagging
    it would manufacture a problem every single morning."""
    events = _collect(
        user=[_unit("backfill.service", active="inactive", sub="dead",
                    type_="oneshot", remain=False),
              _unit("skgateway.service")],
        system=[])
    assert [e.kind for e in events] == ["TierAAllRunning"]
    assert "1 Tier A unit(s)" in events[0].summary  # the oneshot is not even counted


def test_a_remain_after_exit_oneshot_is_still_watched():
    """skchat-coturn.service on this node: Type=oneshot, RemainAfterExit=yes,
    so it holds `active` after its run and inactive really does mean gone."""
    events = _collect(
        user=[_unit("skchat-coturn.service", active="inactive", sub="dead",
                    type_="oneshot", remain=True)],
        system=[])
    assert [e.kind for e in events] == ["TierAUnitDown"]


# --------------------------------------------------------------------------
# the desired set: derived from the marker, and a gap when it is empty
# --------------------------------------------------------------------------

def test_a_unit_that_lost_its_tier_a_marker_leaves_the_desired_set():
    """The desired set is derived, never hand-maintained: a unit whose drop-in
    no longer carries the marker simply stops being watched, with no edit
    anywhere in this repo."""
    events = _collect(
        user=[_unit("demoted.service", active="inactive", sub="dead", tier_a=False),
              _unit("skgateway.service")],
        system=[])
    assert [e.kind for e in events] == ["TierAAllRunning"]
    assert not any("demoted" in e.summary for e in events)


def test_a_non_tier_a_unit_being_down_is_not_this_adapters_business():
    events = _collect(user=[_unit("skgateway.service"),
                            _unit("some-leaf-app.service", active="failed", tier_a=False)],
                      system=[])
    assert [e.kind for e in events] == ["TierAAllRunning"]


def test_an_empty_tier_a_set_is_a_gap_not_an_all_clear():
    """If the marker text ever moves out from under this adapter, the desired
    set silently empties and every morning reads clean. That must be LOUD."""
    events = collect_safe(SystemdTierAAdapter(unit_reader=_reader(user=[], system=[])), WINDOW)
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "SourceUnavailable"
    assert ev.severity == "notable"
    assert ev.source == "systemd_tier_a"
    assert "desired-state set is empty" in ev.summary


# --------------------------------------------------------------------------
# systemd unreachable
# --------------------------------------------------------------------------

def test_systemd_unreachable_everywhere_is_a_gap_never_silence():
    boom = RuntimeError("Failed to connect to bus")
    events = collect_safe(
        SystemdTierAAdapter(unit_reader=_reader(user=boom, system=boom)), WINDOW)
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "SourceUnavailable"
    assert ev.severity == "notable"
    assert ev.source == "systemd_tier_a"
    assert "unreadable in every configured scope" in ev.summary


def test_one_unreadable_scope_never_blanks_the_other():
    """A node where the system scope is not readable must still reconcile its
    user-scope Tier A units, and must still say the system scope went unread."""
    events = _collect(
        user=[_unit("skgateway.service", active="inactive", sub="dead")],
        system=PermissionError("Access denied"))
    kinds = sorted(e.kind for e in events)
    assert kinds == ["SourceUnavailable", "TierAUnitDown"]
    gap = [e for e in events if e.kind == "SourceUnavailable"][0]
    assert gap.source == "systemd_tier_a:system"
    assert gap.severity == "notable"


def test_scopes_are_configurable(monkeypatch):
    monkeypatch.setenv("SKWATCHDOG_SYSTEMD_SCOPES", "user")
    seen = []

    def read(scope):
        seen.append(scope)
        return [_unit("skgateway.service")]

    SystemdTierAAdapter(unit_reader=read).collect(WINDOW)
    assert seen == ["user"]


# --------------------------------------------------------------------------
# the derivation itself: parsing + marker scanning, against real files
# --------------------------------------------------------------------------

def test_parse_show_blocks_handles_arbitrary_property_order_and_blank_lines():
    text = (
        "Type=simple\nId=a.service\nActiveState=active\n"
        "\n"
        "Id=b.service\nActiveState=inactive\nType=oneshot\n"
        "\n"
        "a line with no equals sign\n"
    )
    blocks = parse_show_blocks(text)
    assert [b["Id"] for b in blocks] == ["a.service", "b.service"]
    assert blocks[1]["Type"] == "oneshot"


def test_parse_unit_file_names_drops_bare_templates():
    """Measured: `systemctl show` TRUNCATES at the first bare template name in
    its argv (194 names in, 27 blocks out on this node). A template is also not
    a runnable unit, so "is it running" has no answer for one."""
    text = (
        "anthropic-proxy.service   enabled   enabled\n"
        "cloud9-daemon@.service    disabled  -\n"
        "skmemory-sync@lumina.service static  -\n"
        "some.timer                enabled   enabled\n"
    )
    assert parse_unit_file_names(text) == [
        "anthropic-proxy.service", "skmemory-sync@lumina.service"]


def test_marker_is_found_wherever_systemd_says_the_unit_lives(tmp_path):
    """This is what survives the marker MOVING. The files scanned are whatever
    systemd reported as the unit's fragment plus drop-ins, so the marker can
    move between drop-in files, or into the fragment itself, or across
    directories, and still be found."""
    fragment = tmp_path / "skgateway.service"
    fragment.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    dropin = tmp_path / "restart-storm.conf"
    dropin.write_text(TIER_A_DROPIN, encoding="utf-8")
    assert carries_tier_a_marker([str(fragment), str(dropin)]) is True

    # the same marker, moved into the fragment and out of the drop-in
    fragment.write_text(TIER_A_DROPIN, encoding="utf-8")
    dropin.write_text("[Service]\nRestart=always\n", encoding="utf-8")
    assert carries_tier_a_marker([str(fragment), str(dropin)]) is True


def test_tier_b_is_not_tier_a(tmp_path):
    """Tier B is 'backoff + limiter (leaf app)': a service that is ALLOWED to
    give up. Matching it here would flood the digest with leaf apps."""
    f = tmp_path / "restart-storm.conf"
    f.write_text(TIER_B_DROPIN, encoding="utf-8")
    assert carries_tier_a_marker([str(f)]) is False


def test_an_unreadable_or_missing_unit_file_never_raises(tmp_path):
    good = tmp_path / "ok.conf"
    good.write_text(TIER_A_DROPIN, encoding="utf-8")
    assert carries_tier_a_marker([str(tmp_path / "gone.conf"), str(good)]) is True
    assert carries_tier_a_marker([str(tmp_path / "gone.conf")]) is False


def test_unit_states_from_show_resolves_the_marker_off_disk(tmp_path):
    """The full derivation step 2 -> step 3, with no systemd anywhere: a
    canned `systemctl show` read whose paths point at real temp files."""
    a_dropin = tmp_path / "skgateway.service.d" / "restart-storm.conf"
    a_dropin.parent.mkdir(parents=True)
    a_dropin.write_text(TIER_A_DROPIN, encoding="utf-8")
    b_dropin = tmp_path / "skvoice.service.d" / "restart-storm.conf"
    b_dropin.parent.mkdir(parents=True)
    b_dropin.write_text(TIER_B_DROPIN, encoding="utf-8")
    frag_a = tmp_path / "skgateway.service"
    frag_a.write_text("[Service]\n", encoding="utf-8")
    frag_b = tmp_path / "skvoice.service"
    frag_b.write_text("[Service]\n", encoding="utf-8")

    text = (
        f"Id=skgateway.service\nUnitFileState=enabled\nActiveState=inactive\n"
        f"SubState=dead\nType=simple\nRemainAfterExit=no\n"
        f"FragmentPath={frag_a}\nDropInPaths={a_dropin}\n"
        "\n"
        f"Id=skvoice.service\nUnitFileState=enabled\nActiveState=inactive\n"
        f"SubState=dead\nType=simple\nRemainAfterExit=no\n"
        f"FragmentPath={frag_b}\nDropInPaths={b_dropin}\n"
    )
    states = unit_states_from_show(text, "user")
    assert [s.unit for s in states] == ["skgateway.service", "skvoice.service"]
    assert states[0].tier_a is True and states[0].enabled and not states[0].running
    assert states[1].tier_a is False

    # and drive the adapter over exactly that derived view
    events = SystemdTierAAdapter(unit_reader=lambda scope: states if scope == "user" else []
                                 ).collect(WINDOW)
    assert [e.kind for e in events] == ["TierAUnitDown"]
    assert events[0].object == "skgateway.service@user"


def test_multiple_dropin_paths_are_all_scanned(tmp_path):
    """DropInPaths is a space separated list; the marker commonly sits in the
    last of several drop-ins (config-path, override, restart-storm...)."""
    others = []
    for name in ("config-path.conf", "override.conf"):
        p = tmp_path / name
        p.write_text("[Service]\n", encoding="utf-8")
        others.append(str(p))
    marker = tmp_path / "restart-storm.conf"
    marker.write_text(TIER_A_DROPIN, encoding="utf-8")
    frag = tmp_path / "u.service"
    frag.write_text("[Service]\n", encoding="utf-8")
    text = (f"Id=u.service\nUnitFileState=enabled\nActiveState=active\n"
            f"FragmentPath={frag}\nDropInPaths={' '.join(others)} {marker}\n")
    states = unit_states_from_show(text, "user")
    assert states[0].tier_a is True
    assert len(states[0].files) == 4


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------

def test_load_all_registers_systemd_tier_a():
    from skos.watchdog.adapters import load_all
    assert "systemd_tier_a" in load_all()
