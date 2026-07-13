# Sandbox image carrying the opencode (opencode.ai) CLI + git + python toolchain.
# Installed sovereign fallback; can target a local model. Confirm the npm package
# name against opencode.ai; a real `opencode run` output-shape validation is the
# remaining pre-live step (OpenCodeAdapter._parse is defensive and flagged).
FROM node:24-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates python3 python3-pip python3-venv curl \
    && rm -rf /var/lib/apt/lists/*

# harness CLI: the official installer drops a self-contained binary; copy it onto
# PATH so any uid can run it.
RUN curl -fsSL https://opencode.ai/install | bash \
    && cp /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && chmod 0755 /usr/local/bin/opencode

RUN pip3 install --no-cache-dir --break-system-packages pytest coverage \
    || pip3 install --no-cache-dir pytest coverage

RUN useradd -u 10001 -m sbx
WORKDIR /work
USER sbx
