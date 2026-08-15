"""WD-9: a watchdog finding becomes a STAGED coord card, behind SKWATCHDOG_CARDS.

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md section 9 ("Phase 3:
cards, behind `SKWATCHDOG_CARDS=1` (default off)"), whose rules are all
mandatory and are all implemented here.

Read this first, because it explains every restraint below. On 2026-08-08 the
coord board drowned: 821 autopilot cards, open tasks up to 1246, recovered
only by a per-card validation sweep. This module points a NEW automated
producer at that same board. It is written as if the flood is the default
outcome and every guard below is the only thing preventing it. Where a design
choice trades recall for restraint, this module takes restraint: a missed
finding costs one day, a flood costs a weekend.

WHAT THIS MODULE DOES NOT DO. It never builds, never grades a diff, never
touches the twin gate, never merges, never promotes its own cards, never
writes a freeze file, and never invents a lane. Its involvement in the
handoff chain (finding -> item/card -> human release -> autopilot -> gate ->
PR -> merge) ENDS at filing a staged card.

THE SEVEN GUARDS

1. FLAG OFF IS INVISIBLE. `SKWATCHDOG_CARDS` defaults off, and off means off:
   `file_cards` returns before it resolves a path, reads the board, imports
   skcapstone, or creates a directory. The published digest is byte-identical
   either way, because filing runs after publish and changes tracked work,
   never the report.

2. STAGED LANE ONLY, AND IT IS THE EXISTING ONE. A card is born carrying
   `autopilot-staged` + `autopilot-untriaged`, exactly the tag pair
   `skharness.autocode.orchestrator._decompose_card` puts on a child born
   into the "Proposed" lane (orchestrator.py, "Born STAGED into the Proposed
   lane"). That machinery came out of the 08-08 flood fix. Reusing the same
   tags means a watchdog-filed card is indistinguishable from an
   autopilot-staged one: `autopilot-staged` hides it from OPEN/unblocked so
   it is never selected or built, and the SAME human promotion step,
   `skos autopilot release <epic>` -> `orchestrator.release_epic`, promotes
   it. No second lane, no second promotion path, no way for this module to
   promote anything itself.

3. ONE EPIC, EVER. Everything this module files hangs off a single standing
   parent, `EPIC_ID` (deterministic, derived from the fixed key
   "skwatchdog-findings"), via the `parent:<id>` tag `release_epic` selects
   on. One epic per finding would put the release step back on the human once
   per finding, which is how you get a board nobody reads. The epic is
   created lazily, only when a card is actually about to be filed, so a
   flag-on run with nothing to file still creates nothing at all.

4. A REPO TAG IS REQUIRED, NEVER DEFAULTED. A finding that cannot be
   attributed to a repo is REFUSED and reported, not filed against a guessed
   repo. A wrong default sends work to the wrong repo silently, which is
   worse than not filing. `repo_for` reads only fields an adapter itself
   constructed from a real repo (see its docstring); it never guesses.

5. HARD BUDGET, 5 PER DAY, AND DROPS ARE LOUD. The budget counts cards
   ALREADY filed today (read off the board, see guard 6), so running the
   digest twice in one day cannot spend it twice. Anything over budget is
   dropped, and every dropped finding is named individually in a
   `logging.warning` and in the returned report. Silent truncation reads as
   "covered everything" when it did not. Candidates are ordered
   deterministically by `source_ref` before the cut, so the drops are stable
   and tomorrow's run picks up the same queue rather than reshuffling it.

6. DEDUPE AGAINST EVERYTHING EVER FILED, NOT AGAINST WHAT IS OPEN. This is
   the guard most likely to be got wrong, and the one that matters most.
   Deduping only against currently-open cards means a finding a human judged
   and rejected reappears every single morning forever, which is a flood with
   extra steps. The ledger here is the board's own `tasks/*.json` directory,
   read IN FULL: coord task files are immutable and are never rewritten, and
   "archiving" a card only appends its id to `archive/<host>.jsonl`, so a
   closed, completed, rejected or archived card is still a file on disk with
   its `meta.watchdog.source_ref` intact. Scanning that directory therefore
   answers "have we ever filed this finding" rather than "is it open now".
   There is NO parallel store, no side list of "what we filed": to know what
   was filed, this module asks the board, the same way WD-8 asks the GTD
   sink and `skos.adapters.order` asks the store.
   The one hole, named honestly: a task file that a human hard-deletes leaves
   the ledger, and no design that refuses a parallel store can see it. The
   daily budget caps what that could cost to 5 cards.

7. FREEZE-AWARE, WITH NO SECOND FREEZE CONCEPT. `gtd.fleet_frozen` is
   imported and called, not reimplemented: the same
   `skcapstone.fleet.store.is_frozen(default_paths())` read every actuating
   path in the operator seat uses. Digest generation keeps running under
   freeze; only the writes stand down.

THE WD-8 / WD-9 RELATIONSHIP: ESCALATION, NOT DUPLICATION

Two automated systems filing for the same finding is its own flood, so the
relationship is decided here explicitly rather than left to emerge.

  WD-8's GTD item is the RECORD OF THE PROBLEM. It is what Chef reads in
  `skcapstone gtd next`, it carries the urgency (priority high), it updates
  in place while the problem persists, and it auto-completes when it clears.
  Every problem finding gets one.

  WD-9's card is the PROPOSAL TO FIX IT IN A NAMED REPO. It is dispatch, not
  tracking: it exists so that a human can release it and autopilot can build
  it. It is staged, so it is invisible on the active board until a human
  promotes it, and it is priority low, because a proposal competes with
  nothing. Only a minority of findings ever get one.

A card is therefore only filed when it adds something the item cannot, and
the three preconditions are all checked against stores, never remembered:

  a. The finding is repo-attributable (guard 4). A finding with no repo is
     work nobody can dispatch, so it stays a GTD item, exactly as the spec
     says ("a card requires a confident single `repo:<name>` tag or it stays
     a GTD item").
  b. WD-8 already tracks it, as an OPEN item. If the tracking layer is off or
     has no open item for this finding, there is nothing to escalate, and
     this module files nothing. That is why `SKWATCHDOG_GTD` being off short
     circuits the whole run: a card without its item would be an orphan
     dispatch with no record behind it.
  c. The item has PERSISTED past the run that opened it (its `created_at`
     date is older than this digest's date). A finding that fired for the
     first time this morning gets an item and a day to clear on its own; only
     a problem that is still there tomorrow earns a dispatch card. This is
     the single biggest recall-for-restraint trade in the module, and it is
     deliberate: one day of latency on a real problem, in exchange for the
     entire class of one-morning blips never reaching the board at all.

The card cross-links back by `meta.watchdog.source_ref`, which is WD-8's
stable GTD `source_ref` (`gtd.source_ref_for`), so the item and the card are
one chain rather than two independent trackers, and so the dedupe ledger and
the GTD store speak the same identity.

FAIL-SAFE. `file_cards` never raises. It runs after publish and after the
cursor advance, so it cannot delay or break a digest that has already landed;
every failure is recorded in the returned report instead, and a single bad
card never stops the rest.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..gtd_ingest import _find_item
from .gtd import (
    GTD_SOURCE, FILING_SEVERITY, fleet_frozen, gtd_enabled, source_ref_for,
)
from .render import strip_banned_dashes

logger = logging.getLogger(__name__)

#: The feature flag, default OFF (spec 9). Flipped in the scheduler env file
#: only when Chef is ready. Rollback: unset it. Cards already filed are staged,
#: so rollback needs nothing else: nothing was ever promoted.
FLAG = "SKWATCHDOG_CARDS"

#: The fixed key of the ONE standing parent epic, and the deterministic id
#: derived from it. Deterministic so every node and every run agrees on the
#: same epic without a registry lookup or a stored id.
EPIC_KEY = "skwatchdog-findings"
EPIC_ID = hashlib.sha1(EPIC_KEY.encode("utf-8")).hexdigest()[:8]
EPIC_TITLE = "skwatchdog findings (standing epic)"

#: The tag pair that puts a card in the "Proposed" lane, copied deliberately
#: from `skharness.autocode.orchestrator._decompose_card` so a watchdog card
#: and an autopilot-staged child are the same thing to every reader and to
#: `release_epic`, which strips exactly these two. `autopilot` marks it as
#: engine-eligible work once released.
STAGED_TAGS = ("autopilot", "autopilot-untriaged", "autopilot-staged")

#: Marks a card as this module's, for the ledger scan and for humans grepping.
CARD_TAG = "skwatchdog"

#: The namespaced meta block on the card. Same key WD-8 uses on its GTD item,
#: carrying the same `source_ref`, so item and card are one chain.
META_KEY = "watchdog"

#: Hard cap on NEW cards per digest date (spec 9). Not a tuning knob.
DAILY_BUDGET = 5

#: A staged proposal competes with nothing; the GTD item carries the urgency.
CARD_PRIORITY = "low"
CREATED_BY = "skwatchdog"

_TRUTHY = {"1", "true", "yes", "on"}


def cards_enabled() -> bool:
    """True only when `SKWATCHDOG_CARDS` is explicitly set to a truthy value.
    Anything else, including unset, empty, "0" and "false", reads as off."""
    return str(os.environ.get(FLAG, "")).strip().lower() in _TRUTHY


def coord_home() -> Path:
    """The skcapstone home whose `coordination/` tree holds the board.

    Precedence mirrors `gtd_ingest.gtd_dir` exactly: an explicit
    `SKCAPSTONE_HOME` wins (this is the seam tests point at tmp, so a test can
    never reach Chef's real board), then skcapstone's own shared-root resolver
    when the sibling is installed (so this writes to the SAME board its tools
    read), then the documented default. Unlike `gtd_dir`, this NEVER creates
    the directory: resolving a path must stay a pure read.
    """
    env = os.environ.get("SKCAPSTONE_HOME")
    if env:
        return Path(env).expanduser()
    try:  # optional sibling; align with its exact board location when present
        from skcapstone.mcp_tools._helpers import _shared_root
        return Path(_shared_root()).expanduser()
    except Exception:  # noqa: BLE001 - absent or broken sibling: use the default
        return Path.home() / ".skcapstone"


def tasks_dir() -> Path:
    """The coord task directory: one immutable JSON file per card, and the
    dedupe ledger of guard 6. Never created here."""
    return coord_home() / "coordination" / "tasks"


def card_id_for(source_ref: str) -> str:
    """The card's id, derived deterministically from the finding's stable
    identity. Two runs that somehow both got past the ledger check would then
    write the SAME file rather than two cards: defense in depth behind the
    dedupe, in the same spirit as `stable_qid` in the autocode orchestrator.
    """
    return hashlib.sha1(f"skwatchdog:{source_ref}".encode("utf-8")).hexdigest()[:8]


def repo_for(event: Mapping) -> Optional[str]:
    """The single repo a finding is attributable to, or None to REFUSE it.

    Only two things count as attribution, and neither is a guess:

      `meta.repo`, an adapter stating the repo outright. Any adapter can opt
      in by setting it; that is the intended path for new sources.

      A `git` finding's `object`. The git adapter builds that field as
      `<repo>#<pr>` or `<repo>@<sha>` from `_configured_repos()`, so the
      prefix is a configured repo name, not free text. Reading it back is
      reading the adapter's own field, not inferring from prose.

    Everything else returns None, and the caller reports the refusal. There is
    deliberately no fallback, no "default repo", and no scan of the summary
    text for something that looks like a repo name (guard 4).
    """
    meta = event.get("meta") or {}
    if isinstance(meta, Mapping):
        raw = meta.get("repo")
        if isinstance(raw, str) and raw.strip() and len(raw.split()) == 1:
            return raw.strip()

    source = str(event.get("source") or "")
    if source == "git" or source.startswith("git:"):
        obj = str(event.get("object") or "")
        name = obj.split("#", 1)[0].split("@", 1)[0].strip()
        if name and len(name.split()) == 1 and "/" not in name:
            return name
    return None


def card_title(event: Mapping) -> str:
    """The card's one line. Mirrors WD-8's `item_text` wording on purpose, so
    a human looking at the GTD item and the card sees the same finding named
    the same way. Banned dashes stripped: Chef reads card titles."""
    source = str(event.get("source") or CARD_TAG)
    kind = str(event.get("kind") or "Finding")
    obj = str(event.get("object") or "")
    text = f"skwatchdog {source}: {kind} on {obj}" if obj else f"skwatchdog {source}: {kind}"
    return strip_banned_dashes(text)


def card_description(event: Mapping, *, repo: str, ref: str, date: str) -> str:
    """The card body. States the finding, its links, its identity, and, in
    plain words, that this card is a staged proposal a human must release.
    No acceptance criteria are invented here: scoping the work is the
    reviewer's job, and a machine-written criterion would be a guess that
    later reads as a requirement."""
    link = event.get("link") or {}
    lines = [
        strip_banned_dashes(str(event.get("summary") or "")),
        "",
        f"Filed by skwatchdog on {date} from a `problem` finding that was already "
        f"tracked in the unified GTD and had persisted past the run that opened it.",
        "",
        f"repo: {repo}",
        f"source: {event.get('source') or ''}",
        f"kind: {event.get('kind') or ''}",
        f"object: {event.get('object') or ''}",
        f"watchdog source_ref: {ref}",
    ]
    uri = str(link.get("uri") or "")
    http = str(link.get("http") or "")
    if http:
        lines.append(f"link: {http}")
    if uri:
        lines.append(f"uri: {uri}")
    lines += [
        "",
        f"STAGED. This card sits in the Proposed lane and is not claimable or "
        f"buildable until a human runs `skos autopilot release {EPIC_ID}`. "
        f"Scope it, or archive it: an archived card is remembered, so this "
        f"finding will not be filed again.",
    ]
    return strip_banned_dashes("\n".join(lines))


def _iso_date(value: Any) -> str:
    """The YYYY-MM-DD prefix of an ISO timestamp, or "" when unreadable."""
    text = str(value or "")
    return text[:10] if len(text) >= 10 and text[4] == "-" and text[7] == "-" else ""


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class _Ledger:
    """One pass over `tasks/*.json`: everything this module needs to know
    about what it has ever done, read straight off the board.

    `refs` is the dedupe set of guard 6 and includes archived, completed and
    rejected cards, because their task files are still there. `ids` guards the
    deterministic card id against colliding with an unrelated card. `today` is
    the spent half of the daily budget. `epic` says whether the standing
    parent already exists.
    """

    def __init__(self, refs: set[str], ids: set[str], today: int, epic: bool) -> None:
        self.refs, self.ids, self.filed_today, self.epic_exists = refs, ids, today, epic


def read_ledger(date: str, *, directory: Optional[Path] = None) -> _Ledger:
    """Scan the board's task files. A missing directory is an empty ledger (a
    board that has never been written to); an unreadable or malformed single
    file is skipped rather than raised, the same per-file fail-safe the
    coord_autocode adapter uses.

    NOTE the deliberate absence: no filter on status, on open/closed, on
    archived. Reading the whole directory is the entire point (guard 6).
    """
    directory = directory or tasks_dir()
    refs: set[str] = set()
    ids: set[str] = set()
    filed_today = 0
    epic_exists = False
    if not directory.is_dir():
        return _Ledger(refs, ids, filed_today, epic_exists)

    for path in sorted(directory.glob("*.json")):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(task, Mapping):
            continue
        tid = str(task.get("id") or "")
        if tid:
            ids.add(tid)
        if tid == EPIC_ID:
            epic_exists = True
        block = (task.get("meta") or {}).get(META_KEY) or {}
        if not isinstance(block, Mapping):
            continue
        ref = str(block.get("source_ref") or "")
        if not ref:
            continue
        refs.add(ref)
        # The budget counts by the DIGEST date the card was filed for, not by
        # wall clock: two runs of the same digest date share one budget, and a
        # test can drive dates without touching the clock. `created_at` is the
        # fallback for a card written before this field existed.
        stamped = str(block.get("filed_date") or "") or _iso_date(task.get("created_at"))
        if stamped == date:
            filed_today += 1
    return _Ledger(refs, ids, filed_today, epic_exists)


def _open_gtd_item(ref: str) -> Optional[dict]:
    """WD-8's item for this finding, if it is OPEN. `_find_item` searches the
    archive too, so an archived (completed) item is explicitly rejected here:
    a cleared problem has nothing to escalate."""
    fname, _idx, item, _items = _find_item(GTD_SOURCE, ref)
    if not item or fname == "archive.json":
        return None
    return dict(item)


def _board_for(home: Path):
    """The real coord Board. skcapstone is an OPTIONAL sibling of skos, so the
    import is lazy and only ever happens on a flag-on run that has an actual
    card to file (mirrors the lazy skcapstone imports in the autocode
    orchestrator). An absent sibling means no board to write to, which the
    caller turns into a reported error, never a raise."""
    from skcapstone.coordination import Board
    return Board(home)


def _task_factory():
    """The coord `Task` model, resolved lazily for the same optional-sibling
    reason as `_board_for`, and through one named seam so the filing logic
    (budget, dedupe, refusals) stays testable on a node without skcapstone
    installed. Production always gets the real model."""
    from skcapstone.coordination import Task
    return Task


def _ensure_epic(board, ledger: _Ledger) -> None:
    """Create the ONE standing parent, once, ever. Called only when a card is
    about to be filed, so a run with nothing to file creates nothing.

    The epic itself carries the staged tags too, so nothing ever tries to
    "build the epic": `release_epic` promotes cards tagged `parent:<epic>`, so
    stripping the epic's own tags is never attempted and the container stays
    parked in the Proposed lane where it belongs.
    """
    if ledger.epic_exists:
        return
    board.create_task(_task_factory()(
        id=EPIC_ID,
        title=EPIC_TITLE,
        description=strip_banned_dashes(
            "Standing parent for every card skwatchdog files. Children are born "
            "staged in the Proposed lane and are promoted only by a human running "
            f"`skos autopilot release {EPIC_ID}`. skwatchdog never promotes its own "
            "cards and never builds anything. See "
            "docs/specs/2026-08-10-skwatchdog-architecture.md section 9."),
        priority=CARD_PRIORITY,
        tags=[CARD_TAG, "epic", *STAGED_TAGS],
        created_by=CREATED_BY,
        meta={META_KEY: {"epic_key": EPIC_KEY}},
    ))
    ledger.epic_exists = True


def _create_card(board, event: Mapping, *, repo: str, ref: str, date: str,
                 gtd_id: str) -> str:
    """Write one staged child card and return its id. Uses the real
    `Board.create_task(Task)` contract, the same call
    `orchestrator._create_child` makes, so a watchdog card is an ordinary
    coord card in every respect."""
    cid = card_id_for(ref)
    link = event.get("link") or {}
    board.create_task(_task_factory()(
        id=cid,
        title=card_title(event),
        description=card_description(event, repo=repo, ref=ref, date=date),
        priority=CARD_PRIORITY,
        tags=[CARD_TAG, f"repo:{repo}", f"parent:{EPIC_ID}", *STAGED_TAGS],
        created_by=CREATED_BY,
        meta={
            # `meta.autopilot.parent` + `staged` are what the autocode engine
            # reads for parentage and lane; the `parent:` tag above is what
            # `release_epic` selects on. Both, exactly as a staged child born
            # in the orchestrator carries both.
            "autopilot": {"parent": EPIC_ID, "staged": True},
            META_KEY: {
                "source_ref": ref,
                "source": str(event.get("source") or ""),
                "kind": str(event.get("kind") or ""),
                "object": str(event.get("object") or ""),
                "severity": str(event.get("severity") or FILING_SEVERITY),
                "repo": repo,
                "filed_date": date,
                "gtd_item": gtd_id,
                "link": {"uri": str(link.get("uri") or ""),
                         "http": str(link.get("http") or "")},
            },
        },
    ))
    return cid


def file_cards(digest: Mapping, *, board=None) -> dict:
    """File this run's eligible problem findings as STAGED coord cards.

    NEVER raises. It runs after the digest has published and after cursors
    have advanced, so a board problem must not, and cannot, take down a run
    that already landed: every failure is recorded in the returned report.

    Returns `{enabled, skipped, filed, refused, deduped, dropped, budget,
    errors?}`:
      `filed`    `{source_ref, id, repo}` per card actually written.
      `refused`  `{source_ref, reason}` per finding that did not qualify
                 (`no-repo`, `gtd-untracked`, `gtd-new`, `id-collision`).
      `deduped`  count of findings already present in the ledger, ever.
      `dropped`  `{source_ref, title, reason}` per finding cut by the budget,
                 each also logged at WARNING (guard 5).

    `board` is a test seam. It is never needed in production: the default
    board is resolved from `coord_home()`, and only on a run that is filing.
    """
    report: dict = {"enabled": False, "skipped": None, "filed": [], "refused": [],
                    "deduped": 0, "dropped": [], "budget": DAILY_BUDGET}

    # Flag check FIRST, before anything that reads a path, the board, or the
    # GTD store. Off means nothing happened at all (guard 1).
    if not cards_enabled():
        report["skipped"] = "flag-off"
        return report
    report["enabled"] = True

    # A card is an escalation of a WD-8 item (see module docstring). With the
    # tracking layer off there is nothing to escalate, and checking here also
    # keeps this module from resolving the GTD dir (which would create it) on
    # a node where GTD filing is deliberately off.
    if not gtd_enabled():
        report["skipped"] = "gtd-off"
        return report

    if fleet_frozen():
        report["skipped"] = "frozen"
        return report

    try:
        _file_cards_unguarded(digest, report, board=board)
    except Exception as exc:  # noqa: BLE001 - deliberate: filing never breaks the digest
        report.setdefault("errors", []).append(str(exc))
    return report


def _candidates(digest: Mapping) -> dict:
    """The run's problem findings, collapsed to one entry per stable identity
    (several sightings of one problem inside a window are one finding, first
    sighting wins) and ordered deterministically by that identity so the
    budget cut is stable run to run."""
    wanted: dict[str, Mapping] = {}
    for event in (digest.get("problems") or []):
        if not isinstance(event, Mapping):
            continue
        if str(event.get("severity") or "") != FILING_SEVERITY:
            continue
        wanted.setdefault(source_ref_for(event), event)
    return {ref: wanted[ref] for ref in sorted(wanted)}


def _file_cards_unguarded(digest: Mapping, report: dict, *, board=None) -> None:
    date = _iso_date(digest.get("date")) or _today()
    ledger = read_ledger(date)

    eligible: list[tuple[str, Mapping, str, str]] = []  # (ref, event, repo, gtd_id)
    for ref, event in _candidates(digest).items():
        if ref in ledger.refs:
            # Filed before, at any time, in any state, including judged and
            # rejected. It does not come back (guard 6).
            report["deduped"] += 1
            continue
        repo = repo_for(event)
        if not repo:
            report["refused"].append({"source_ref": ref, "reason": "no-repo"})
            continue
        item = _open_gtd_item(ref)
        if item is None:
            report["refused"].append({"source_ref": ref, "reason": "gtd-untracked"})
            continue
        opened = _iso_date(item.get("created_at"))
        if not opened or opened >= date:
            # Opened by this same run (or later): give it a day to clear. An
            # item with an unreadable created_at cannot be shown to have
            # persisted, so it is treated as new: in doubt, file fewer.
            report["refused"].append({"source_ref": ref, "reason": "gtd-new"})
            continue
        if card_id_for(ref) in ledger.ids:
            # The deterministic id belongs to some other card. Refuse rather
            # than overwrite a card that is not ours.
            report["refused"].append({"source_ref": ref, "reason": "id-collision"})
            continue
        eligible.append((ref, event, repo, str(item.get("id") or "")))

    remaining = max(DAILY_BUDGET - ledger.filed_today, 0)
    report["budget"] = remaining
    for ref, event, _repo, _gtd_id in eligible[remaining:]:
        entry = {"source_ref": ref, "title": card_title(event),
                 "reason": f"daily budget {DAILY_BUDGET} exhausted"}
        report["dropped"].append(entry)
        # Loud, per finding, by name. A count alone reads as "covered
        # everything" when it did not (guard 5).
        logger.warning(
            "skwatchdog: DROPPED finding %s (%s): daily card budget %d exhausted "
            "(%d already filed on %s). It was NOT filed and will be reconsidered "
            "on the next run.", ref, entry["title"], DAILY_BUDGET,
            ledger.filed_today, date)

    if not eligible[:remaining]:
        return

    if board is None:
        board = _board_for(coord_home())
    _ensure_epic(board, ledger)

    for ref, event, repo, gtd_id in eligible[:remaining]:
        try:
            cid = _create_card(board, event, repo=repo, ref=ref, date=date, gtd_id=gtd_id)
        except Exception as exc:  # noqa: BLE001 - one bad card never stops the rest
            report.setdefault("errors", []).append(f"{ref}: {exc}")
            continue
        report["filed"].append({"source_ref": ref, "id": cid, "repo": repo})


__all__ = [
    "FLAG", "EPIC_KEY", "EPIC_ID", "EPIC_TITLE", "STAGED_TAGS", "CARD_TAG",
    "META_KEY", "DAILY_BUDGET", "cards_enabled", "coord_home", "tasks_dir",
    "card_id_for", "repo_for", "card_title", "card_description", "read_ledger",
    "file_cards",
]
