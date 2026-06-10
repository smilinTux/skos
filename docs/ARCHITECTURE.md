# skos Architecture

skos is a **ports & adapters** foundation: capabilities are ports, concrete tools
are adapters, and a profile decides which adapter each port resolves to. Everything
else — the data-root, the descriptor, the renderers, the brain, the surfaces — exists
to make "declare what, resolve how" work from a laptop to a cluster.

## The ports/adapters model

```mermaid
flowchart LR
    APP["app.yaml descriptor<br/>(declares capabilities needed)"] --> RES{resolver<br/>+ profile}
    RES -->|personal| A1["adapter A<br/>(e.g. ollama, local postgres)"]
    RES -->|team| A2["adapter B<br/>(e.g. vllm, shared postgres)"]
    RES -->|enterprise| A3["adapter C<br/>(e.g. k8s-served, clustered)"]
    A1 & A2 & A3 --> REND["renderer<br/>(compose / k8s / nomad)"]
    REND --> DEPLOY["deployed service"]
```

You write one `app.yaml`. The resolver maps each declared capability to a concrete
adapter for your profile; the renderer turns the result into a platform manifest.
Swap a profile, get a different deployment — same descriptor.

## The install flow

```mermaid
sequenceDiagram
    participant Op as operator/agent
    participant Setup as skos setup
    participant Plan as skos plan
    participant Inst as skos install
    participant Reg as registry
    participant FS as $SK_DATA_ROOT
    Op->>Setup: create data-root tree + recommended personal set
    Op->>Plan: --profile <p> → resolve capability→adapter (dry run)
    Plan-->>Op: install plan (what would happen)
    Op->>Inst: --profile <p>
    Inst->>FS: ensure data-root tree
    Inst->>Reg: record installed capabilities + adapters
    Inst-->>Op: applied
```

CLI surface: `skos {path, profile, descriptor, list, materialize, capabilities,
resolve, render, setup, plan, install}` plus the brain/surface commands below.

## The data-root abstraction

Every service reads and writes under **`$SK_DATA_ROOT`**, resolved per profile.
`skos path <subdir>` prints the absolute path — so code never hardcodes locations
and the same app works whether the root is `~/.skdata` on a laptop or a mounted
volume in a cluster.

## The brain (self-building knowledge ontology)

skos carries an **entity-graph brain**: an `EntityNode` ontology that a wiki/graph
self-builds and validates.

```mermaid
flowchart TD
    INIT["skos brain init<br/>(scaffold entity-graph skeleton + self-build prompt)"] --> NODES["EntityNode files<br/>(typed, schema-validated)"]
    NODES --> IDX["skos brain index <ns><br/>(build _index.md per namespace)"]
    NODES --> VAL["skos brain validate<br/>(check node vs EntityNode schema)"]
    IDX --> SURF["surfaces expose it"]
```

## Surfaces (runtime adapters)

The brain is exposed through **surfaces** — runtime adapters that map skos entities
into a host environment (Obsidian, Claude Code, Codex, n8n).

```mermaid
flowchart LR
    BRAIN["skos brain<br/>(EntityNodes)"] --> SURFPORT{Surface port}
    SURFPORT --> OBS["obsidian"]
    SURFPORT --> CC["claude-code"]
    SURFPORT --> CX["codex"]
    SURFPORT --> N8N["n8n"]
```

`skos surface {resolve, list, entities, show}` — list registered surfaces, see what
entities they expose, and render an entity node as markdown for that surface.

## Where skos sits under everything else

```mermaid
flowchart TD
    subgraph STACK["SKWorld services (all deployed *through* skos)"]
      SKOPS["skops (ops/ITIL)"]
      SKINGEST["skingest (ingest)"]
      SKCHAT["skchat"]
      SKMEMORY["skmemory"]
      DOTS["… every sk* capability"]
    end
    STACK -->|"app.yaml + skos resolve/render/install"| SKOS["**skos** foundation<br/>data-root · descriptor · resolver · renderers · packaging · brain"]
    SKOS --> PROFILES["profiles: personal → team → enterprise"]
```

skos is the **#1 sub-project** of the v2 build sequence — the filesystem & packaging
foundation the rest of the stack is built on (design specs:
`2026-06-09-skos-{filesystem-packaging-foundation,capability-map,brain-architecture}.md`).
