# SKOS Autopilot v1.5 - Sovereign Sandbox + Swappable Harness Design

**Status:** design, awaiting Chef review before writing-plans.
**Companion to:** `docs/skos-autopilot-architecture.md` sections 11 (harness seam) and 12 (harness security model).
**Coord:** `07c78c7f`.

## 1. Goal

Two coupled deliverables:

1. **Sovereign sandbox.** Complete the harness confinement so live execution can
   be safely turned on. The engineering executor's `run_task`/`grade` spawn a
   coding-agent CLI; v1.5 makes that spawn run inside a disposable Docker
   container that (a) cannot read any host secret and (b) cannot reach any host
   outside a pinned allowlist. Proven by an integration test that runs a real
   confined container, not asserted in prose.

2. **Swappable harness registry.** Promote the single hardcoded `ClaudeCodeAdapter`
   into a clean registry of harness adapter modules, so claude-code, pi, opencode,
   and codex can be swapped by name (`--harness`) or config with no change above
   the seam. The sandbox is harness-agnostic: it confines whatever CLI runs inside.

The strategic default is **pi** driving a **local sovereign model via skgateway**,
because that keeps even the inference channel on the tailnet (no external reach at
all). `live_execution` stays config-gated regardless of harness: landing v1.5 does
not turn on live execution; the first live run is a Chef-gated PR-only canary.

## 2. Context (what exists, what changed)

- The seam already exists: `harness.py` defines the `HarnessAdapter` Protocol
  (`capabilities`/`assess`/`run_task`/`grade`) and `ProviderCapabilities`;
  `claude_code.py` implements it with `LaunchConfig` (argv, worktree,
  egress_allowlist), `build_launch`, and `_spawn` as the single subprocess
  boundary. `_spawn` raises `HarnessUnavailable` unless `live_execution=True`
  (posture C). The tool firewall (`is_forbidden`) is enforced now: normalizes
  input, rejects whole-server and empty-segment `mcp__...` grants, denies
  secret/comms/kms/trustee/ansible/sk-access tools by category, fails closed.
- **bwrap is not viable rootless on noroc2027** (the intended node): `bwrap`
  fails `setting up uid map: Permission denied`; `unshare -Urn` fails the same.
  Unprivileged user/network namespaces are blocked for the agent. Docker daemon
  is reachable, passwordless sudo works, `nft`/`iptables` exist. Chef's decision:
  build the sandbox on Docker.
- **Harness landscape on the box (grounded):** `claude` installed (wrapped as the
  lumina agent); `opencode` installed with headless `opencode run [message]` plus
  `serve`/`acp` server modes, MIT, can target local models; `codex` is a stub
  ("not installed"); `pi` not installed. **pi** (pi.dev) is MIT, self-hostable,
  "primitives not features," supports 15+ providers **including Ollama/local**,
  and has a headless `pi -p` / `--mode json` mode plus an RPC protocol and SDK,
  with permission-gating and sandboxing via extensions. pi + a local model =
  maximal sovereignty and control, which is why it is the target default.

## 3. Architecture

Two layers, cleanly separated:

```
executor.run_task/grade
  -> harness = HarnessRegistry.get(config.harness)        # pi | claude-code | opencode | codex
  -> harness.build_launch(instruction, data, worktree, repo)  # -> LaunchConfig (argv, auth mounts, egress, image)
  -> Sandbox.spawn(launch)                                 # HARNESS-AGNOSTIC confinement
       -> _ensure_capable(node)                            # docker + image + proxy present, else fail closed
       -> disposable container: worktree RW, harness auth RO, nothing secret,
          internal network + allowlist proxy = sole egress
       -> harness.parse(stdout) -> dict                    # per-harness output shape
```

- **Harness adapter** = the per-tool module. It knows ONLY how to invoke its CLI
  headlessly, what auth it needs, which image carries it, how to parse its output,
  and what it can do (`capabilities`). It does NOT know about Docker.
- **Sandbox** = the shared confinement. It knows ONLY how to run an arbitrary
  `LaunchConfig` in a disposable, secret-free, egress-pinned container. It does
  NOT know which harness it is running.

This is the modularization Chef asked for: adding or swapping a harness is a new
adapter module, never a sandbox change; hardening the sandbox is one place, for
all harnesses.

## 4. The harness registry (swappability)

### 4.1 Adapter contract

Each adapter is a module implementing `HarnessAdapter` plus a `LaunchSpec`
descriptor the sandbox consumes. The Protocol gains no new *methods* above the
seam; what becomes first-class is the descriptor the adapter hands the sandbox:

```python
@dataclass
class LaunchSpec:
    name: str                       # "pi" | "claude-code" | "opencode" | "codex"
    argv: list[str]                 # headless invocation, prompt included
    image: str                      # sandbox image tag that carries this CLI + toolchain
    auth_mounts: list[AuthMount]    # host cred paths -> container paths, read-only
    auth_env: dict[str, str]        # provider keys / model routing (e.g. skgateway base URL)
    egress_hosts: list[str]         # hosts this harness must reach (inference endpoint, etc.)
    parse: Callable[[str], dict]    # stdout -> structured payload
    capabilities: ProviderCapabilities
```

`HarnessRegistry.get(name)` returns the adapter; `--harness <name>` and
`autopilot.yaml: harness.name` select it; unknown name fails closed at load with
the list of registered harnesses. `warn_missing_capabilities` (already in
`harness.py`) runs against the selected adapter's `capabilities`.

### 4.2 Per-harness realizations

- **pi (target default).** argv `pi -p <prompt> --mode json` (headless JSON).
  Model routed to **skgateway** (`http://localhost:18780/v1`, sovereign local
  model, e.g. ornith) via `auth_env` (`OPENAI_BASE_URL`/pi provider config) or a
  bundled `models.json`; OR to a frontier model if Chef sets that. `egress_hosts`
  = skgateway only when local (fully on-tailnet). Image: `sandbox-pi` (node +
  `pi` + git + toolchain). auth: provider key env (or none for local skgateway).
- **claude-code (works today, refactored into the registry).** argv
  `claude -p <prompt> --output-format json --dangerously-skip-permissions
  --allowedTools <allowlist>`. auth: RO-bind `~/.claude/.credentials.json`.
  `egress_hosts` = Anthropic API (external, named + reviewed). Image:
  `sandbox-claude`.
- **opencode (installed, sovereign fallback).** argv `opencode run <message>
  --pure` (no external plugins) with a model flag; can target skgateway/local or
  a provider. auth: `~/.config/opencode` + `~/.local/share/opencode` creds RO (or
  provider env). Image: `sandbox-opencode`.
- **codex (roadmap; currently a stub on the box).** argv `codex exec <prompt>`
  when a real codex is installed. Descriptor stubbed now, adapter implemented when
  codex is actually present. Its presence in the registry proves the swap surface.

v1.5 implements the registry + **pi**, **claude-code**, and **opencode** adapters
(three real, swappable harnesses). codex is registered as a stub adapter that
fails closed with "codex not installed on this node" until wired.

## 5. The sandbox (harness-agnostic confinement)

### 5.1 Container (per spawn, disposable)

- **Image:** `LaunchSpec.image`, a pinned image built in-repo under
  `docker/sandbox/<harness>/`, base `node:24-bookworm` + the harness CLI + `git`
  + the repo toolchain (python 3.12 + pytest for skos). Version-pinned, never
  pulled at runtime, non-root user `sbx`, workdir `/work`.
- **Filesystem:** worktree bind-mounted **RW at `/work`** (only writable host
  path); `--read-only` rootfs; tmpfs `/tmp` and `/home/sbx`. Container HOME is the
  image's own, so `~/.skcapstone`, skvault, `~/.hermes`, `~/.ssh` **do not exist
  inside** (confinement by absence, not by hiding). No host home mount.
- **Auth:** only `LaunchSpec.auth_mounts` (RO) and `auth_env`. For pi-on-skgateway
  that is just a base-URL env and no cred file at all (nothing external to leak).
  For claude-code, the single `~/.claude/.credentials.json` RO. Nothing else.
- **Hardening:** `--rm`, `--user sbx`, `--security-opt no-new-privileges`,
  `--cap-drop ALL`, `--pids-limit`, `--memory`/`--cpus` from `caps`, no
  `--privileged`, **docker socket never mounted into the container**.

### 5.2 Pinned egress: internal network + allowlist proxy (sole egress)

- `docker network create --internal sbx-internal` - no external route at all.
- A sovereign ~60-line Python allowlist **proxy sidecar** (`sandbox_proxy.py`),
  **dual-homed** on `sbx-internal` and a normal bridge, is the only reachable host
  from the sandbox and the only thing with outward access. It handles `CONNECT`
  and allows only hosts in the pinned allowlist = the repo git remote + CI +
  `LaunchSpec.egress_hosts` (the harness inference endpoint). Everything else
  gets `403`, logged to the run journal.
- The sandbox container joins only `sbx-internal` with
  `HTTPS_PROXY=http://sbxproxy:8080`; it has no other route out.
- **Sovereignty win:** when the harness is pi/opencode on skgateway, the only
  egress host is skgateway (local tailnet), so a live run makes **no external
  network reach at all**. With claude-code, the Anthropic endpoint is the one
  named, reviewed external path.

### 5.3 Node-capability + fail closed

`Sandbox._ensure_capable()` checks docker reachable + the selected harness image
present + proxy image present, before any live spawn. Missing -> `HarnessUnavailable`
(fail closed), never an unconfined run. Per-node, mirroring section 12.

### 5.4 `_spawn` becomes `Sandbox.spawn`

The subprocess boundary moves from `ClaudeCodeAdapter._spawn` to a shared
`Sandbox.spawn(launch: LaunchSpec+worktree)`. Under `live_execution=True` it
ensures capability, creates the ephemeral internal network, starts the proxy with
the egress allowlist, runs the container, captures structured stdout, tears down
container + proxy + network in `finally`, returns `launch.parse(stdout)`. Under
`live_execution=False` (default) it raises exactly as today. Each adapter's
`assess`/`run_task`/`grade` call `Sandbox.spawn` with their `build_launch` output;
the old bwrap `bash_wrapper` is removed.

## 6. Security model

Threat -> control (unchanged by harness choice, because the sandbox is the same):
- injection -> secret read -> exfil: secrets not in the container; even if present,
  egress is allowlisted to non-attacker hosts.
- redirected git push: proxy denies any host but the repo's real remote + CI.
- tool exfil: `is_forbidden` denies comms/secret MCP tools already.
- escape/privilege: cap-drop, no-new-privileges, non-root, read-only rootfs, no
  docker socket, resource caps.
Residual: the inference channel is a real reach and is a **named** egress host.
With a local model (pi/opencode on skgateway) that host is on-tailnet, so the
residual external-inference reach is **eliminated**, not just bounded. The Docker
daemon is a root-equivalent trust the orchestrator holds; the untrusted session
never gets the socket. Per-harness auth is least-privilege: only that harness's
cred, read-only, and for local-model harnesses often none.

## 7. The confinement proof (acceptance gate)

`tests/test_sandbox_confinement_it.py`, gated behind `RUN_SANDBOX_IT=1` + docker
availability (skipped otherwise). In a real confined container it asserts:
1. host secret trees (`.skcapstone`, skvault, `.hermes/.env`) are absent/unreadable;
2. an off-allowlist host (`example.com`) is denied through the proxy;
3. the allowlisted git host reaches through the proxy;
4. `/work` is writable, other host paths are not.
Run per harness image in scope (at least pi and claude-code). Until green on a
node, `live_execution` must not be enabled there.

## 8. Posture and config gating

- `autopilot.yaml: harness.name` (default `claude-code` until the pi adapter +
  proof are green, then Chef may switch the default to `pi`), `harness.model`
  (routing, e.g. skgateway `sk-default` for pi), `harness.live_execution`
  (**default false**), `harness.sandbox_image` per harness.
- Enabling live execution is a two-key act: `harness.live_execution: true` AND an
  explicit `--no-dry-run --canary` on one task. Merging v1.5 does not enable it.
- First live use: a Chef-gated PR-only canary (`automerge_repos` stays empty).

## 9. Error handling

- missing docker/image/proxy or unknown harness name: `HarnessUnavailable` /
  load-time error, fail closed, escalate to the digest, no crash.
- container non-zero/timeout: a failed grade round (executor already retries then
  escalates); container always torn down in `finally`.
- proxy denial mid-run: surfaced in grade notes + journal; a task needing an
  unlisted host escalates as an add-to-allowlist decision, never silent egress.

## 10. Testing strategy

- **Unit (no docker):** the docker `run` argv and the `LaunchSpec` are composed
  into inspectable structures; assert per harness the flags (read-only, cap-drop,
  no-new-privileges, the exact auth mounts, internal network, proxy env, no socket
  mount) without spawning. Each adapter's `build_launch`/`parse` unit-tested
  against a captured sample of its CLI's real JSON output. The proxy allow/deny
  logic unit-tested against a host allowlist. Registry selection + fail-closed on
  unknown name unit-tested.
- **Integration (docker, gated):** the section 7 proof, per harness image.
- **Regression:** the full existing suite (473) stays green; posture-C dry-run
  tests unchanged because `live_execution` still defaults off.

## 11. Phasing / scope

- **v1.5 (this build):** the Sandbox module + HarnessRegistry + adapters for
  **pi** (sovereign default target), **claude-code** (refactored), **opencode**
  (installed fallback); codex a fail-closed stub adapter. Confinement proof for pi
  + claude-code images. Config gating. Docs updated (architecture section 12,
  SOP).
- **Out of scope / future:** codex adapter once codex is really installed;
  multi-node sandbox rollout (v1.5 targets noroc2027; other nodes install the
  images + proxy and pass the proof); confining the ops/ITIL executor's
  remediation (separate control); a `serve`/RPC long-session mode for pi/opencode
  (v1.5 uses one-shot headless per round, matching the fresh-context Ralph loop).
