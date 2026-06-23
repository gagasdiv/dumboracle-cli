#!/usr/bin/env bash
# Create a virtual environment and install dependencies (Linux / macOS).
#   $ ./setup.sh
set -euo pipefail

PYTHON="${PYTHON:-python3}"

echo "Creating virtual environment in .venv ..."
"$PYTHON" -m venv .venv

VENV_PYTHON=".venv/bin/python"
echo "Upgrading pip ..."
"$VENV_PYTHON" -m pip install --upgrade pip --quiet

echo "Installing requirements ..."
"$VENV_PYTHON" -m pip install -r requirements.txt

if [ ! -f connections.yaml ]; then
    cp connections.example.yaml connections.yaml
    echo "Created connections.yaml from the example - edit it with your databases."
fi

echo
echo "Done. Activate with:   source .venv/bin/activate"
echo "Then run:              python -m dumboracle"
