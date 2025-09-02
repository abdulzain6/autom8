#!/bin/bash

# Test runner script for ACI project
# This script installs dependencies and runs tests

set -e

echo "🧪 ACI Test Runner"
echo "=================="

# Check if we're in a virtual environment
if [[ "$VIRTUAL_ENV" == "" ]]; then
    echo "⚠️  Warning: Not in a virtual environment"
    echo "   Consider activating a virtual environment first:"
    echo "   python -m venv venv && source venv/bin/activate"
    echo ""
fi

# Install test dependencies
echo "📦 Installing test dependencies..."
pip install -r aci/requirements.txt

# Run tests
echo ""
echo "🧪 Running tests..."
echo "==================="

# Change to the project root directory
cd "$(dirname "$0")"

# Run pytest with coverage
python -m pytest aci/tests/ -v --tb=short --cov=aci --cov-report=term-missing

echo ""
echo "✅ Tests completed!"
echo ""
echo "📊 Coverage report generated"
echo "   For HTML coverage report, run: python -m pytest aci/tests/ --cov=aci --cov-report=html"
