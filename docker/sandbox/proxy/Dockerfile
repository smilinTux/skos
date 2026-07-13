# Sovereign egress allowlist proxy sidecar for the autopilot sandbox.
# Runs `python -m skos.autopilot.sandbox_proxy <port> <hosts...>` (invoked by
# Sandbox.spawn). Only the single stdlib-only proxy module is present; a minimal
# package tree makes the -m import resolve without pulling the skos deps.
FROM python:3.12-slim

RUN mkdir -p /app/skos/autopilot \
    && touch /app/skos/__init__.py /app/skos/autopilot/__init__.py
COPY src/skos/autopilot/sandbox_proxy.py /app/skos/autopilot/sandbox_proxy.py
ENV PYTHONPATH=/app
WORKDIR /app
# Command is supplied by Sandbox.spawn:
#   python -m skos.autopilot.sandbox_proxy <port> <allow-host> <allow-host> ...
