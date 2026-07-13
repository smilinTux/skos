# Sandbox image carrying the pi (pi.dev) CLI + git + python test toolchain.
# pi is the sovereign target harness; it can route to a local model via skgateway
# (auth_env), keeping all egress on-tailnet.
#
# NOTE: confirm the exact pi install command from pi.dev before building. The npm
# package name below is the assumed install; adjust if pi ships via a different
# channel (curl installer, cargo, etc.). pi is not the default harness, so this
# image build + a real `pi -p --mode json` output-shape validation are the
# remaining pre-live steps for the pi harness (its adapter code is done + unit
# tested; PiAdapter._parse is defensive and flagged for validation).
FROM node:24-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates python3 python3-pip python3-venv curl \
    && rm -rf /var/lib/apt/lists/*

# pinned harness CLI (confirmed from pi.dev: npm package @earendil-works/pi-coding-agent)
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent

RUN pip3 install --no-cache-dir --break-system-packages pytest coverage \
    || pip3 install --no-cache-dir pytest coverage

RUN useradd -u 10001 -m sbx
WORKDIR /work
USER sbx
