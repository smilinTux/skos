# Mission

skos exists to give every sovereign service one consistent shape so agents and their infrastructure can deploy anything, anywhere, and own all of it, from a laptop to a Kubernetes cluster, with zero lock-in.

It is the filesystem, packaging, and capability foundation of the SKWorld ecosystem. Every capability (skdata, skmodel, skchat, capauth, and the rest) is a port; every concrete tool (postgres, ollama, matrix, and so on) is a swappable adapter. You declare what you want; skos resolves how for your profile.

## Scope

- A data-root abstraction (`$SK_DATA_ROOT`), an `app.yaml` descriptor with compose/k8s/nomad renderers, and an OCI packaging adapter.
- A profile resolver (personal, team, enterprise) over a capability catalog (the 4 C's: cloud, comms, compute, core).
- A self-building brain (an entity-graph knowledge ontology) and integration surfaces (obsidian, claude-code, codex, n8n).

Within SKStack v2, skos is the substrate the whole stack stands on: the filesystem, descriptor, and capability/adapter resolution that every other sk* service is deployed through.

## Non-goals

- skos is not itself a capability implementation; it resolves capabilities to adapters rather than being the postgres, the model, or the chat server.
- It does not lock you to one runtime or one profile; the same model spans laptop to cluster and personal to enterprise.
- It is not an orchestrator replacement; it renders descriptors for the orchestrator you already run.
