#!/bin/bash
# QA Coverage Agent — one-time setup
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing Python dependencies..."
pip3 install -r "$SCRIPT_DIR/requirements.txt"

echo ""
echo "Add the following aliases to your ~/.zshrc (or ~/.bashrc):"
echo ""
echo "  alias coverage=\"python3 $SCRIPT_DIR/coverage_check.py\""
echo "  alias qacov=\"python3 $SCRIPT_DIR/qa_coverage.py\""
echo "  alias epicov=\"python3 $SCRIPT_DIR/epic_coverage.py\""
echo ""
echo "Also make sure these environment variables are set in your shell profile:"
echo "  export YOUTRACK_TOKEN=<your YouTrack permanent token>"
echo "  export GITHUB_TOKEN=<your GitHub personal access token>"
echo ""
echo "Setup complete!"
