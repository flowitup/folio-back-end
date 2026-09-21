#!/usr/bin/env bash
# Entrypoint for the ai-browser image (Dockerfile.browser).
#
# Default mode: runs the fetch_invoice poll loop under a virtual display (xvfb-run
# starts and tears down its own Xvfb per invocation — that is all the poll loop needs,
# since it never needs a human to see the screen).
#
# "login-session" mode: started by hand, once per merchant, by the owner
# (docs/assistant-merchant-login.md) — starts a long-lived Xvfb :99, x11vnc bound to it
# (so the owner can VNC in over an SSH tunnel) and Chrome on the persistent profile
# directory, so the owner can log into each merchant site once. The poll loop is never
# started in this mode.
set -euo pipefail

MODE="${1:-default}"

if [[ "$MODE" == "login-session" ]]; then
    echo "browser-entrypoint: login-session — Xvfb :99 + x11vnc + Chrome on the persistent profile"
    export DISPLAY=:99

    Xvfb :99 -screen 0 1280x800x24 &
    xvfb_pid=$!
    # Give Xvfb a moment to bind its socket before Chrome/x11vnc try to attach to it.
    sleep 1

    x11vnc -display :99 -nopw -listen 0.0.0.0 -forever &
    vnc_pid=$!

    # --no-sandbox: Chrome's own sandbox tries to create a user/PID namespace
    # (unshare(2)), which a plain Docker container's default seccomp/capability set
    # refuses ("Operation not permitted") — same reasoning as agent.py's
    # chromium_sandbox=False for the automated poll-loop mode.
    google-chrome-stable \
        --user-data-dir="${BROWSER_PROFILE_DIR:-/app/profile}" \
        --no-sandbox \
        --no-first-run \
        --no-default-browser-check \
        --start-maximized &
    chrome_pid=$!

    cleanup() {
        kill "$chrome_pid" "$vnc_pid" "$xvfb_pid" 2>/dev/null || true
    }
    trap cleanup TERM INT

    wait "$chrome_pid"
    cleanup
    exit 0
fi

echo "browser-entrypoint: starting the fetch_invoice poll loop"
exec xvfb-run -a python -m app.infrastructure.browser_worker
