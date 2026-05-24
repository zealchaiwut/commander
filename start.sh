#!/bin/bash
echo "→ Launching commander tmux session..."

SESSION="commander"

# Check if already inside tmux
if [ -n "$TMUX" ]; then
  echo "✗ You're already inside tmux. Detach first (Ctrl+j d) then try again."
  exit 1
fi

# Check if session exists already
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "→ Session '$SESSION' already exists. Attaching..."
  tmux attach-session -t "$SESSION"
  exit 0
fi

# Check that layout file exists
if [ ! -f ~/.commander.tmux ]; then
  echo "✗ Layout file ~/.commander.tmux not found. Create it first."
  exit 1
fi

echo "→ Creating new session..."
tmux new-session -d -s "$SESSION"

echo "→ Loading layout..."
tmux source-file ~/.commander.tmux

echo "→ Attaching..."
tmux attach-session -t "$SESSION"
