"""
Shared test fixtures and configuration for ACI tests.
"""

import pytest
import sys
import os
from unittest.mock import Mock

# Add the project root to the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


@pytest.fixture(scope="session")
def test_environment():
    """Set up test environment variables."""
    test_env = {
        "TESTING": "true",
        "DB_URL": "sqlite:///:memory:",
        "SMTP_SERVER": "smtp.test.com",
        "SMTP_PORT": "587",
        "SMTP_USERNAME": "test@example.com",
        "SMTP_PASSWORD": "test_password",
        "FROM_EMAIL_AGENT": "agent@example.com"
    }
    
    # Set environment variables for tests
    for key, value in test_env.items():
        os.environ[key] = value
    
    yield test_env
    
    # Cleanup
    for key in test_env.keys():
        if key in os.environ:
            del os.environ[key]


@pytest.fixture
def mock_logger():
    """Create a mock logger for testing."""
    return Mock()


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all mocks before each test."""
    yield
    # This runs after each test - you can add cleanup logic here if needed
