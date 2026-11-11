"""Tests for scheduler-as-code (card 29ae4313 / deploy-plan 4b).

Guarantees the committed schedule manifest (deploy/schedule/jobs.yaml) parses and is
internally consistent, that rendering never leaks a secret VALUE, that the managed
crontab block splices idempotently, and that diff correctly detects drift.
"""
import textwrap

import pytest

from skos import schedule as sched


# --------------------------------------------------------------- committed manifest

def test_committed_manifest_loads_and_is_consistent():
    s = sched.load()  # repo deploy/schedule/jobs.yaml
    assert s.jobs, "manifest declares no jobs"
    names = [j.name for j in s.jobs]
    assert len(names) == len(set(names)), "job names must be unique"
    # The runner is an installed command (skcapstone's sk-cron-run), not a
    # path into this checkout. Asserted here rather than calling
    # check_runner_exists, which is strict about the binary being present and
    # so cannot run on a box without skcapstone installed; it has its own
    # tests below, both directions, against a controlled HOME.
    assert s.runner == "$HOME/.skenv/bin/sk-cron-run"
    assert "$SKOS_REPO" not in s.runner, "the crontab must not depend on a checkout path"
    # every schedule is a valid 5-field cron expression
    for j in s.jobs:
        sched.validate_schedule(j.schedule, j.name)


def test_committed_manifest_has_no_secret_values():
    """The committed manifest must never carry a secret value; only $NAME refs."""
    text = (sched.repo_root() / "deploy" / "schedule" / "jobs.yaml").read_text()
    assert "sk2026" not in text
    # rendered lines carry only the protected env-file path, never values
    s = sched.load()
    for line in sched.render_lines(s):
        assert "SKOS_SCHEDULE_ENV=" in line
        assert "GOG_KEYRING_PASSWORD=" not in line
        assert "sk2026" not in line


# ------------------------------------------------------------------- validation

def test_duplicate_job_name_rejected(tmp_path):
    m = tmp_path / "jobs.yaml"
    m.write_text(textwrap.dedent("""
        version: 1
        defaults: {runner: "$SKOS_REPO/scripts/sk-cron-run.sh", path: "/usr/bin", secret_env: []}
        jobs:
          - {name: a, schedule: "0 6 * * *", command: "echo hi", log: "/tmp/a.log"}
          - {name: a, schedule: "5 6 * * *", command: "echo ho", log: "/tmp/a.log"}
    """))
    with pytest.raises(sched.ScheduleError, match="duplicate job name"):
        sched.load(m)


def test_duplicate_cron_slot_rejected(tmp_path):
    m = tmp_path / "jobs.yaml"
    m.write_text(textwrap.dedent("""
        version: 1
        defaults: {runner: "$SKOS_REPO/scripts/sk-cron-run.sh", path: "/usr/bin", secret_env: []}
        jobs:
          - {name: first, schedule: "45 7 * * *", command: "echo hi", log: "/tmp/a.log"}
          - {name: second, schedule: "45 7 * * *", command: "echo ho", log: "/tmp/b.log"}
    """))
    with pytest.raises(sched.ScheduleError, match="duplicate cron slot"):
        sched.load(m)


@pytest.mark.parametrize("bad", ["0 6 * *", "60 6 * * *", "0 24 * * *", "0 6 0 * *", "0 6 * 13 *", "0 6 * * x"])
def test_invalid_cron_rejected(tmp_path, bad):
    m = tmp_path / "jobs.yaml"
    m.write_text(textwrap.dedent(f"""
        version: 1
        defaults: {{runner: "$SKOS_REPO/scripts/sk-cron-run.sh", path: "/usr/bin", secret_env: []}}
        jobs:
          - {{name: a, schedule: "{bad}", command: "echo hi", log: "/tmp/a.log"}}
    """))
    with pytest.raises(sched.ScheduleError):
        sched.load(m)


@pytest.mark.parametrize("good", ["0 6 * * *", "*/30 * * * *", "22 */3 * * *", "0 10 * * 0", "0 14 1 * 0"])
def test_valid_cron_accepted(good):
    sched.validate_schedule(good, "x")  # no raise


def test_0745_job_resolves_to_watchdog_digest_not_sk_status_report():
    """WD-4 (card 2405db76): the 07:45 slot is absorbed by `skos watchdog
    digest` so Chef gets exactly one morning DM. This pins the cutover so a
    future edit to jobs.yaml can't silently revert it back to the old
    `sk-status report` DM path without a test failing.
    """
    s = sched.load()
    by_name = {j.name: j for j in s.jobs}
    assert "ops-report" in by_name, "the 07:45 job was renamed or removed"
    job = by_name["ops-report"]
    assert job.schedule == "45 7 * * *"
    assert "skos watchdog digest" in job.command
    assert "sk-status report" not in job.command

    # sk-status stays installed as the digest's counts engine; it must not be
    # ripped out of the schedule entirely (it still backs corpus-check).
    assert any("sk-status" in j.command for j in s.jobs)

    # exactly one job fires at 07:45, and it is the watchdog digest -- two
    # jobs in this slot is precisely the "two daily DMs" failure WD-4 exists
    # to prevent.
    at_0745 = [j for j in s.jobs if j.schedule == "45 7 * * *"]
    assert len(at_0745) == 1
    assert at_0745[0].name == "ops-report"


def test_missing_key_rejected(tmp_path):
    m = tmp_path / "jobs.yaml"
    m.write_text(textwrap.dedent("""
        version: 1
        defaults: {runner: "$SKOS_REPO/scripts/sk-cron-run.sh", path: "/usr/bin", secret_env: []}
        jobs:
          - {name: a, schedule: "0 6 * * *", command: "echo hi"}
    """))
    with pytest.raises(sched.ScheduleError, match="missing required key"):
        sched.load(m)


# ----------------------------------------------------------------------- render

def test_render_expand_produces_concrete_paths():
    s = sched.load()
    lines = sched.render_lines(s, expand=True, home="/home/tester", repo="/home/tester/clawd/skos")
    assert lines
    joined = "\n".join(lines)
    assert "$HOME" not in joined and "$SKOS_REPO" not in joined
    assert "/home/tester/.skenv/bin/sk-cron-run" in joined
    assert "scripts/sk-cron-run.sh" not in joined, "the shim is legacy, never rendered"


def test_render_never_injects_secret_even_when_provided():
    s = sched.load()
    lines = sched.render_lines(s, expand=True, home="/h", repo="/h/r", secrets={"GOG_KEYRING_PASSWORD": "SEKRET"})
    assert all("SEKRET" not in ln for ln in lines)
    assert all("GOG_KEYRING_PASSWORD=" not in ln for ln in lines)


# ------------------------------------------------------------------------- diff

def _render_live(s, home="/home/tester", repo="/home/tester/clawd/skos", secret="livesecret"):
    return "\n".join(
        sched.render_lines(s, expand=True, home=home, repo=repo, secrets={"GOG_KEYRING_PASSWORD": secret})
    )


def test_diff_clean_when_live_matches_manifest_ignoring_secret():
    s = sched.load()
    live = _render_live(s, secret="whatever-value")
    d = sched.diff(s, live, home="/home/tester", repo="/home/tester/clawd/skos")
    assert d.ok, (d.missing, d.changed)
    assert not d.extra


def test_diff_detects_missing_and_changed():
    s = sched.load()
    live_lines = _render_live(s).splitlines()
    # drop one job, mutate another's schedule
    kept = live_lines[1:]
    kept[0] = kept[0].replace(kept[0].split()[0], "59", 1)  # change first field of 2nd job
    d = sched.diff(s, "\n".join(kept), home="/home/tester", repo="/home/tester/clawd/skos")
    assert not d.ok
    assert d.missing or d.changed


# --------------------------------------------------------------- splice / block

def test_splice_inserts_then_replaces_idempotently():
    s = sched.load()
    block = sched.render_block(s, expand=True, home="/h", repo="/h/r", secrets={"GOG_KEYRING_PASSWORD": "x"})
    base = "0 7 * * * /home/x/personal-job.sh\n"
    once = sched.splice_block(base, block)
    assert sched.BLOCK_BEGIN in once and sched.BLOCK_END in once
    assert "personal-job.sh" in once  # unmanaged lines preserved
    # second splice with a new block must replace, not duplicate
    block2 = block.replace("GOG_KEYRING_PASSWORD=x", "GOG_KEYRING_PASSWORD=y")
    twice = sched.splice_block(once, block2)
    assert twice.count(sched.BLOCK_BEGIN) == 1
    assert twice.count(sched.BLOCK_END) == 1
    assert "personal-job.sh" in twice


def test_install_fails_without_resolved_secret(tmp_path, monkeypatch):
    """Dry-run is safe and useful before credential provisioning."""
    s = sched.load()
    monkeypatch.delenv("GOG_KEYRING_PASSWORD", raising=False)
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("# nothing here\n")
    monkeypatch.setattr(sched, "read_crontab", lambda: "")
    rendered = sched.install(s, env_file=empty_env, dry_run=True)
    assert "SKOS_SCHEDULE_ENV=" in rendered
    assert "GOG_KEYRING_PASSWORD=" not in rendered


def test_env_file_placeholder_not_treated_as_secret(tmp_path, monkeypatch):
    s = sched.load()
    monkeypatch.delenv("GOG_KEYRING_PASSWORD", raising=False)  # ignore any live env fallback
    envf = tmp_path / "s.env"
    envf.write_text("GOG_KEYRING_PASSWORD=replace-me\n")
    resolved = sched.resolve_secrets(s, envf)
    assert "GOG_KEYRING_PASSWORD" not in resolved  # placeholder ignored


# ------------------------------------------------------------------- runner target

def test_runner_target_resolves_home_and_repo_forms(tmp_path, monkeypatch):
    """Both forms resolve. The function is named for what it returns.

    It was ``runner_path_in_repo`` and always joined onto the repo root, which
    was true while the runner was a script in this checkout and a lie the
    moment it became an installed command. A path helper whose name disagrees
    with its return value is how skcapstone's per-host-artifact bug happened:
    callers guessed between the pip copy and the deployed copy and were right
    by luck.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    installed = sched.Schedule(
        runner="$HOME/.skenv/bin/sk-cron-run", path="", secret_env=[], jobs=[]
    )
    assert sched.runner_target(installed) == tmp_path / ".skenv" / "bin" / "sk-cron-run"

    legacy = sched.Schedule(
        runner="$SKOS_REPO/scripts/sk-cron-run.sh", path="", secret_env=[], jobs=[]
    )
    assert sched.runner_target(legacy) == sched.repo_root() / "scripts" / "sk-cron-run.sh"


def test_check_runner_exists_refuses_an_absent_installed_command(tmp_path, monkeypatch):
    """Strict on purpose: the in-repo shim is not evidence the command exists.

    The shim resolves at render time and then exits 78 at run time, so
    accepting it here would move the silence one layer down instead of ending
    it, and make this an instrument that cannot fail.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    s = sched.Schedule(runner="$HOME/.skenv/bin/sk-cron-run", path="", secret_env=[], jobs=[])
    with pytest.raises(sched.ScheduleError) as err:
        sched.check_runner_exists(s)
    assert "sk-cron-run" in str(err.value) and "skcapstone" in str(err.value)

    binary = tmp_path / ".skenv" / "bin" / "sk-cron-run"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    sched.check_runner_exists(s)  # present now, so no raise


def test_both_runner_spellings_sign_as_the_same_managed_job():
    """A host migrating from the shim to the installed command must SPLICE.

    _signature keyed on the literal "sk-cron-run.sh", so the moment the
    manifest named the installed command the parser stopped recognising its
    own managed lines and `schedule install` would have appended a second copy
    of the whole block instead of replacing it. It now keys on the runner's
    identity, reusing timer_wrap's basename-minus-.sh rule so the crontab half
    and the systemd half agree on what counts as the same runner.
    """
    tail = "myjob /bin/true >> /x/l 2>&1"
    head = "0 3 * * * SKOS_SCHEDULE_ENV=/x/e PATH=/b"
    installed = sched._signature(f"{head} /home/u/.skenv/bin/sk-cron-run {tail}")
    legacy = sched._signature(f"{head} /home/u/clawd/skos/scripts/sk-cron-run.sh {tail}")
    assert installed is not None and installed == legacy
    assert sched._signature(f"{head} /usr/bin/something-else {tail}") is None


def test_fleet_cronjobs_run_under_their_fleet_object_name():
    """CMDB drift ties a fleet CronJob to a crontab entry through the
    sk-cron-run job name, so each one installed here must use the fleet
    object's own name (skcapstone deploy/fleet-objects/bulletproof). Until
    2026-11-11 these three ran from hand-written lines outside the managed
    block (or not at all), and drift reported them missing."""
    jobs = {job.name: job for job in sched.load().jobs}
    assert jobs["skmem-reconcile"].command.endswith("-m skmemory.reconcile --all"), (
        "one agent-agnostic run, not a hand line per agent"
    )
    assert jobs["skcapstone-backup-gfs"].command == "$HOME/.skenv/bin/skcapstone-gfs-backup.sh"
    assert jobs["capauth-custody-doctor"].command == "$HOME/.skenv/bin/capauth doctor custody --json"
    for name in ("skmem-reconcile", "skcapstone-backup-gfs", "capauth-custody-doctor"):
        assert jobs[name].log, f"{name} must log, not discard its output"
