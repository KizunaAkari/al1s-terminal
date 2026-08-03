#!/usr/bin/env bash
set -Eeuo pipefail

mkdir -p "${AGENT_WORKDIR:-/data}" /root/.android /models

# The image owns the ADB client. The USB device and its authorization are
# supplied by the terminal host through Docker, so the agent can keep using
# the existing subprocess-based ADB adapter without a code-path fork.
adb start-server >/dev/null 2>&1 || true

exec "$@"
