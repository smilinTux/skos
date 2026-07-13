# Sandbox image carrying the Claude Code CLI + git + a python test toolchain.
# The confined harness runs as non-root user `sbx` in /work (the bind-mounted
# task worktree). No secrets are baked in; auth is bind-mounted read-only at run.
FROM node:24-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates python3 python3-pip python3-venv curl \
    && rm -rf /var/lib/apt/lists/*

# pinned harness CLI
RUN npm install -g @anthropic-ai/claude-code

# repo test toolchain (bookworm python3 is 3.11; adequate for running repo tests
# inside the sandbox; the confinement proof does not depend on the minor version)
RUN pip3 install --no-cache-dir --break-system-packages pytest coverage \
    || pip3 install --no-cache-dir pytest coverage

RUN useradd -u 10001 -m sbx
WORKDIR /work
USER sbx
