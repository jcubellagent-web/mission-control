#!/usr/bin/env bash
# bf_step.sh — bf_push with guaranteed 3s minimum display time
# Usage: bf_step.sh "objective" "steps" "state"
# Use this instead of bf_push.sh for any step that should be visible to Josh.
# Blocks for 3s so the brain feed card actually shows each state before advancing.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/bf_push.sh" "$@"
sleep 3
