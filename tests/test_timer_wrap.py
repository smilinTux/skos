"""Wrap systemd user timers in sk-cron-run so nothing scheduled fails silently.

Card ``95a3b69e``. The entire crontab routes through ``sk-cron-run``, which is
what provides the run-ledger, the failure-to-GTD capture and the sk-alert. Zero
of the systemd user timers did, so half the scheduling surface could fail with
nobody told. That is exactly how skoperator failed 16 times on 2026-08-13 and
alerted nobody, while OBSERVABILITY_AND_SCHEDULING_STANDARD says nothing
scheduled fails silently.

The wrap is a systemd DROP-IN, never an edit of the original unit:

    [Service]
    ExecStart=
    ExecStart=<sk-cron-run> <job> <original ExecStart>

The empty ``ExecStart=`` is required: systemd APPENDS to a list-typed
directive, so without the reset the job would run twice, once bare and once
wrapped. Drop-ins make the whole thing reversible by deleting a file, which
matters when the blast radius is every scheduled job on the box.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skos.timer_wrap import (
    THIRD_PARTY_PREFIXES,
    dropin_text,
    is_wrappable,
    plan_wraps,
    wrap_command,
)


# ── the wrapped command ───────────────────────────────────────────────────


def test_the_wrapper_precedes_the_original_command():
    out = wrap_command("/opt/sk-cron-run.sh", "skoperator", "%h/.skenv/bin/skoperator run")
    assert out == "/opt/sk-cron-run.sh skoperator %h/.skenv/bin/skoperator run"


def test_systemd_specifiers_survive_untouched():
    """%h must reach systemd, not be expanded when the drop-in is written."""
    out = wrap_command("/opt/sk-cron-run.sh", "job", "%h/clawd/scripts/thing.sh --flag")
    assert "%h/clawd/scripts/thing.sh --flag" in out


def test_an_already_wrapped_command_is_not_double_wrapped():
    cmd = "/opt/sk-cron-run.sh job /bin/true"
    assert wrap_command("/opt/sk-cron-run.sh", "job", cmd) == cmd


# ── the drop-in ───────────────────────────────────────────────────────────


def test_the_dropin_resets_execstart_before_setting_it():
    """Without the empty reset, systemd APPENDS and the job runs twice."""
    text = dropin_text("/opt/sk-cron-run.sh", "job", "/bin/true")
    lines = [ln for ln in text.splitlines() if ln.startswith("ExecStart")]
    assert lines[0] == "ExecStart="
    assert lines[1] == "ExecStart=/opt/sk-cron-run.sh job /bin/true"


def test_the_dropin_declares_its_service_section():
    """[Service] must precede the ExecStart DIRECTIVES.

    Matched line-wise on purpose: the explanatory comment mentions ExecStart=
    too, and a substring search would find that first and prove nothing.
    """
    lines = dropin_text("/opt/sk-cron-run.sh", "job", "/bin/true").splitlines()
    section = lines.index("[Service]")
    directives = [i for i, ln in enumerate(lines) if ln.startswith("ExecStart=")]
    assert directives, "no ExecStart directive at line start"
    assert section < min(directives)


def test_the_dropin_says_what_wrote_it_and_how_to_undo_it():
    """A file that appears in someone's unit must explain itself."""
    text = dropin_text("/opt/sk-cron-run.sh", "job", "/bin/true")
    assert "95a3b69e" in text  # the card
    assert "sk-cron-run" in text


# ── which units are in scope ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "unit",
    ["skoperator", "skchat-backup", "wiki-reconcile", "skmemory-sync@lumina", "sktrip"],
)
def test_sk_owned_units_are_wrappable(unit):
    assert is_wrappable(unit) is True


@pytest.mark.parametrize("unit", sorted(THIRD_PARTY_PREFIXES))
def test_third_party_units_are_left_alone(unit):
    """Wrapping someone else's unit is not ours to do, and snap/systemd units
    get replaced by their packages anyway."""
    assert is_wrappable(f"{unit}whatever") is False


def test_systemd_own_units_are_left_alone():
    assert is_wrappable("systemd-tmpfiles-clean") is False


# ── planning over a real unit tree ────────────────────────────────────────


def _unit(tmp: Path, name: str, execstart: str) -> None:
    (tmp / f"{name}.service").write_text(
        f"[Unit]\nDescription={name}\n\n[Service]\nType=oneshot\nExecStart={execstart}\n",
        encoding="utf-8",
    )
    (tmp / f"{name}.timer").write_text(
        f"[Unit]\nDescription={name} timer\n\n[Timer]\nOnCalendar=daily\n", encoding="utf-8"
    )


def test_plan_covers_every_timer_backed_service(tmp_path):
    _unit(tmp_path, "skoperator", "%h/.skenv/bin/skoperator run")
    _unit(tmp_path, "wiki-reconcile", "%h/clawd/scripts/wiki-reconcile-commit.sh")
    plan = plan_wraps(tmp_path, "/opt/sk-cron-run.sh")
    assert {p["unit"] for p in plan} == {"skoperator", "wiki-reconcile"}
    assert all(p["wrapped_exec"].startswith("/opt/sk-cron-run.sh ") for p in plan)


def test_a_continued_execstart_is_joined_not_truncated(tmp_path):
    """systemd allows a backslash-continued ExecStart across lines.

    Reading only the first line would wrap `nextcloudcmd` with none of its
    arguments and the timer would run a completely different command. Three
    live sync timers on the box are written this way, so this is not
    theoretical: it is the failure a dry run caught before anything was
    written.
    """
    (tmp_path / "syncer.service").write_text(
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/usr/bin/nextcloudcmd \\\n"
        "    --non-interactive \\\n"
        "    --silent \\\n"
        "    %h/Nextcloud/share/\n",
        encoding="utf-8",
    )
    (tmp_path / "syncer.timer").write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")

    plan = plan_wraps(tmp_path, "/opt/sk-cron-run.sh")
    assert len(plan) == 1
    wrapped = plan[0]["wrapped_exec"]
    for fragment in ("--non-interactive", "--silent", "%h/Nextcloud/share/"):
        assert fragment in wrapped, f"lost {fragment}"
    assert "\\" not in wrapped  # joined into one line, not left half-continued


def test_plan_skips_a_service_with_no_timer(tmp_path):
    """A service nothing schedules is not part of the scheduling surface."""
    _unit(tmp_path, "skoperator", "/bin/true")
    (tmp_path / "manual-thing.service").write_text(
        "[Service]\nExecStart=/bin/true\n", encoding="utf-8"
    )
    assert {p["unit"] for p in plan_wraps(tmp_path, "/opt/sk-cron-run.sh")} == {"skoperator"}


def test_plan_skips_third_party_timers(tmp_path):
    _unit(tmp_path, "skoperator", "/bin/true")
    _unit(tmp_path, "snap.firmware-updater.firmware-notifier", "/bin/true")
    assert {p["unit"] for p in plan_wraps(tmp_path, "/opt/sk-cron-run.sh")} == {"skoperator"}


def test_plan_is_idempotent_against_an_already_wrapped_unit(tmp_path):
    """Re-running must not stack wrappers, or the ledger job name compounds."""
    _unit(tmp_path, "skoperator", "/opt/sk-cron-run.sh skoperator /bin/true")
    plan = plan_wraps(tmp_path, "/opt/sk-cron-run.sh")
    assert plan == []


def test_plan_reports_a_unit_it_cannot_parse_instead_of_skipping_it(tmp_path):
    """Silence is the bug being fixed; an unreadable unit must be loud."""
    _unit(tmp_path, "skoperator", "/bin/true")
    (tmp_path / "broken.timer").write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")
    # broken.timer exists with no matching .service
    plan = plan_wraps(tmp_path, "/opt/sk-cron-run.sh", strict=False)
    problems = [p for p in plan if p.get("problem")]
    assert any(p["unit"] == "broken" for p in problems)


def test_strict_mode_raises_on_an_unresolvable_timer(tmp_path):
    (tmp_path / "broken.timer").write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")
    with pytest.raises(ValueError, match="broken"):
        plan_wraps(tmp_path, "/opt/sk-cron-run.sh", strict=True)
