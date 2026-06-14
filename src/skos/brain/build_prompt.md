# skos Brain — Self-Build Prompt

> This prompt is laid down by `skos brain init` on first install.
> An agent (Claude Code / Lumina) runs it to flesh out the entity-graph skeleton.
> Origin: Karpathy gist karpathy/442a6bf5, re-pointed at the skos EntityNode
> ontology + frontmatter schema + dual-write (canon/skmem-pg) + indexing rules.
> Date: 2026-06-09 (skos subsystem-brain-ontology)

---

## Context: what you're building

The skos Brain is a **git-backed Markdown entity-graph** — the definitions layer
of the sovereign AI-OS.  Every component (agent, skill, workflow, rule, tool,
knowledge, project, output, memory, command, namespace) is a **typed EntityNode**:
YAML frontmatter carrying identity + lifecycle + edges + runtime pointers, plus a
Markdown body for human and agent reading.

**The three-layer model (commit this to memory):**

| Layer | Role | Location |
|-------|------|----------|
| git wiki (canon) | Source of truth — definitions only | `~/clawd/wiki/pages/entities/<namespace>/` |
| skmem-pg | Retrieval projection — embeddings + BM25 + AGE | Postgres on .158 |
| Promotion loop | draft → reviewed → canon | `skos brain` CLI |

**Rule:** write to canon (git), project to index (skmem-pg), promote learnings
back to canon.  skmem-pg is a *derived projection*, never a second source.

---

## EntityNode frontmatter schema (required fields)

```yaml
---
id: <kebab-case-slug>            # e.g. agent-lumina, skill-wiki-ingest
type: <entity_type>              # agent|skill|workflow|rule|tool|knowledge|project|output|memory|command|namespace
namespace: <namespace>           # e.g. skos, skmemory, skcomms, meta-ads
lifecycle_state: draft           # draft → reviewed → canon
summary: "One-line summary."     # scanned by agents in _index.md
runtime_adapters: []             # optional: [claude-code, codex, paperclip]
tools: []                        # optional: list of tool entity ids
edges:                           # optional: directed edges to other nodes
  - target: <entity-id>
    type: depends_on             # depends_on|cites|defines|requires|relates_to
    weight: 0.9
state_stored_at: ""              # optional: postgres://... or path (not committed to git)
---
```

Edge type semantics:
- `depends_on` — this node requires the target to function
- `cites` — this node references the target as a source
- `defines` — this node is the canonical definition of the target concept
- `requires` — this node requires the target at runtime
- `relates_to` — general association (default)

---

## Index discipline (first-class requirement)

Every namespace has a `_index.md` listing **every entity with its one-line summary**.
Agents MUST scan the index first; only open individual nodes when needed.

Generate/refresh indexes with: `skos brain index <namespace>`

Canonical format (auto-generated, do not hand-edit):
```markdown
---
namespace: <namespace>
entity_count: N
updated: <ISO-date>
---

# <namespace> — entity index

| id | type | lifecycle | summary |
|----|------|-----------|---------|
| agent-lumina | agent | canon | Queen of SKWorld, DevOps engineer. |
```

---

## Your build task

The scaffold (namespaced directories + stub indexes) was created by `skos brain init`.
Now you need to **flesh it out** by creating entity nodes for the key components of
this sovereign AI-OS.  Work namespace by namespace.

### Step 1 — skos namespace (the OS itself)

Create entity nodes for:
- `namespace-skos` (type: namespace) — the skos service registry and packaging foundation
- `namespace-skos-brain` (type: namespace) — this entity graph
- `namespace-skmemory` (type: namespace) — memory system (tiered, skmem-pg)
- `namespace-skingest` (type: namespace) — ingestion pipeline (dual-write)
- `namespace-skinterface` (type: namespace) — runtime adapter surfaces

### Step 2 — agents namespace

For each known agent in `~/.skcapstone/agents/`, create an entity node:
- Read the soul blueprint at `~/.skcapstone/agents/<name>/soul/base.json`
- Extract: description, capabilities, runtime, tools used
- Write node: type=agent, namespace=agents, lifecycle_state=draft (promote when reviewed)
- Add edges to the skills and tools it uses

Core agents to seed: lumina, opus, jarvis, ava.

### Step 3 — skills namespace

The 68+ skills live in `~/clawd/skills/`.  For each skill directory:
- Read the skill's README or main .md file
- Create a skill entity node summarising: what it does, what agent uses it, CLI trigger
- lifecycle_state=draft

Seed the highest-value ones first: deep-research, code-review, security-review,
update-config, comfyui-creative, comfyui-worship, gmail-sender.

### Step 4 — tools namespace

Core infrastructure tools (from `~/clawd/wiki/tools/` and known CLI tools):
- `tool-skmemory-cli` — skmemory CLI (search, snapshot, ritual)
- `tool-skcapstone-mcp` — MCP server (128+ tools)
- `tool-gog-cli` — Google Workspace CLI (5 authed accounts)
- `tool-skwhisper` — subconscious context daemon
- `tool-mxbai-embed` — mxbai-embed-large on .100:11434 (primary embed model)
- `tool-skmem-pg` — Postgres+pgvector+BM25+AGE on .158:5432

### Step 5 — knowledge namespace

Seed with the architecture decisions:
- `knowledge-brain-ontology` — this spec (2026-06-09-skos-brain-architecture.md)
- `knowledge-mxbai-cutover` — mxbai embedding cutover (bge-legal superseded)
- `knowledge-pg-hybrid-search` — hybrid BM25+vector RRF search model
- `knowledge-aios-three-layers` — AIOS shell / Karpathy wiki / gbrain RAG layers

### Step 6 — Run indexes

After writing each namespace, run:
```bash
skos brain index <namespace>
```

Verify the index looks correct before moving to the next namespace.

### Step 7 — Validate

Spot-check a few nodes:
```bash
skos brain validate ~/clawd/wiki/pages/entities/agents/agent-lumina.md
skos brain validate ~/clawd/wiki/pages/entities/tools/tool-skmem-pg.md
```

Fix any validation errors before marking nodes as `reviewed`.

### Step 8 — Promote mature nodes

Nodes you're confident in: promote from `draft` → `reviewed`.
Only promote to `canon` after Chef reviews.

```python
from skos.brain.canon import promote
promote("~/clawd/wiki/pages/entities/agents/agent-lumina.md", "reviewed")
```

---

## Dual-write to skmem-pg (after nodes are written)

Once nodes are canon-reviewed, project them into the retrieval index.
Use skingest's pipeline or the skmemory CLI:

```bash
# Re-embed all entity nodes with mxbai (1100-char truncation safe)
skmemory ingest ~/clawd/wiki/pages/entities/ --table docs --model mxbai
```

The hybrid search function in skmem-pg (`hybrid_search_docs`) will then find
entity nodes by semantic query + BM25 keyword search.

---

## Quality rules

1. **One entity per file.** No multi-entity files.
2. **Kebab-case slugs.** `agent-lumina`, not `AgentLumina` or `agent_lumina`.
3. **Summary ≤ 120 chars.** It appears in index tables — keep it tight.
4. **Edges are typed.** Don't leave edge type as `relates_to` if a more specific type fits.
5. **body is human+agent-readable.** Markdown prose, wiki-links `[[entity-id]]`, code blocks.
6. **No secrets in nodes.** `state_stored_at` can point to a URI; never embed credentials.
7. **lifecycle_state = draft on creation.** Promote forward; never downgrade.

---

## When you're done

Update `~/clawd/wiki/index.md` with a section for entities (link to the namespace indexes).
Append to `~/clawd/wiki/log.md`:
```
## [2026-06-09] brain-init | skos entity-graph skeleton seeded by skos brain init
```

Commit with message: `wiki: brain init — entity graph skeleton (skos/agents/skills/tools/knowledge)`
