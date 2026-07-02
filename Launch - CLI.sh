#!/bin/bash
echo "Starting Obscura CLI Manager..."

PYTHON_CMD="python3"

if [ -f "./.venv/bin/python3" ]; then
    PYTHON_CMD="./.venv/bin/python3"
elif [ -f "./env/bin/python3" ]; then
    PYTHON_CMD="./env/bin/python3"
elif [ -f "../../../env/bin/python3" ]; then
    PYTHON_CMD="../../../env/bin/python3"
elif [ -f "../../../venv/bin/python3" ]; then
    PYTHON_CMD="../../../venv/bin/python3"
elif [ -f "../../../.venv/bin/python3" ]; then
    PYTHON_CMD="../../../.venv/bin/python3"
fi

echo "使用的 Python 解释器: $PYTHON_CMD"
$PYTHON_CMD -m obscura_manager.cli
