#!/bin/bash
set -e

# Check if UV is installed
if ! command -v uv &> /dev/null; then
    echo "UV not found. Installing UV..."
    pip install uv
fi

# Build the package
echo "Building the package with UV..."
uv build

# Create a venv and install the package in development mode
echo "Creating a virtual environment and installing in development mode..."
uv venv
source .venv/bin/activate

# Install the package in development mode
uv pip install -e .

echo "Setup complete! To run the server:"
echo "1. Start the OSC daemon: ableton-osc-daemon"
echo "2. Start the MCP server: ableton-mcp-server"
echo ""
echo "To deactivate the virtual environment, run: deactivate" 