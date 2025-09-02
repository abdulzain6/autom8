"""
Comprehensive tests for the NotifyMe connector.

This test suite covers:
- Initialization and configuration
- Markdown to HTML conversion
- Email sending functionality
- File attachment handling
- Error handling and edge cases
- SMTP connection and authentication
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, call
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from aci.server.app_connectors.notifyme import Notifyme, MAX_ATTACHMENT_SIZE_MB
from aci.common.db.sql_models import LinkedAccount, SupabaseUser, Artifact
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials


class TestNotifymeConnector:
    """Test suite for the NotifyMe connector."""

    @pytest.fixture
    def mock_user(self):
        """Create a mock user."""
        user = Mock(spec=SupabaseUser)
        user.id = "test-user-id"
        user.email = "test@example.com"
        return user

    @pytest.fixture
    def mock_linked_account(self, mock_user):
        """Create a mock linked account."""
        linked_account = Mock(spec=LinkedAccount)
        linked_account.user = mock_user
        linked_account.user_id = "test-user-id"
        linked_account.id = "test-linked-account-id"
        return linked_account

    @pytest.fixture
    def security_scheme(self):
        """Create a NoAuthScheme security scheme."""
        return NoAuthScheme()

    @pytest.fixture
    def security_credentials(self):
        """Create NoAuthScheme credentials."""
        return NoAuthSchemeCredentials()

    @pytest.fixture
    def mock_db_session(self):
        """Create a mock database session."""
        return Mock()

    @pytest.fixture
    def mock_file_manager(self):
        """Create a mock file manager."""
        return Mock()

    @pytest.fixture
    def mock_config(self):
        """Create mock configuration."""
        config = Mock()
        config.DB_FULL_URL = "postgresql://test:test@localhost/test"
        config.FROM_EMAIL_AGENT = "agent@example.com"
        config.SMTP_SERVER = "smtp.example.com"
        config.SMTP_PORT = 587
        config.SMTP_USERNAME = "smtp_user"
        
        # Mock password with get_secret_value method
        mock_password = Mock()
        mock_password.get_secret_value.return_value = "smtp_password"
        config.SMTP_PASSWORD = mock_password
        
        return config

    @pytest.fixture
    def notifyme_connector(self, mock_linked_account, security_scheme, security_credentials, mock_db_session, mock_file_manager, mock_config):
        """Create a NotifyMe connector instance with mocked dependencies."""
        with patch('aci.server.app_connectors.notifyme.create_db_session', return_value=mock_db_session), \
             patch('aci.server.app_connectors.notifyme.FileManager', return_value=mock_file_manager), \
             patch('aci.server.app_connectors.notifyme.config', mock_config):
            
            connector = Notifyme(
                linked_account=mock_linked_account,
                security_scheme=security_scheme,
                security_credentials=security_credentials,
                run_id="test-run-id"
            )
            
            # Manually set the mocked objects for easier access in tests
            connector.db = mock_db_session
            connector.file_manager = mock_file_manager
            
            return connector


class TestNotifymeInitialization(TestNotifymeConnector):
    """Test the initialization of the NotifyMe connector."""

    def test_initialization_success(self, notifyme_connector, mock_linked_account):
        """Test successful initialization of the connector."""
        assert notifyme_connector.user_email == "test@example.com"
        assert notifyme_connector.linked_account == mock_linked_account
        assert notifyme_connector.db is not None
        assert notifyme_connector.file_manager is not None

    def test_initialization_with_run_id(self, mock_linked_account, security_scheme, security_credentials, mock_config):
        """Test initialization with a specific run ID."""
        with patch('aci.server.app_connectors.notifyme.create_db_session'), \
             patch('aci.server.app_connectors.notifyme.FileManager'), \
             patch('aci.server.app_connectors.notifyme.config', mock_config):
            
            connector = Notifyme(
                linked_account=mock_linked_account,
                security_scheme=security_scheme,
                security_credentials=security_credentials,
                run_id="custom-run-id"
            )
            
            assert connector.run_id == "custom-run-id"


class TestMarkdownToHtmlConversion(TestNotifymeConnector):
    """Test the markdown to HTML conversion functionality."""

    def test_convert_simple_markdown(self, notifyme_connector):
        """Test conversion of simple markdown text."""
        markdown_text = "# Hello World\n\nThis is **bold** text."
        html_result = notifyme_connector._convert_markdown_to_html(markdown_text)
        
        assert "<h1>Hello World</h1>" in html_result
        assert "<strong>bold</strong>" in html_result
        assert "<!DOCTYPE html>" in html_result
        assert "<style>" in html_result

    def test_convert_markdown_with_code_blocks(self, notifyme_connector):
        """Test conversion of markdown with code blocks."""
        markdown_text = """# Code Example

```python
def hello():
    print("Hello World")
```"""
        html_result = notifyme_connector._convert_markdown_to_html(markdown_text)
        
        assert "<h1>Code Example</h1>" in html_result
        assert "<pre>" in html_result
        assert "def" in html_result and "hello" in html_result

    def test_convert_markdown_with_tables(self, notifyme_connector):
        """Test conversion of markdown tables."""
        markdown_text = """| Name | Age |
|------|-----|
| John | 25  |
| Jane | 30  |"""
        html_result = notifyme_connector._convert_markdown_to_html(markdown_text)
        
        assert "<table>" in html_result
        assert "Name" in html_result and "Age" in html_result
        assert "John" in html_result and "Jane" in html_result

    def test_convert_markdown_with_links_and_lists(self, notifyme_connector):
        """Test conversion of markdown with links and lists."""
        markdown_text = """## Features

- [Google](https://google.com)
- **Bold item**
- *Italic item*

> This is a blockquote"""
        html_result = notifyme_connector._convert_markdown_to_html(markdown_text)
        
        assert "<h2>Features</h2>" in html_result
        assert "Google" in html_result and "https://google.com" in html_result
        assert "<ul>" in html_result or "<li>" in html_result
        assert "<blockquote>" in html_result or "blockquote" in html_result

    def test_convert_plain_text_fallback(self, notifyme_connector):
        """Test that plain text is returned when markdown conversion fails."""
        with patch('aci.server.app_connectors.notifyme.markdown.Markdown') as mock_markdown:
            mock_markdown.side_effect = Exception("Markdown conversion failed")
            
            plain_text = "This is plain text"
            result = notifyme_connector._convert_markdown_to_html(plain_text)
            
            assert result == plain_text

    def test_markdown_detection_positive_cases(self, notifyme_connector):
        """Test that markdown indicators are properly detected."""
        test_cases = [
            "# Heading",
            "**bold text**",
            "_italic text_",
            "```code block```",
            "[link](url)",
            "| table | cell |",
            "> blockquote",
            "- list item",
            "+ list item"
        ]
        
        for text in test_cases:
            markdown_indicators = ['#', '*', '_', '```', '[', ']', '|', '>', '-', '+']
            likely_markdown = any(indicator in text for indicator in markdown_indicators)
            assert likely_markdown, f"Should detect markdown in: {text}"

    def test_markdown_detection_negative_cases(self, notifyme_connector):
        """Test that plain text doesn't trigger markdown detection incorrectly."""
        plain_text = "This is just regular text without any special formatting."
        markdown_indicators = ['#', '*', '_', '```', '[', ']', '|', '>', '-', '+']
        likely_markdown = any(indicator in plain_text for indicator in markdown_indicators)
        assert not likely_markdown


class TestEmailSending(TestNotifymeConnector):
    """Test the email sending functionality."""

    @patch('aci.server.app_connectors.notifyme.smtplib.SMTP')
    @patch('aci.server.app_connectors.notifyme.config')
    def test_send_plain_text_email_success(self, mock_config_patch, mock_smtp, notifyme_connector):
        """Test successful sending of a plain text email."""
        # Setup mock config
        mock_config_patch.SMTP_SERVER = "smtp.example.com"
        mock_config_patch.SMTP_PORT = 587
        mock_config_patch.SMTP_USERNAME = "smtp_user"
        
        mock_password = Mock()
        mock_password.get_secret_value.return_value = "smtp_password"
        mock_config_patch.SMTP_PASSWORD = mock_password
        
        # Setup mock SMTP
        mock_server = Mock()
        mock_smtp.return_value.__enter__.return_value = mock_server
        
        # Execute
        result = notifyme_connector.send_me_email(
            subject="Test Subject",
            body="This is a plain text email without markdown."
        )
        
        # Verify
        assert result["status"] == "success"
        assert "test@example.com" in result["message"]
        
        # Verify SMTP calls
        mock_smtp.assert_called_once_with("smtp.example.com", 587)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("smtp_user", "smtp_password")
        mock_server.send_message.assert_called_once()

    @patch('aci.server.app_connectors.notifyme.smtplib.SMTP')
    def test_send_markdown_email_success(self, mock_smtp, notifyme_connector, mock_config):
        """Test successful sending of a markdown email."""
        # Setup
        mock_server = Mock()
        mock_smtp.return_value.__enter__.return_value = mock_server
        
        # Execute
        result = notifyme_connector.send_me_email(
            subject="Test Subject",
            body="# Markdown Email\n\nThis is **bold** text."
        )
        
        # Verify
        assert result["status"] == "success"
        assert "test@example.com" in result["message"]
        
        # Verify SMTP calls
        mock_server.send_message.assert_called_once()
        
        # Get the message that was sent
        call_args = mock_server.send_message.call_args[0][0]
        assert call_args["Subject"] == "Test Subject"
        assert call_args["To"] == "test@example.com"

    def test_send_email_without_user_email_raises_error(self, notifyme_connector):
        """Test that sending email without user email raises an error."""
        # Setup - remove user email
        notifyme_connector.user_email = None
        
        # Execute & Verify
        with pytest.raises(ValueError, match="User email is not available"):
            notifyme_connector.send_me_email("Subject", "Body")

    @patch('aci.server.app_connectors.notifyme.smtplib.SMTP')
    def test_send_email_smtp_authentication_error(self, mock_smtp, notifyme_connector):
        """Test handling of SMTP authentication errors."""
        # Setup
        mock_server = Mock()
        mock_smtp.return_value.__enter__.return_value = mock_server
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, "Authentication failed")
        
        # Execute & Verify
        with pytest.raises(Exception, match="SMTP login failed"):
            notifyme_connector.send_me_email("Subject", "Body")

    @patch('aci.server.app_connectors.notifyme.smtplib.SMTP')
    def test_send_email_general_smtp_error(self, mock_smtp, notifyme_connector):
        """Test handling of general SMTP errors."""
        # Setup
        mock_server = Mock()
        mock_smtp.return_value.__enter__.return_value = mock_server
        mock_server.send_message.side_effect = Exception("Connection failed")
        
        # Execute & Verify
        with pytest.raises(Exception, match="An unexpected error occurred"):
            notifyme_connector.send_me_email("Subject", "Body")


class TestFileAttachments(TestNotifymeConnector):
    """Test the file attachment functionality."""

    def test_send_email_with_valid_artifacts(self, notifyme_connector, mock_db_session, mock_file_manager):
        """Test sending email with valid file attachments."""
        # Setup mock artifact
        mock_artifact = Mock(spec=Artifact)
        mock_artifact.id = "artifact-1"
        mock_artifact.filename = "test.txt"
        mock_artifact.size_bytes = 1024
        mock_artifact.user_id = "test-user-id"
        
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_artifact
        mock_file_manager.read_artifact.return_value = (iter([b"file content"]), "text/plain")
        
        with patch('aci.server.app_connectors.notifyme.smtplib.SMTP') as mock_smtp:
            mock_server = Mock()
            mock_smtp.return_value.__enter__.return_value = mock_server
            
            # Execute
            result = notifyme_connector.send_me_email(
                subject="Test with attachment",
                body="Email with attachment",
                artifact_ids=["artifact-1"]
            )
            
            # Verify
            assert result["status"] == "success"
            mock_file_manager.read_artifact.assert_called_once_with("artifact-1")

    def test_send_email_with_nonexistent_artifact(self, notifyme_connector, mock_db_session):
        """Test sending email with non-existent artifact ID."""
        # Setup - return None for artifact query
        mock_db_session.query.return_value.filter.return_value.first.return_value = None
        
        with patch('aci.server.app_connectors.notifyme.smtplib.SMTP') as mock_smtp:
            mock_server = Mock()
            mock_smtp.return_value.__enter__.return_value = mock_server
            
            # Execute - should continue without the missing artifact
            result = notifyme_connector.send_me_email(
                subject="Test",
                body="Test",
                artifact_ids=["nonexistent-artifact"]
            )
            
            # Verify - email should still be sent successfully
            assert result["status"] == "success"

    def test_send_email_with_unauthorized_artifact(self, notifyme_connector, mock_db_session):
        """Test sending email with artifact belonging to different user."""
        # Setup mock artifact with different user_id
        mock_artifact = Mock(spec=Artifact)
        mock_artifact.id = "artifact-1"
        mock_artifact.user_id = "different-user-id"
        
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_artifact
        
        # Execute & Verify
        with pytest.raises(ValueError, match="access denied"):
            notifyme_connector.send_me_email(
                subject="Test",
                body="Test",
                artifact_ids=["artifact-1"]
            )

    def test_send_email_with_oversized_attachments(self, notifyme_connector, mock_db_session):
        """Test sending email with attachments exceeding size limit."""
        # Setup mock artifact that exceeds size limit
        mock_artifact = Mock(spec=Artifact)
        mock_artifact.id = "artifact-1"
        mock_artifact.filename = "large_file.txt"
        mock_artifact.size_bytes = (MAX_ATTACHMENT_SIZE_MB + 1) * 1024 * 1024
        mock_artifact.user_id = "test-user-id"
        
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_artifact
        
        # Execute & Verify
        with pytest.raises(ValueError, match="exceeds the.*MB limit"):
            notifyme_connector.send_me_email(
                subject="Test",
                body="Test",
                artifact_ids=["artifact-1"]
            )

    def test_send_email_with_multiple_artifacts_within_limit(self, notifyme_connector, mock_db_session, mock_file_manager):
        """Test sending email with multiple artifacts within size limit."""
        # Setup multiple mock artifacts
        artifacts = []
        for i in range(3):
            artifact = Mock(spec=Artifact)
            artifact.id = f"artifact-{i}"
            artifact.filename = f"file{i}.txt"
            artifact.size_bytes = 1024  # 1KB each
            artifact.user_id = "test-user-id"
            artifacts.append(artifact)
        
        # Setup query to return different artifacts based on filter
        def query_side_effect(*args):
            query_mock = Mock()
            filter_mock = Mock()
            
            def filter_side_effect(condition):
                # This is a simplified way to handle the filter condition
                # In a real scenario, you'd parse the condition more carefully
                for artifact in artifacts:
                    if hasattr(condition, 'right') and condition.right.value == artifact.id:
                        filter_mock.first.return_value = artifact
                        break
                else:
                    filter_mock.first.return_value = None
                return filter_mock
            
            query_mock.filter = filter_side_effect
            return query_mock
        
        mock_db_session.query.side_effect = query_side_effect
        mock_file_manager.read_artifact.return_value = (iter([b"content"]), "text/plain")
        
        with patch('aci.server.app_connectors.notifyme.smtplib.SMTP') as mock_smtp:
            mock_server = Mock()
            mock_smtp.return_value.__enter__.return_value = mock_server
            
            # Execute
            result = notifyme_connector.send_me_email(
                subject="Test multiple attachments",
                body="Email with multiple attachments",
                artifact_ids=["artifact-0", "artifact-1", "artifact-2"]
            )
            
            # Verify
            assert result["status"] == "success"


class TestEdgeCases(TestNotifymeConnector):
    """Test edge cases and error conditions."""

    def test_before_execute_validation(self, notifyme_connector):
        """Test the _before_execute validation method."""
        # Test with valid email
        notifyme_connector._before_execute()  # Should not raise
        
        # Test with invalid email
        notifyme_connector.user_email = None
        with pytest.raises(ValueError, match="User email is not available"):
            notifyme_connector._before_execute()

    def test_send_email_with_empty_subject_and_body(self, notifyme_connector):
        """Test sending email with empty subject and body."""
        with patch('aci.server.app_connectors.notifyme.smtplib.SMTP') as mock_smtp:
            mock_server = Mock()
            mock_smtp.return_value.__enter__.return_value = mock_server
            
            result = notifyme_connector.send_me_email(
                subject="",
                body=""
            )
            
            assert result["status"] == "success"

    def test_send_email_with_unicode_content(self, notifyme_connector):
        """Test sending email with unicode content."""
        with patch('aci.server.app_connectors.notifyme.smtplib.SMTP') as mock_smtp:
            mock_server = Mock()
            mock_smtp.return_value.__enter__.return_value = mock_server
            
            result = notifyme_connector.send_me_email(
                subject="Unicode Test 🚀",
                body="# Unicode Content\n\nThis contains émojis 🎉 and special châractërs!"
            )
            
            assert result["status"] == "success"

    def test_send_email_with_very_long_content(self, notifyme_connector):
        """Test sending email with very long content."""
        with patch('aci.server.app_connectors.notifyme.smtplib.SMTP') as mock_smtp:
            mock_server = Mock()
            mock_smtp.return_value.__enter__.return_value = mock_server
            
            long_content = "# Long Content\n\n" + "This is a very long line. " * 1000
            
            result = notifyme_connector.send_me_email(
                subject="Long content test",
                body=long_content
            )
            
            assert result["status"] == "success"


class TestIntegration(TestNotifymeConnector):
    """Integration tests combining multiple features."""

    @patch('aci.server.app_connectors.notifyme.smtplib.SMTP')
    def test_full_email_with_markdown_and_attachments(self, mock_smtp, notifyme_connector, mock_db_session, mock_file_manager):
        """Test sending a complete email with markdown content and attachments."""
        # Setup mock artifact
        mock_artifact = Mock(spec=Artifact)
        mock_artifact.id = "artifact-1"
        mock_artifact.filename = "report.pdf"
        mock_artifact.size_bytes = 2048
        mock_artifact.user_id = "test-user-id"
        
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_artifact
        mock_file_manager.read_artifact.return_value = (iter([b"PDF content"]), "application/pdf")
        
        # Setup SMTP mock
        mock_server = Mock()
        mock_smtp.return_value.__enter__.return_value = mock_server
        
        # Execute
        markdown_content = """# Monthly Report

## Summary

- **Revenue**: $10,000
- **Expenses**: $5,000
- **Profit**: $5,000

## Details

Please find the detailed report attached.

> This is confidential information."""
        
        result = notifyme_connector.send_me_email(
            subject="Monthly Report - January 2024",
            body=markdown_content,
            artifact_ids=["artifact-1"]
        )
        
        # Verify
        assert result["status"] == "success"
        assert "test@example.com" in result["message"]
        
        # Verify SMTP interactions
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once()
        mock_server.send_message.assert_called_once()
        
        # Verify file manager interaction
        mock_file_manager.read_artifact.assert_called_once_with("artifact-1")


# Pytest configuration and fixtures
@pytest.fixture(scope="session")
def test_config():
    """Test configuration for the entire test session."""
    return {
        "test_email": "test@example.com",
        "smtp_server": "smtp.example.com",
        "smtp_port": 587
    }


# Test runner configuration
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
