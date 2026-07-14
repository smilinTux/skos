# Sandbox image carrying the pi (pi.dev) CLI + git + python test toolchain.
# pi is the sovereign target harness: it routes to a local model via skgateway (the
# skgw provider is injected as /agent/models.json + PI_CODING_AGENT_DIR=/agent by
# PiAdapter), so all egress stays on-tailnet through the sandbox allowlist proxy.
# Verified live end-to-end in the confined sandbox against skgateway/ornith-tiny.
FROM node:24-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates python3 python3-pip python3-venv curl \
    && rm -rf /var/lib/apt/lists/*

# pinned harness CLI (pi.dev). --ignore-scripts: pi's postinstall is not needed and
# would pull extra host-specific artifacts; the CLI runs fine without it.
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent

RUN pip3 install --no-cache-dir --break-system-packages pytest coverage \
    || pip3 install --no-cache-dir pytest coverage

RUN useradd -u 10001 -m sbx
WORKDIR /work
USER sbx
