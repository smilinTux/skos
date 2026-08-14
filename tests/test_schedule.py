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
    # runner referenced by the manifest must exist in the repo
    sched.check_runner_exists(s)
    # every schedule is a valid 5-field cron expression
    for j in s.jobs:
        sched.validate_schedule(j.schedule, j.name)


def test_committed_manifest_has_no_secret_values():
    """The committed manifest must never carry a secret value; only $NAME refs."""
    text = (sched.repo_root() / "deploy" / "schedule" / "jobs.yaml").read_text()
    assert "sk2026" not in text
    # rendered lines (portable form) must reference the secret by name, not value
    s = sched.load()
    for line in sched.render_lines(s):
        assert "GOG_KEYRING_PASSWORD=$GOG_KEYRING_PASSWORD" in line
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
    assert "/home/tester/clawd/skos/scripts/sk-cron-run.sh" in joined


def test_render_injects_secret_only_when_provided():
    s = sched.load()
    lines = sched.render_lines(s, expand=True, home="/h", repo="/h/r", secrets={"GOG_KEYRING_PASSWORD": "SEKRET"})
    assert any("GOG_KEYRING_PASSWORD=SEKRET" in ln for ln in lines)


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
    s = sched.load()
    monkeypatch.delenv("GOG_KEYRING_PASSWORD", raising=False)
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("# nothing here\n")
    with pytest.raises(sched.ScheduleError, match="unresolved secret"):
        sched.install(s, env_file=empty_env, dry_run=True)


def test_env_file_placeholder_not_treated_as_secret(tmp_path, monkeypatch):
    s = sched.load()
    monkeypatch.delenv("GOG_KEYRING_PASSWORD", raising=False)  # ignore any live env fallback
    envf = tmp_path / "s.env"
    envf.write_text("GOG_KEYRING_PASSWORD=replace-me\n")
    resolved = sched.resolve_secrets(s, envf)
    assert "GOG_KEYRING_PASSWORD" not in resolved  # placeholder ignored
