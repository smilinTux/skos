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
    apply_wraps,
    dropin_text,
    is_wrappable,
    parse_show_execstart,
    plan_wraps,
    split_exec_prefix,
    systemd_execstart_query,
    wrap_command,
)

RUNNER = "/opt/sk-cron-run.sh"


# ── the wrapped command ───────────────────────────────────────────────────


def test_the_wrapper_precedes_the_original_command():
    out = wrap_command(
        "/opt/sk-cron-run.sh", "skoperator", "%h/.skenv/bin/skoperator run"
    )
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
        f"[Unit]\nDescription={name} timer\n\n[Timer]\nOnCalendar=daily\n",
        encoding="utf-8",
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
    (tmp_path / "syncer.timer").write_text(
        "[Timer]\nOnCalendar=daily\n", encoding="utf-8"
    )

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
    assert {p["unit"] for p in plan_wraps(tmp_path, "/opt/sk-cron-run.sh")} == {
        "skoperator"
    }


def test_plan_skips_third_party_timers(tmp_path):
    _unit(tmp_path, "skoperator", "/bin/true")
    _unit(tmp_path, "snap.firmware-updater.firmware-notifier", "/bin/true")
    assert {p["unit"] for p in plan_wraps(tmp_path, "/opt/sk-cron-run.sh")} == {
        "skoperator"
    }


def test_plan_is_idempotent_against_an_already_wrapped_unit(tmp_path):
    """Re-running must not stack wrappers, or the ledger job name compounds."""
    _unit(tmp_path, "skoperator", "/opt/sk-cron-run.sh skoperator /bin/true")
    plan = plan_wraps(tmp_path, "/opt/sk-cron-run.sh")
    assert plan == []


def test_plan_reports_a_unit_it_cannot_parse_instead_of_skipping_it(tmp_path):
    """Silence is the bug being fixed; an unreadable unit must be loud."""
    _unit(tmp_path, "skoperator", "/bin/true")
    (tmp_path / "broken.timer").write_text(
        "[Timer]\nOnCalendar=daily\n", encoding="utf-8"
    )
    # broken.timer exists with no matching .service
    plan = plan_wraps(tmp_path, "/opt/sk-cron-run.sh", strict=False)
    problems = [p for p in plan if p.get("problem")]
    assert any(p["unit"] == "broken" for p in problems)


def test_strict_mode_raises_on_an_unresolvable_timer(tmp_path):
    (tmp_path / "broken.timer").write_text(
        "[Timer]\nOnCalendar=daily\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="broken"):
        plan_wraps(tmp_path, "/opt/sk-cron-run.sh", strict=True)


# ── card 47e32514: the wrap must read the EFFECTIVE ExecStart ─────────────
#
# systemd's effective ExecStart is the base fragment PLUS its drop-ins in
# lexical order, where a bare `ExecStart=` resets the list. Reading only the
# base file discards every flag a drop-in contributed, silently. Live proof on
# the primary node: skoperator.service's base fragment is report-only
# (`skoperator run`), execute.conf adds --execute and a further drop-in adds
# --honor, and the skos-managed sk-cron-run.conf was rebuilt from the BASE, so
# the operator seat ran report-only while every status surface looked healthy.
#
# Every test below injects the query. None of them talk to the live manager, so
# they mean the same thing in CI, in a container and on a non-systemd box.


def _show(argv: str, *, flags: str = "", ignore_errors: str | None = None) -> str:
    """One `systemctl show -p ExecStart(Ex) --value` record, real format.

    Copied from `systemctl --user show skoperator.service -p ExecStartEx
    --value` on systemd 255. `path` is only the binary; `argv[]` is the whole
    command, which is why the parser must read argv[].
    """
    third = (
        "ignore_errors=" + ignore_errors
        if ignore_errors is not None
        else "flags=" + flags
    )
    return (
        f"{{ path={argv.split()[0]} ; argv[]={argv} ; {third} ; "
        "start_time=[Sat 2026-08-15 02:03:53 EDT] ; stop_time=[Sat 2026-08-15 02:04:52 EDT] ; "
        "pid=318228 ; code=exited ; status=0 }\n"
    )


def _query(mapping: dict[str, str]):
    """An effective-ExecStart query backed by a dict, recording its calls."""

    def query(unit_name: str) -> str | None:
        query.calls.append(unit_name)  # type: ignore[attr-defined]
        return mapping.get(unit_name)

    query.calls = []  # type: ignore[attr-defined]
    return query


# ── the parser ────────────────────────────────────────────────────────────


def test_the_parser_reads_argv_not_path():
    """`path` is the binary alone. Parsing it would drop every argument."""
    out = parse_show_execstart(_show("/usr/bin/skoperator run --execute --honor"))
    assert out == "/usr/bin/skoperator run --execute --honor"


def test_the_parser_takes_the_last_of_several_execstart_records():
    """ExecStart is list-typed; `systemctl show` prints one record per line.

    The file parser takes the last ExecStart, so this must agree with it.
    """
    value = _show("/bin/first") + _show("/bin/second --flag")
    assert parse_show_execstart(value) == "/bin/second --flag"


def test_the_parser_survives_a_semicolon_inside_the_command():
    """`find -exec ... ;` puts a bare ";" in argv, which is also the record's
    own field separator. Terminating argv[] on the FOLLOWING key rather than on
    the next " ; " keeps the command whole."""
    out = parse_show_execstart(_show("/usr/bin/find /tmp -name x -exec rm {} ;"))
    assert out == "/usr/bin/find /tmp -name x -exec rm {} ;"


def test_the_parser_reads_both_flag_spellings():
    """ExecStartEx says flags=ignore-failure, ExecStart says ignore_errors=yes.

    Either way the `-` prefix has to come back, or a job that was allowed to
    fail starts marking its unit failed.
    """
    assert parse_show_execstart(_show("/bin/x", flags="ignore-failure")) == "-/bin/x"
    assert parse_show_execstart(_show("/bin/x", ignore_errors="yes")) == "-/bin/x"
    assert parse_show_execstart(_show("/bin/x", ignore_errors="no")) == "/bin/x"


def test_the_parser_refuses_a_prefix_it_cannot_reproduce():
    """`+` drops sandboxing and `!` changes user/group. Guessing at those would
    change a job's privileges, so an unknown flag means "fall back to the file".
    """
    assert parse_show_execstart(_show("/bin/x", flags="privileged")) is None


def test_the_parser_returns_none_for_output_with_no_record():
    """An unknown unit exits 0 with EMPTY output. That is not an ExecStart."""
    assert parse_show_execstart("") is None
    assert parse_show_execstart("\n") is None


# ── prefixes travel ahead of the runner ───────────────────────────────────


def test_an_exec_prefix_is_split_off_the_command():
    assert split_exec_prefix("-/bin/x --flag") == ("-", "/bin/x --flag")
    assert split_exec_prefix("/bin/x") == ("", "/bin/x")


def test_a_prefix_wraps_outside_the_runner_not_inside_the_arguments():
    """`-` qualifies the whole ExecStart. Left in argument position it would be
    handed to the job as a literal argument and the unit would start failing."""
    assert wrap_command(RUNNER, "job", "-/bin/x") == f"-{RUNNER} job /bin/x"


def test_an_already_wrapped_prefixed_command_is_not_double_wrapped():
    cmd = f"-{RUNNER} job /bin/x"
    assert wrap_command(RUNNER, "job", cmd) == cmd


# ── the bug itself ────────────────────────────────────────────────────────


def test_flags_contributed_by_a_dropin_survive_the_wrap(tmp_path):
    """THE REGRESSION. Card 47e32514, reproduced from the live skoperator unit.

    Base fragment is report-only; the effective command carries --execute and
    --honor from drop-ins. The wrap must keep them.
    """
    _unit(tmp_path, "skoperator", "%h/.skenv/bin/skoperator run")
    query = _query(
        {
            "skoperator.service": _show(
                "/home/x/.skenv/bin/skoperator run --execute --honor"
            )
        }
    )

    plan = plan_wraps(tmp_path, RUNNER, effective=query)

    assert len(plan) == 1
    wrapped = plan[0]["wrapped_exec"]
    assert (
        wrapped
        == f"{RUNNER} skoperator /home/x/.skenv/bin/skoperator run --execute --honor"
    )
    assert plan[0]["exec_source"] == "effective"


def test_the_dropin_written_carries_the_effective_flags(tmp_path):
    """The plan is not the artefact; the drop-in file is. Assert on the file."""
    _unit(tmp_path, "skoperator", "%h/.skenv/bin/skoperator run")
    query = _query(
        {
            "skoperator.service": _show(
                "/home/x/.skenv/bin/skoperator run --execute --honor"
            )
        }
    )

    written = apply_wraps(tmp_path, RUNNER, effective=query)

    text = Path(written[0]).read_text(encoding="utf-8")
    assert "--execute --honor" in text
    assert text.splitlines()[-1].endswith("skoperator run --execute --honor")


def test_the_base_file_read_alone_is_what_loses_the_flags(tmp_path):
    """Negative control. Without the effective read the flags DO vanish, which
    is what makes the test above evidence rather than decoration."""
    _unit(tmp_path, "skoperator", "%h/.skenv/bin/skoperator run")
    plan = plan_wraps(tmp_path, RUNNER, effective=None)
    assert "--execute" not in plan[0]["wrapped_exec"]


# ── idempotency, which the effective read makes load-bearing ──────────────


def test_an_already_wrapped_effective_command_produces_no_plan_entry(tmp_path):
    """The effective ExecStart of a wrapped unit IS the wrapped command.

    Without an idempotent wrap_command this read would stack a second runner on
    every single run, and the ledger job name would compound with it.
    """
    _unit(tmp_path, "skoperator", "%h/.skenv/bin/skoperator run")
    wrapped = f"{RUNNER} skoperator /home/x/.skenv/bin/skoperator run --execute"
    query = _query({"skoperator.service": _show(wrapped)})

    assert plan_wraps(tmp_path, RUNNER, effective=query) == []


def test_re_planning_after_apply_converges(tmp_path):
    """Apply, feed the result back as the new effective value, plan again.

    That is the real loop: after `apply_wraps` + daemon-reload, the next run
    reads back exactly what was written.
    """
    _unit(tmp_path, "skoperator", "%h/.skenv/bin/skoperator run")
    first = plan_wraps(
        tmp_path,
        RUNNER,
        effective=_query({"skoperator.service": _show("/bin/op run --execute")}),
    )
    assert len(first) == 1

    second = plan_wraps(
        tmp_path,
        RUNNER,
        effective=_query({"skoperator.service": _show(first[0]["wrapped_exec"])}),
    )
    assert second == []


# ── the fallback must behave exactly as it always did ─────────────────────


def test_no_query_available_reproduces_the_file_only_behaviour(tmp_path):
    """A box with no systemctl, or a manager that cannot answer, must land
    exactly where it always did: on the base fragment, specifiers intact."""
    _unit(tmp_path, "skoperator", "%h/.skenv/bin/skoperator run")
    forced = plan_wraps(tmp_path, RUNNER, effective=None)
    unanswered = plan_wraps(tmp_path, RUNNER, effective=_query({}))
    assert forced == unanswered
    assert forced[0]["exec"] == "%h/.skenv/bin/skoperator run"
    assert forced[0]["exec_source"] == "file"


def test_an_empty_query_answer_falls_back_rather_than_reporting_no_execstart(tmp_path):
    """`systemctl show` on an unknown unit exits 0 with EMPTY output. Treating
    that as "no ExecStart" would flood the plan with false problems."""
    _unit(tmp_path, "skoperator", "%h/.skenv/bin/skoperator run")
    plan = plan_wraps(tmp_path, RUNNER, effective=_query({"skoperator.service": ""}))
    assert plan[0]["exec_source"] == "file"
    assert not any(p.get("problem") for p in plan)


def test_a_template_unit_still_resolves_through_the_file(tmp_path):
    """systemd refuses `foo@.service` as "neither a valid invocation ID nor unit
    name", so a bare template can only ever be read from its file."""
    (tmp_path / "skmemory-sync@.service").write_text(
        "[Service]\nExecStart=%h/.skenv/bin/skmemory sync --quiet\n", encoding="utf-8"
    )
    (tmp_path / "skmemory-sync@.timer").write_text(
        "[Timer]\nOnCalendar=hourly\n", encoding="utf-8"
    )

    plan = plan_wraps(tmp_path, RUNNER, effective=_query({}))

    assert len(plan) == 1
    assert plan[0]["exec_source"] == "file"
    assert plan[0]["wrapped_exec"].endswith("%h/.skenv/bin/skmemory sync --quiet")


def test_the_systemd_query_short_circuits_a_bare_template():
    """It must not even shell out: systemd cannot answer, and the subprocess
    would only cost a fork to be told so."""
    assert systemd_execstart_query("skmemory-sync@.service") is None


def test_an_instanced_timer_is_queried_under_its_own_instance_name(tmp_path):
    """The drop-in lands in foo@bar.service.d, so the effective ExecStart that
    matters is foo@bar.service, not the foo@.service template it inherits."""
    (tmp_path / "skmemory-sync@.service").write_text(
        "[Service]\nExecStart=%h/.skenv/bin/skmemory sync\n", encoding="utf-8"
    )
    (tmp_path / "skmemory-sync@lumina.timer").write_text(
        "[Timer]\nOnCalendar=hourly\n", encoding="utf-8"
    )
    query = _query(
        {"skmemory-sync@lumina.service": _show("/bin/skmemory sync --agent lumina")}
    )

    plan = plan_wraps(tmp_path, RUNNER, effective=query)

    assert query.calls == ["skmemory-sync@lumina.service"]
    assert plan[0]["wrapped_exec"].endswith("/bin/skmemory sync --agent lumina")


def test_third_party_timers_are_never_even_queried(tmp_path):
    """Out-of-scope units must not cost a subprocess, let alone a drop-in."""
    _unit(tmp_path, "snap.firmware-updater.firmware-notifier", "/bin/true")
    query = _query({})
    plan_wraps(tmp_path, RUNNER, effective=query)
    assert query.calls == []
