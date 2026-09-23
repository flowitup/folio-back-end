#!/usr/bin/env bash
# Entrypoint for the ai-browser image (Dockerfile.browser).
#
# Default mode: runs the fetch_invoice poll loop under a virtual display. Xvfb is
# started here directly (not via xvfb-run) and Python is exec'd as tini's direct child,
# so SIGTERM reaches the poll loop immediately — see the comment further down.
#
# "login-session" mode: started by hand, once per merchant, by the owner
# (docs/assistant-merchant-login.md) — starts a long-lived Xvfb :99, x11vnc bound to it
# (so the owner can VNC in over an SSH tunnel) and Chrome on the persistent profile
# directory, so the owner can log into each merchant site once. The poll loop is never
# started in this mode.
set -euo pipefail

MODE="${1:-default}"

# A hard-killed container (SIGKILL past the stop grace period, host reboot) leaves
# /tmp/.X99-lock and the /tmp/.X11-unix/X99 socket behind in the writable layer. On
# restart, Xvfb can land on the same PID as the previous run; the X server treats a
# lock as stale only via `kill(pid, 0)` succeeding, so it kills itself and exits
# silently. Clearing both paths before every Xvfb start removes that stale state.
clear_stale_x_lock() {
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
}

# Block, bounded to ~10s, until Xvfb either binds its socket or dies. `set -e` never
# sees a failed background job, so without this check a dead Xvfb goes unnoticed and
# every later job fails against an empty DISPLAY.
wait_for_xvfb() {
    local xvfb_pid="$1"
    local socket="/tmp/.X11-unix/X99"
    local waited_ms=0
    local timeout_ms=10000
    while [[ ! -S "$socket" ]]; do
        if ! kill -0 "$xvfb_pid" 2>/dev/null; then
            echo "browser-entrypoint: Xvfb (pid $xvfb_pid) exited before binding $socket" >&2
            return 1
        fi
        if (( waited_ms >= timeout_ms )); then
            echo "browser-entrypoint: timed out after ${timeout_ms}ms waiting for $socket" >&2
            return 1
        fi
        sleep 0.1
        waited_ms=$((waited_ms + 100))
    done
    if ! kill -0 "$xvfb_pid" 2>/dev/null; then
        echo "browser-entrypoint: Xvfb (pid $xvfb_pid) died right after binding $socket" >&2
        return 1
    fi
}

if [[ "$MODE" == "login-session" ]]; then
    echo "browser-entrypoint: login-session — Xvfb :99 + x11vnc + Chrome on the persistent profile"
    export DISPLAY=:99

    clear_stale_x_lock
    Xvfb :99 -screen 0 1280x800x24 &
    xvfb_pid=$!
    wait_for_xvfb "$xvfb_pid" || exit 1

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

# `xvfb-run -a python ...` made Python a *grandchild* of tini (the direct child was
# xvfb-run's own wrapper shell, which traps only EXIT/USR1, never TERM/INT — Debian's
# xvfb-run). A `docker stop` (SIGTERM to tini, forwarded only to its direct child) never
# reached Python, so the in-flight job was always hard-killed by SIGKILL once the stop
# grace period elapsed, instead of draining gracefully through __main__.py's own SIGTERM
# handler. Starting Xvfb here and `exec`ing Python directly makes Python tini's direct
# child, so SIGTERM reaches it immediately.
export DISPLAY=:99
clear_stale_x_lock
Xvfb :99 -screen 0 1280x800x24 &
xvfb_pid=$!
wait_for_xvfb "$xvfb_pid" || exit 1

# Chrome's Singleton* lock files point at the previous container's hostname — a
# hard-killed container can leave them behind, and Chrome then refuses to open the
# profile directory ("in use on another computer"), silently failing every job. This
# container is the only writer of its own profile volume, so it is always safe to clear
# stale locks at startup.
profile_dir="${BROWSER_PROFILE_DIR:-/app/profile}"
rm -f "$profile_dir"/Singleton{Lock,Socket,Cookie}

exec python -m app.infrastructure.browser_worker
