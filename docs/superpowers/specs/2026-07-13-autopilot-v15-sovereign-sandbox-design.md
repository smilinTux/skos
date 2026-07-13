# SKOS Autopilot v1.5 - Sovereign Sandbox (Docker) Design

**Status:** design, awaiting Chef review before writing-plans.
**Companion to:** `docs/skos-autopilot-architecture.md` section 12 (harness security model).
**Coord:** `07c78c7f`.

## 1. Goal

Complete the harness confinement so `ClaudeCodeAdapter` live execution can be
safely turned on. The engineering executor's `run_task` and `grade` spawn
`claude -p`; v1.5 makes that spawn run inside a disposable Docker container that
(a) cannot read any secret on the host and (b) cannot reach any host outside a
pinned allowlist. The guarantee is proven by an integration test that runs a real
confined container, not asserted in prose. Only after that proof is green may
`live_execution` be enabled, and even then it stays a deliberate, config-gated,
human act.

## 2. Context (what already exists, what changed)

- `src/skos/autopilot/claude_code.py` already has the seam: `LaunchConfig`
  (argv, worktree, bash_wrapper, egress_allowlist), `build_launch`, and `_spawn`
  as the single subprocess boundary. `_spawn` raises `HarnessUnavailable` unless
  `live_execution=True` (posture C, v1). The tool firewall (`is_forbidden`) is
  enforced now: it normalizes input, rejects whole-server and empty-segment
  `mcp__...` grants, denies secret/comms/kms/trustee/ansible/sk-access tools by
  category, and the constructor fails closed if the allowlist contains any.
- The v1 scaffolding assumed a `bwrap` + `--unshare-net` confinement. **Grounding
  on noroc2027 (the intended autopilot node) found that path is not viable
  rootless here:** `bwrap` fails at `setting up uid map: Permission denied` and
  `unshare -Urn` fails identically. Unprivileged user and network namespaces are
  blocked for the agent, so the bwrap approach cannot confine either filesystem
  or network without a host posture change.
- What IS available on the node: the Docker daemon is reachable, passwordless
  sudo works, `nft`/`iptables` exist, and base images `node:24-bookworm` and
  `python:3.12-slim` are present. Chef's decision: build the sandbox on Docker.

## 3. Architecture

The seam does not move. Everything above `_spawn` (orchestrator, executor, twin
gate, digest) is untouched. v1.5 replaces the dead bwrap branch inside `_spawn`
with a Docker branch:

```
executor.run_task/grade
  -> adapter.build_launch(instruction, data, worktree, repo_remote, ci_endpoint)
       -> LaunchConfig{ argv=[claude,-p,prompt], worktree, egress_allowlist }
  -> adapter._spawn(cfg)                      # the only subprocess boundary
       -> _ensure_sandbox_capable(node)       # fail closed if docker/proxy missing
       -> _run_in_container(cfg)              # docker run, disposable, confined
            - internal network (no external route)
            - allowlist proxy sidecar = the sole egress
            - worktree bind RW, cred file bind RO, nothing else from host home
       -> parse structured stdout -> dict
```

The container is the confinement boundary (the Docker daemon runs as root, so the
rootless-userns limitation does not apply). Filesystem confinement is achieved by
mounting nothing secret rather than by hiding it: the container's HOME is the
image's own, so `~/.skcapstone`, the skvault file, `~/.hermes`, and `~/.ssh` do
not exist inside it.

## 4. Components

### 4.1 Sandbox image (`skos-autopilot-sandbox`)

A pinned image, built deliberately (Dockerfile in-repo under
`docker/autopilot-sandbox/`), never pulled or mutated at runtime:
- base `node:24-bookworm`
- `@anthropic-ai/claude-code` at a pinned version
- `git`, plus the engineering repo toolchain (for skos: python 3.12 + pytest +
  coverage). A repo whose `RepoSpec` needs a different toolchain either uses a
  per-repo image tag (`RepoSpec.sandbox_image`, optional, default the shared one)
  or the shared image if its toolchain suffices.
- a non-root user `sbx` (uid mapped), working dir `/work`.
The image tag is pinned in config (`autopilot.yaml: harness.sandbox_image`) so a
run is reproducible and a supply-chain change is a deliberate rebuild.

### 4.2 Container launch (`_run_in_container`)

`docker run` flags (assembled by the adapter, inspectable before spawn):
- `--rm` (disposable), `--user sbx`, `--workdir /work`
- `--read-only` root filesystem + `--tmpfs /tmp` + `--tmpfs /home/sbx` (writable
  scratch only where needed)
- `--mount type=bind,src=<worktree>,dst=/work` (RW, the only writable host path)
- `--mount type=bind,src=~/.claude/.credentials.json,dst=/home/sbx/.claude/.credentials.json,ro`
  (the single OAuth file, read-only, nothing else from `~/.claude`)
- `--network <internal-net>` (see 4.3), `--env HTTPS_PROXY/HTTP_PROXY=http://sbxproxy:8080`
- hardening: `--security-opt no-new-privileges`, `--cap-drop ALL`,
  `--pids-limit`, `--memory`/`--cpus` from `caps`, no `--privileged`, no docker
  socket mounted into the container.
- argv: the existing `LaunchConfig.argv` (`claude -p <prompt>` with
  `--dangerously-skip-permissions` and the `--allowedTools` allowlist).

The untrusted session inside the container gets the worktree, the toolchain, the
cred file, and a single egress path. It cannot escalate (no-new-privileges, caps
dropped), cannot read host secrets (not mounted), cannot mount the docker socket.

### 4.3 Pinned egress: internal network + allowlist proxy (sole egress)

Docker-native realization of "no network except a pinned proxy":
- `docker network create --internal sbx-internal` - an **internal** network has
  no external route at all.
- The **allowlist proxy** runs as a small sidecar **dual-homed** on both
  `sbx-internal` and a normal bridge. It is the only reachable host from the
  sandbox container and the only thing with outward access.
- The sandbox container joins only `sbx-internal` and routes all HTTP(S) through
  `HTTPS_PROXY=http://sbxproxy:8080`. It has no other route out.

The proxy is a sovereign ~60-line Python `http.server`-based forward proxy
(`src/skos/autopilot/sandbox_proxy.py`) that:
- handles only `CONNECT host:port`, allows the connection **only** if `host` is in
  the pinned allowlist (the task repo's git remote host, the CI host, and the
  skgateway host from `egress_allowlist`), else returns `403` and closes.
- allowlist is passed in at start (no config file inside the jail), matched on
  exact host (and optional port). No wildcard by default.
- logs every allow/deny to the run journal for audit.
It runs as a sidecar container built into the sandbox image (or a tiny separate
image), started per run and torn down with the network, so there is no
long-lived proxy attack surface.

Rationale for internal-network-plus-proxy over `--network none`: a truly
network-less container cannot reach a host proxy, so `--network none` alone gives
no controlled egress. `--internal` + a dual-homed proxy is the precise,
hostname-level, root-free equivalent of the intent.

### 4.4 Node-capability detection + fail closed

`_ensure_sandbox_capable()` checks, before any live spawn: docker daemon
reachable, the sandbox image present at the pinned tag, and the proxy image
present. If any is missing, engineering execution on that node raises
`HarnessUnavailable` (fail closed), never downgrades to an unconfined run. This
mirrors section 12's per-node fail-closed rule.

### 4.5 `_spawn` rewrite

`_spawn` keeps its contract (in: `LaunchConfig`, out: parsed dict). Under
`live_execution=True` it now: ensures capability, creates the ephemeral internal
network, starts the proxy sidecar with the egress allowlist, runs the sandbox
container, captures structured stdout, tears down container + proxy + network in
a `finally`, and returns the parsed payload. Under `live_execution=False`
(default) it raises exactly as today. The old `bash_wrapper` bwrap argv is
removed; `LaunchConfig.egress_allowlist` is now consumed by the proxy.

## 5. Security model

Threat -> control:
- Prompt-injection -> secret read -> exfil: secrets are not in the container
  (not mounted), so there is nothing to read; even if a secret were present,
  egress is allowlisted to non-attacker hosts, so it cannot leave.
- Redirected git push to attacker remote: the proxy denies any host but the
  repo's real git remote and CI, so a push cannot be redirected out.
- Tool-based exfil (comms/secret MCP tools): already denied by `is_forbidden`
  (enforced today), independent of the sandbox.
- Container escape / privilege: `--cap-drop ALL`, `--no-new-privileges`,
  non-root user, read-only rootfs, no docker socket, resource caps.
Residuals (documented, bounded): the inference channel itself
(claude -> Anthropic, and the local skgateway path) is a real external reach and
is enumerated explicitly in the egress allowlist as a named, reviewed path; the
data-framing rule (untrusted text as data, never instructions) plus the denial of
all outbound-comms tools bound the exfil-via-inference risk inherent to any LLM
agent. The Docker daemon is a root-equivalent trust the orchestrator (not the
untrusted session) holds; the session never gets the socket.

## 6. The confinement proof (acceptance gate)

`tests/test_sandbox_confinement_it.py`, gated behind `RUN_SANDBOX_IT=1` and
docker availability (skipped otherwise, so CI without docker stays green). It
builds/uses the pinned image and asserts, in a real confined container:
1. **Secrets unreadable:** `cat /home/sbx/.skcapstone/...`, the skvault path, and
   `/home/sbx/.hermes/.env` all fail (no such file) - the host secret trees are
   absent inside.
2. **Off-allowlist unreachable:** a request to a non-allowlisted host (for
   example `https://example.com`) through the proxy is denied/times out.
3. **On-allowlist reachable:** a request to the allowlisted git host succeeds
   through the proxy.
4. **Worktree writable, host RO:** `/work` is writable; attempting to write any
   other host-origin path fails.
Until this test is green on a node, `live_execution` must not be enabled there.

## 7. Posture and config gating

- The build wires the Docker `_spawn` and lands the proof test, but
  `live_execution` **defaults OFF**. Enabling it is a two-key act: set
  `autopilot.yaml: harness.live_execution: true` AND the run must be an explicit
  `--no-dry-run --canary` on a single task. Merging v1.5 does not, by itself,
  turn on live execution.
- First live use is a Chef-gated canary on one repo-routed task, PR-only
  (`automerge_repos` stays empty), so the first real run opens a PR for human
  review rather than merging.

## 8. Error handling

- Missing docker/image/proxy: `HarnessUnavailable`, fail closed, escalate the
  item to the digest (not a crash).
- Container non-zero exit / timeout: captured as a failed round in the twin gate
  (the executor already treats a non-passing grade as a retry, then escalate);
  the container is always torn down in `finally`.
- Proxy denial during a run: surfaced in the grade notes and journal; a task that
  legitimately needs an unlisted host escalates as a decision (add-to-allowlist),
  never silently opens egress.

## 9. Testing strategy

- Unit (no docker): the docker `run` argv is composed into an inspectable
  structure (like `LaunchConfig`), and unit tests assert the flags (read-only,
  cap-drop, no-new-privileges, the single cred bind, internal network, proxy env,
  no socket mount) without spawning. The proxy's allow/deny logic is unit-tested
  directly against a host allowlist (allow git host, deny example.com, deny
  empty/attacker host).
- Integration (docker, gated): the section 6 confinement proof.
- Regression: the full existing suite (473) stays green; posture-C dry-run tests
  are unchanged because `live_execution` still defaults off.

## 10. Out of scope (future)

- Non-claude harness adapters running confined (the pattern generalizes: each
  adapter's `_spawn` runs in the same container primitive, but only
  `ClaudeCodeAdapter` is wired in v1.5).
- Multi-node sandbox rollout (v1.5 targets noroc2027; other nodes gain it by
  installing the image + proxy and passing the same proof).
- The ops/ITIL executor's remediation confinement (separate gate; ops actions run
  through the reversible-operations allowlist, a different control).
