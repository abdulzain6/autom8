# ACI Tests

This directory contains comprehensive tests for the ACI (Automation & Communication Interface) project.

## Structure

```
aci/tests/
├── __init__.py
├── conftest.py                    # Shared test fixtures
├── server/
│   ├── __init__.py
│   └── app_connectors/
│       ├── __init__.py
│       └── test_notifyme.py      # NotifyMe connector tests
└── ...
```

## Running Tests

### Prerequisites

1. Install test dependencies:
   ```bash
   pip install -r aci/requirements.txt
   ```

2. Ensure you're in the project root directory

### Quick Start

Run all tests with the provided script:
```bash
./run_tests.sh
```

### Manual Testing

Run tests manually with pytest:
```bash
# Run all tests
python -m pytest aci/tests/ -v

# Run specific test file
python -m pytest aci/tests/server/app_connectors/test_notifyme.py -v

# Run with coverage
python -m pytest aci/tests/ --cov=aci --cov-report=term-missing

# Run specific test class
python -m pytest aci/tests/server/app_connectors/test_notifyme.py::TestNotifymeConnector -v

# Run specific test method
python -m pytest aci/tests/server/app_connectors/test_notifyme.py::TestNotifymeConnector::test_send_plain_text_email_success -v
```

## Test Categories

### NotifyMe Connector Tests (`test_notifyme.py`)

Comprehensive test suite for the NotifyMe email connector:

1. **Initialization Tests**
   - Connector setup and configuration
   - Parameter validation
   - Dependency injection

2. **Markdown to HTML Conversion Tests**
   - Simple markdown conversion
   - Code blocks, tables, lists
   - Links and formatting
   - Error handling and fallbacks

3. **Email Sending Tests**
   - Plain text emails
   - Markdown emails (auto-converted to HTML)
   - SMTP connection and authentication
   - Error handling (auth failures, connection issues)

4. **File Attachment Tests**
   - Valid file attachments
   - Size limit validation
   - Access control (user authorization)
   - Multiple attachments
   - Missing/invalid artifacts

5. **Edge Cases and Error Handling**
   - Empty content
   - Unicode and special characters
   - Very long content
   - Network failures

6. **Integration Tests**
   - Complete email workflow
   - Markdown + attachments
   - End-to-end functionality

## Test Fixtures

The tests use comprehensive mocking to avoid external dependencies:

- **Mock Database**: In-memory SQLite for database operations
- **Mock SMTP**: No actual emails sent during testing
- **Mock File System**: No actual files required
- **Mock Configuration**: Test-specific config values

## Coverage

The test suite aims for high coverage of the NotifyMe connector:

- **Line Coverage**: >95%
- **Branch Coverage**: >90%
- **Function Coverage**: 100%

Run coverage reports:
```bash
# Terminal report
python -m pytest aci/tests/ --cov=aci --cov-report=term-missing

# HTML report (generates htmlcov/ directory)
python -m pytest aci/tests/ --cov=aci --cov-report=html
```

## Writing New Tests

When adding new connectors or functionality:

1. Create test files following the naming convention: `test_<module_name>.py`
2. Use descriptive test class names: `TestConnectorName`
3. Group related tests in classes
4. Use clear, descriptive test method names
5. Follow the AAA pattern: Arrange, Act, Assert
6. Mock external dependencies
7. Test both success and failure scenarios
8. Include edge cases

### Example Test Structure

```python
class TestNewConnector:
    """Test suite for the New connector."""
    
    @pytest.fixture
    def connector(self):
        """Create a connector instance for testing."""
        # Setup code here
        return connector_instance
    
    def test_success_case(self, connector):
        """Test successful operation."""
        # Arrange
        # Act
        # Assert
    
    def test_error_case(self, connector):
        """Test error handling."""
        # Arrange
        # Act & Assert
        with pytest.raises(ExpectedException):
            connector.method_that_should_fail()
```

## Continuous Integration

These tests are designed to run in CI/CD environments:

- No external dependencies required
- Fast execution (all tests < 30 seconds)
- Comprehensive coverage
- Clear error reporting

## Debugging Tests

For debugging failed tests:

1. Run with verbose output: `pytest -v -s`
2. Use `--pdb` for interactive debugging
3. Add print statements or logging
4. Run specific tests in isolation
5. Check mock call counts and arguments

## Best Practices

1. **Keep tests independent**: Each test should be able to run in isolation
2. **Use descriptive names**: Test names should clearly indicate what is being tested
3. **Mock external dependencies**: Don't rely on external services, databases, or files
4. **Test edge cases**: Include tests for error conditions and boundary values
5. **Maintain test performance**: Keep tests fast to encourage frequent running
6. **Update tests with code changes**: Ensure tests stay current with implementation
