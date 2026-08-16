"""sites: read-only link-check + reachability narration for the static
marketing sites (WD-12, Phase 3, the last and smallest Phase 3 card).

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section 11: "the
sites are static (a link-checker/lighthouse adapter covers them for
near-zero cost)". Lighthouse is explicitly out of scope (the card's own
words, "later if ever"); this module is the link-checker half only.

THE RULE THIS MODULE EXISTS TO HOLD
------------------------------------
This is the only adapter of the ten that makes a NETWORK REQUEST rather than
reading a local or sibling source, and that changes the shape of everything
below it.

  polite        HEAD before GET wherever the target accepts it, a
                per-request timeout, and a hard wall-clock run budget, so
                one slow or hanging site can never delay the digest. The
                digest must publish on time even if the whole internet is
                down.
  no false      a target that could not be CHECKED at all (the run budget
  all-clear     was spent before reaching it, or a retry series was cut
                short by the budget) is reported as a visible gap line
                (``SiteCheckBudgetSpent``), never folded into "0 problems".
                Same trap ``adapters/email.py`` refuses by not reusing
                ``skos.mail.list_threads``.
  blip vs       one failed request is not a broken site. A site earns
  outage        ``SiteUnreachable`` only after ``SITES_RETRIES`` consecutive
                failed attempts WITHIN THIS RUN, spaced by
                ``SITES_RETRY_DELAY_S``. This module holds no state between
                runs -- the only state any skwatchdog adapter owns anywhere
                is the cursor store in ``skos.watchdog.cursor``, per
                ``adapters/__init__.py``'s own docstring -- so "N
                consecutive DAYS down" was never on the table; in-run retry
                is the tool available, and it is the one used.
  our network   if every site actually checked this run came back down,
  vs theirs     that reads as OUR network being unreachable, not several
                independent Cloudflare zones failing on the same morning.
                With at least a handful of sites checked (``checked >=
                _NETWORK_FAULT_MIN_SITES``), this module refuses to file one
                ``problem`` event per site for what is almost certainly a
                single local fault: it raises instead, so ``collect_safe``
                folds the whole run into one ``SourceUnavailable`` line
                (mirrors ``adapters/email.py``'s "every box failed" rule).
                Below that count, each site's own down event still files
                normally -- there is no flood to guard against with one or
                two sites configured.
  severity      a site wholly unreachable is a ``problem`` (files a GTD
                item, can escalate to a coord card, WD-8/WD-9). A single
                broken outbound link found on an otherwise-reachable site is
                at most ``notable``: a flaky third-party link must not
                manufacture tracked work every morning (the card's own
                example).

WHERE THE SITE LIST COMES FROM
-------------------------------
There is no single machine-readable list of the ~26 Cloudflare-zone sites
anywhere in this repo, or under ``~/clawd``, at the time of writing (checked
``site-repos/``, the SEO template tree, and the domain-standard docs -- all
prose, none of them a config file this adapter could read). Rather than
hardcode a snapshot of someone else's domain inventory into this public
repo, or guess, the list is config, resolved exactly the way
``adapters/email.py`` resolves ``GTD_MAIL_ACCOUNTS``: ``skos.secret_env``,
env first, then the gitignored operator env file.
``SKWATCHDOG_SITES``, comma-separated URLs. The documented default is
EMPTY, so an unconfigured install checks nothing rather than guessing at
Chef's domain inventory.
"""
from __future__ import annotations

import os
import time
from html.parser import HTMLParser
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from ..events import WatchdogEvent, WatchdogLink
from ..port import Window, WatchdogSourceAdapter, registry

#: A polite, self-identifying UA. No target should mistake this run for a
#: browser or a scraper; it names itself and what it is doing.
USER_AGENT = "skos-watchdog-sites/1.0 (+read-only reachability check)"

#: Per-request timeout (HEAD or GET, whichever is in flight for that call).
SITES_TIMEOUT_S = float(os.environ.get("SKWATCHDOG_SITES_TIMEOUT_S", "10"))

#: Total wall-clock budget across every request this run makes (site checks
#: AND the link checks they spawn). The digest must never be delayed by a
#: slow or hanging site; targets past the budget are reported as skipped,
#: never silently dropped.
SITES_RUN_BUDGET_S = float(os.environ.get("SKWATCHDOG_SITES_BUDGET_S", "90"))

#: Consecutive failed attempts, within THIS run, before a site is called
#: unreachable rather than blipped. See module docstring, "blip vs outage".
SITES_RETRIES = int(os.environ.get("SKWATCHDOG_SITES_RETRIES", "3"))

#: Delay between retry attempts for one target.
SITES_RETRY_DELAY_S = float(os.environ.get("SKWATCHDOG_SITES_RETRY_DELAY_S", "2"))

#: Outbound links pulled from one site's homepage and checked, capped so one
#: link-heavy page cannot consume the whole run budget.
MAX_LINKS_PER_SITE = int(os.environ.get("SKWATCHDOG_SITES_MAX_LINKS", "10"))

#: Bytes read from a homepage while looking for links. A reachability probe,
#: not a mirror: this bounds the download regardless of page size.
MAX_PAGE_BYTES = 2_000_000

#: Minimum sites actually checked before "everything failed" is treated as
#: our own network rather than that many independent site outages. See
#: module docstring, "our network vs theirs".
_NETWORK_FAULT_MIN_SITES = 3


class SitesCheckError(RuntimeError):
    """Raised when every site checked this run came back down (and enough
    sites were checked for that to be a meaningful signal): see module
    docstring, "our network vs theirs". Never raised for a single site."""


def _sites() -> list[str]:
    """The operator's site list, resolved the way ``adapters/email.py``
    resolves Gmail accounts: ``SKWATCHDOG_SITES`` (comma-separated URLs) via
    ``skos.secret_env``. Returns [] when unconfigured, so nothing about
    Chef's domain inventory is baked into this public repo, and an
    unconfigured install checks nothing rather than guessing."""
    from ...secret_env import resolve
    raw = resolve("SKWATCHDOG_SITES", "") or ""
    out: list[str] = []
    for u in raw.split(","):
        u = u.strip()
        if u and u not in out:
            out.append(u)
    return out


def _host(url: str) -> str:
    return urlparse(url).netloc or url


class _LinkExtractor(HTMLParser):
    """The only place a fetched page's HTML becomes data: pulls absolute
    http(s) hrefs out of `<a>` tags and nothing else. No text, no title, no
    other tag or attribute is retained, mirroring the read-boundary
    discipline ``adapters/email.py`` and ``adapters/chat_skchat.py`` use for
    their own sources."""

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value and not value.startswith("#"):
                # A pure same-page anchor is not a separate resource to
                # check; requesting it would only re-check this page again.
                absolute = urljoin(self.base_url, value)
                if urlparse(absolute).scheme in ("http", "https"):
                    self.links.append(absolute)
                break


def _extract_links(html_text: str, base_url: str, cap: int) -> list[str]:
    parser = _LinkExtractor(base_url)
    try:
        parser.feed(html_text)
    except Exception:  # noqa: BLE001 - malformed HTML degrades to "no links found"
        return []
    out: list[str] = []
    for link in parser.links:
        if link not in out:
            out.append(link)
        if len(out) >= cap:
            break
    return out


def _open(url: str, method: str, timeout: float):
    """The ONLY place this module opens a real network connection. Every
    other function reaches the network exclusively through here, so tests
    need to monkeypatch exactly this one seam (mirrors
    ``adapters/email.py``'s ``_search_thread_ids`` boundary). Never called
    directly by ``collect()``; always through ``_check_once``/``_fetch_body``.
    """
    req = Request(url, method=method, headers={"User-Agent": USER_AGENT})
    return urlopen(req, timeout=timeout)


def _check_once(url: str, timeout: float) -> tuple[bool, str]:
    """One reachability attempt: HEAD first (politest), falling back to GET
    only when the server rejects HEAD outright (405), since some static
    hosts do. Returns ``(ok, detail)``: ``ok`` is a status under 400;
    ``detail`` is the status code or a short error description, never
    response content."""
    try:
        with _open(url, "HEAD", timeout) as resp:
            status = resp.status
        return status < 400, f"HTTP {status}"
    except HTTPError as exc:
        if exc.code == 405:
            try:
                with _open(url, "GET", timeout) as resp:
                    status = resp.status
                return status < 400, f"HTTP {status}"
            except HTTPError as exc2:
                return False, f"HTTP {exc2.code}"
            except (URLError, OSError, ValueError) as exc2:
                return False, str(exc2)
        return False, f"HTTP {exc.code}"
    except (URLError, OSError, ValueError) as exc:
        return False, str(exc)


def _fetch_body(url: str, timeout: float, max_bytes: int) -> str:
    """A single bounded GET, used only to look for outbound links on a
    homepage already confirmed reachable. Never called for a site that
    failed its own reachability check."""
    with _open(url, "GET", timeout) as resp:
        raw = resp.read(max_bytes)
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def _check_with_retries(url: str, deadline: float) -> tuple[str, str]:
    """Up to ``SITES_RETRIES`` attempts, spaced by ``SITES_RETRY_DELAY_S``,
    so a single blip cannot read as an outage (module docstring, "blip vs
    outage"). Returns ``(status, detail)`` where status is one of:

      "ok"           a request in the series succeeded.
      "down"         every attempt in the full series failed.
      "inconclusive" the run's time budget was spent before the retry
                     series finished. Never reported as a confirmed outage,
                     because that would be a conclusion this run did not
                     actually earn (module docstring, "no false all-clear"
                     -- the inverse failure mode: no false ALARM either).
    """
    last_detail = "no attempt made"
    for attempt in range(1, SITES_RETRIES + 1):
        if time.monotonic() >= deadline:
            return "inconclusive", last_detail
        ok, detail = _check_once(url, SITES_TIMEOUT_S)
        last_detail = detail
        if ok:
            return "ok", detail
        if attempt < SITES_RETRIES:
            time.sleep(SITES_RETRY_DELAY_S)
    return "down", last_detail


@registry.register
class SitesAdapter(WatchdogSourceAdapter):
    name = "sites"

    def collect(self, window: Window) -> list[WatchdogEvent]:
        sites = _sites()
        if not sites:
            return []

        deadline = time.monotonic() + SITES_RUN_BUDGET_S

        out: list[WatchdogEvent] = []
        skipped: list[str] = []
        checked = 0
        down = 0
        last_down_detail = ""

        for url in sites:
            if time.monotonic() >= deadline:
                skipped.append(url)
                continue
            status, detail = _check_with_retries(url, deadline)
            if status == "inconclusive":
                skipped.append(url)
                continue
            checked += 1
            if status == "down":
                down += 1
                last_down_detail = detail
                out.append(self._unreachable_event(window, url, detail))
                continue
            broken = self._check_links(url, deadline)
            if broken:
                out.append(self._broken_links_event(window, url, broken))

        if checked >= _NETWORK_FAULT_MIN_SITES and down == checked:
            raise SitesCheckError(
                f"all {checked} configured site(s) checked this run failed "
                f"({last_down_detail}); treating as a local network fault, "
                "not simultaneous outages across independent sites")

        if skipped:
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="SiteCheckBudgetSpent",
                object="sites", severity="notable",
                summary=(f"{len(skipped)} configured site(s) were not checked (or not "
                         f"confirmed) this run: the run's time budget was spent. "
                         f"No line above covers them."),
                link=WatchdogLink(uri="skworld://skos/watchdog/sites", http=""),
                ref=f"{self.name}:budget-spent:{window.until[:10]}",
                meta={"skipped": len(skipped)},
            ))

        if checked and not out:
            # Every configured site was checked (and confirmed), none down,
            # no broken links found: a visible "all clear" line, mirroring
            # scheduler.py's SchedulerHealthy, so silence is never the only
            # signal of a clean run.
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="SitesHealthy",
                object="sites", severity="info",
                summary=f"{checked} site(s) checked, all reachable, no broken links found.",
                link=WatchdogLink(uri="skworld://skos/watchdog/sites", http=""),
                ref=f"{self.name}:summary:{window.until[:10]}",
                meta={"checked": checked},
            ))

        return out

    # -- link discovery, run only against a site already confirmed reachable

    def _check_links(self, site_url: str, deadline: float) -> list[str]:
        if time.monotonic() >= deadline:
            return []
        try:
            body = _fetch_body(site_url, SITES_TIMEOUT_S, MAX_PAGE_BYTES)
        except (HTTPError, URLError, OSError, ValueError):
            return []  # reachability is already established; skip link discovery
        links = _extract_links(body, site_url, MAX_LINKS_PER_SITE)
        broken: list[str] = []
        for link in links:
            if time.monotonic() >= deadline:
                break
            ok, _detail = _check_once(link, SITES_TIMEOUT_S)
            if not ok:
                broken.append(link)
        return broken

    # -- event builders

    def _unreachable_event(self, window: Window, url: str, detail: str) -> WatchdogEvent:
        host = _host(url)
        date = window.until[:10]
        return WatchdogEvent(
            ts=window.until, source=self.name, kind="SiteUnreachable", object=host,
            severity="problem",
            summary=f"{host} was unreachable after {SITES_RETRIES} attempts ({detail}).",
            link=WatchdogLink(uri=f"skworld://skos/watchdog/sites/{host}", http=url),
            ref=f"{self.name}:unreachable:{host}:{date}",
            meta={"url": url, "attempts": SITES_RETRIES, "detail": detail},
        )

    def _broken_links_event(self, window: Window, site_url: str,
                             broken: list[str]) -> WatchdogEvent:
        host = _host(site_url)
        date = window.until[:10]
        count = len(broken)
        return WatchdogEvent(
            ts=window.until, source=self.name, kind="BrokenLinksFound", object=host,
            severity="notable",
            summary=f"{count} broken link(s) found on {host}.",
            link=WatchdogLink(uri=f"skworld://skos/watchdog/sites/{host}", http=site_url),
            ref=f"{self.name}:broken-links:{host}:{date}",
            meta={"site": site_url, "broken": broken},
        )


__all__ = ["SitesAdapter", "SitesCheckError", "SITES_RETRIES", "SITES_TIMEOUT_S",
           "SITES_RUN_BUDGET_S", "MAX_LINKS_PER_SITE"]
