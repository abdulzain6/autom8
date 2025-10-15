import markdown 
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from typing import Optional, List
import os
import io
from aci.common.fcm import FCMManager
from aci.common.utils import create_db_session
from aci.server import config
from aci.server.file_management import FileManager
from aci.common.db.sql_models import Artifact, LinkedAccount, UserProfile
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase

try:
    from PIL import Image
except ImportError:
    Image = None

import requests

logger = get_logger(__name__)

# Define a constant for the maximum total size of attachments in megabytes.
MAX_ATTACHMENT_SIZE_MB = 10
# Maximum size for individual images before compression (in MB)
MAX_IMAGE_SIZE_MB = 2.5
# Image compression quality (1-100, lower = smaller file)
IMAGE_COMPRESSION_QUALITY = 70


class Notifyme(AppConnectorBase):
    """
    Connector for sending email notifications to the user using a centrally configured SMTP account.
    Supports automatic markdown to HTML conversion for better email formatting.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: Optional[str] = None,
    ):
        """
        Initializes the NotifyMe connector.
        """
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )

        # Initialize database session and file manager
        self.db = create_db_session(config.DB_FULL_URL)
        self.file_manager = FileManager(self.db)
        
        # Store user's email
        self.user_email = str(self.linked_account.user.email)
        
        # Get user's phone number from profile
        profile = self.db.query(UserProfile).filter(UserProfile.id == self.linked_account.user_id).first()
        self.user_phone = profile.phone_number if profile else None
        
        logger.info(f"NotifyMe connector initialized for user: {self.user_email}, phone: {self.user_phone}")

    def _convert_markdown_to_html(self, text: str, include_logo: bool = False) -> str:
        """
        Converts markdown text to HTML format for email compatibility with robust edge case handling.
        
        Args:
            text: The markdown text to convert.
            include_logo: Whether to include the logo image tag.
            
        Returns:
            HTML formatted text.
        """
        try:
            # Handle None or empty text
            if not text:
                return '<p style="color: #FFFFFF;">No content provided.</p>'
            
            # Convert to string if not already
            text = str(text).strip()
            
            if not text:
                return '<p style="color: #FFFFFF;">No content provided.</p>'
            
            # Clean up common problematic characters and sequences
            text = text.replace('\r\n', '\n').replace('\r', '\n')  # Normalize line endings
            text = text.replace('\u00a0', ' ')  # Replace non-breaking spaces
            text = text.replace('\u2018', "'").replace('\u2019', "'")  # Smart quotes
            text = text.replace('\u201c', '"').replace('\u201d', '"')  # Smart quotes
            text = text.replace('\u2013', '-').replace('\u2014', '--')  # En/em dashes
            
            # Handle multiple consecutive newlines (preserve intentional spacing)
            import re
            text = re.sub(r'\n{3,}', '\n\n', text)  # Limit to max 2 consecutive newlines
            
            # Ensure text has proper newlines for markdown processing
            if not text.endswith('\n'):
                text += '\n'
            
            # Pre-process to handle edge cases
            lines = text.split('\n')
            processed_lines = []
            
            for line in lines:
                # Handle URLs that might break markdown parsing
                line = re.sub(r'(https?://[^\s]+)', r'<\1>', line)
                # Handle email addresses
                line = re.sub(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', r'<\1>', line)
                processed_lines.append(line)
            
            text = '\n'.join(processed_lines)
            
            # Configure markdown with extensions for better email compatibility and line breaks
            md = markdown.Markdown(
                extensions=[
                    'markdown.extensions.tables',
                    'markdown.extensions.fenced_code',
                    'markdown.extensions.nl2br',  # Convert newlines to <br>
                    'markdown.extensions.codehilite',
                    'markdown.extensions.sane_lists',  # Better list handling
                    'markdown.extensions.def_list'  # Definition lists
                ],
                extension_configs={
                    'codehilite': {
                        'use_pygments': False,  # Disable syntax highlighting for better email compatibility
                        'noclasses': True
                    },
                    'nl2br': {
                        'br': True  # Ensure <br> tags are added
                    }
                },
                output_format='html',  # Better HTML output
                tab_length=4  # Consistent tab handling
            )
            
            html_content = md.convert(text)
            
            # Post-process HTML to fix common issues
            # Fix URLs and emails that were protected
            html_content = re.sub(r'&lt;(https?://[^&]+)&gt;', r'<a href="\1">\1</a>', html_content)
            html_content = re.sub(r'&lt;([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})&gt;', r'<a href="mailto:\1">\1</a>', html_content)
            
            # Ensure proper spacing around elements
            html_content = re.sub(r'</p>\s*<p>', '</p>\n<p>', html_content)
            html_content = re.sub(r'</li>\s*<li>', '</li>\n<li>', html_content)
            
            # Handle empty paragraphs (convert to line breaks)
            html_content = re.sub(r'<p>\s*</p>', '<br>', html_content)
            
            # Logo HTML if requested
            logo_html = ""
            if include_logo:
                logo_html = '<img src="cid:logo" alt="Autom8 Logo" style="max-width: 150px; margin-bottom: 20px; display: block; margin-left: auto; margin-right: auto;">'
            
            # Enhanced dark theme CSS with improved handling for all edge cases
            email_css = """
            <style>
                /* Reset and base styles */
                * {
                    box-sizing: border-box;
                }
                body { 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; 
                    line-height: 1.6; 
                    color: #FFFFFF; 
                    background-color: #121212; 
                    margin: 0; 
                    padding: 20px; 
                    word-wrap: break-word; 
                    word-break: break-word;
                    overflow-wrap: break-word;
                    -webkit-text-size-adjust: 100%;
                    -ms-text-size-adjust: 100%;
                }
                .email-container {
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #1e1e1e;
                    border-radius: 8px;
                    padding: 20px;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
                    word-wrap: break-word;
                    word-break: break-word;
                    overflow-wrap: break-word;
                }
                
                /* Typography */
                h1, h2, h3, h4, h5, h6 { 
                    color: #00FFFF; 
                    margin: 1.5em 0 0.8em 0; 
                    font-weight: 600;
                    line-height: 1.3;
                    word-wrap: break-word;
                    word-break: break-word;
                }
                h1 { font-size: 24px; margin-top: 0; }
                h2 { font-size: 20px; }
                h3 { font-size: 18px; }
                h4 { font-size: 16px; }
                h5 { font-size: 14px; }
                h6 { font-size: 13px; }
                
                p { 
                    margin: 0 0 1em 0; 
                    color: #FFFFFF;
                    word-wrap: break-word;
                    word-break: break-word;
                    overflow-wrap: break-word;
                }
                
                /* Handle empty paragraphs and spacing */
                p:empty {
                    margin: 0.5em 0;
                    line-height: 0.5em;
                }
                
                /* Improved list styling */
                ol, ul { 
                    margin: 0 0 1em 0; 
                    padding-left: 25px; 
                    color: #FFFFFF;
                    line-height: 1.5;
                }
                li { 
                    margin-bottom: 0.5em; 
                    color: #FFFFFF;
                    word-wrap: break-word;
                    word-break: break-word;
                }
                li:last-child {
                    margin-bottom: 0;
                }
                
                /* Nested lists */
                ol ol, ol ul, ul ol, ul ul {
                    margin: 0.3em 0;
                    padding-left: 20px;
                }
                
                /* Blockquotes */
                blockquote { 
                    border-left: 3px solid #00FFFF; 
                    margin: 1em 0; 
                    padding: 0.5em 0 0.5em 1em; 
                    color: #CCCCCC; 
                    font-style: italic; 
                    background-color: #232323;
                    border-radius: 0 4px 4px 0;
                    word-wrap: break-word;
                }
                
                /* Code styling */
                code { 
                    background-color: #232323; 
                    color: #00FFFF;
                    padding: 2px 4px; 
                    border-radius: 3px; 
                    font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace; 
                    font-size: 0.85em; 
                    border: 1px solid #444;
                    word-wrap: break-word;
                    word-break: break-all;
                }
                pre { 
                    background-color: #232323; 
                    color: #FFFFFF;
                    padding: 12px; 
                    border-radius: 6px; 
                    border: 1px solid #444;
                    overflow-x: auto; 
                    font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace; 
                    margin: 1em 0;
                    line-height: 1.4;
                    white-space: pre-wrap;
                    word-wrap: break-word;
                }
                pre code {
                    background: none;
                    border: none;
                    padding: 0;
                    font-size: inherit;
                }
                
                /* Table styling */
                table { 
                    border-collapse: collapse; 
                    width: 100%; 
                    margin: 1em 0; 
                    background-color: #232323;
                    border-radius: 6px;
                    overflow: hidden;
                    font-size: 14px;
                }
                th, td { 
                    border: 1px solid #444; 
                    padding: 8px 12px; 
                    text-align: left; 
                    color: #FFFFFF;
                    word-wrap: break-word;
                    word-break: break-word;
                    vertical-align: top;
                }
                th { 
                    background-color: #1e1e1e; 
                    color: #00FFFF;
                    font-weight: bold; 
                }
                
                /* Links */
                a { 
                    color: #00FFFF; 
                    text-decoration: underline; 
                    word-wrap: break-word;
                    word-break: break-all;
                    overflow-wrap: break-word;
                }
                a:hover { 
                    color: #00CCCC;
                }
                
                /* Horizontal rules */
                hr { 
                    border: none; 
                    border-top: 1px solid #444; 
                    margin: 1.5em 0; 
                    opacity: 0.7;
                }
                
                /* Images */
                img {
                    max-width: 100%;
                    height: auto;
                    border-radius: 4px;
                }
                
                /* Line breaks */
                br {
                    line-height: 1.5;
                }
                
                /* Handle long URLs and text */
                .long-url {
                    word-break: break-all;
                    overflow-wrap: break-word;
                }
                
                /* Mobile responsiveness */
                @media only screen and (max-width: 480px) {
                    body {
                        padding: 10px;
                    }
                    .email-container {
                        padding: 15px;
                    }
                    h1 { font-size: 20px; }
                    h2 { font-size: 18px; }
                    h3 { font-size: 16px; }
                    table {
                        font-size: 12px;
                    }
                    th, td {
                        padding: 6px 8px;
                    }
                }
                
                /* Fix for specific email clients */
                .outlookfix {
                    width: 100%;
                }
                
                /* Ensure proper spacing */
                .content-spacing > *:first-child {
                    margin-top: 0 !important;
                }
                .content-spacing > *:last-child {
                    margin-bottom: 0 !important;
                }
            </style>
            """
            
            # Clean up HTML content and add safety wrappers
            html_content = html_content.strip()
            if not html_content:
                html_content = '<p style="color: #FFFFFF;">No content to display.</p>'
            
            # Wrap in a complete HTML document with dark theme container and optional logo
            full_html = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <meta http-equiv="X-UA-Compatible" content="IE=edge">
                <title>Email Notification</title>
                {email_css}
            </head>
            <body>
                <!--[if mso]>
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                <tr>
                <td>
                <![endif]-->
                <div class="email-container">
                    {logo_html}
                    <div class="content-spacing">
                        {html_content}
                    </div>
                </div>
                <!--[if mso]>
                </td>
                </tr>
                </table>
                <![endif]-->
            </body>
            </html>
            """
            
            return full_html
            
        except Exception as e:
            logger.error(f"Failed to convert markdown to HTML: {e}")
            # Robust fallback for any conversion errors
            safe_text = str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
            lines = safe_text.split('\n')
            formatted_lines = []
            
            for line in lines:
                line = line.strip()
                if line:
                    formatted_lines.append(f'<p style="color: #FFFFFF; margin: 0 0 0.5em 0; word-wrap: break-word;">{line}</p>')
                else:
                    formatted_lines.append('<br>')
            
            formatted_content = ''.join(formatted_lines)
            
            return f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Email Notification</title>
                <style>
                    body {{ 
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; 
                        line-height: 1.6; 
                        color: #FFFFFF; 
                        background-color: #121212; 
                        margin: 0; 
                        padding: 20px;
                        word-wrap: break-word;
                    }}
                    .container {{ 
                        max-width: 600px; 
                        margin: 0 auto; 
                        background-color: #1e1e1e; 
                        padding: 20px; 
                        border-radius: 8px;
                        word-wrap: break-word;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    {formatted_content}
                </div>
            </body>
            </html>
            """

    def _before_execute(self) -> None:
        """
        A hook for pre-execution logic.
        """
        if not self.user_email:
            raise ValueError("User email is not available in the linked account.")

    def _compress_image(self, image_bytes: bytes, filename: str) -> tuple[bytes, str]:
        """
        Compress an image to reduce file size for email compatibility.
        
        Args:
            image_bytes: Raw image bytes
            filename: Original filename
            
        Returns:
            Tuple of (compressed_bytes, new_filename)
        """
        if Image is None:
            logger.warning("PIL not available, cannot compress image")
            return image_bytes, filename
        
        try:
            # Open image from bytes
            image = Image.open(io.BytesIO(image_bytes))
            
            # Convert to RGB if necessary (for JPEG compatibility)
            if image.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', image.size, (255, 255, 255))
                if image.mode == 'P':
                    image = image.convert('RGBA')
                background.paste(image, mask=image.split()[-1] if image.mode == 'RGBA' else None)
                image = background
            
            # Calculate target size (aim for ~1MB max)
            original_size = len(image_bytes)
            target_size = min(1024 * 1024, original_size)  # 1MB max
            
            # If already small enough, don't compress
            if original_size <= target_size:
                return image_bytes, filename
            
            # Try different quality levels to get under target size
            for quality in [IMAGE_COMPRESSION_QUALITY, 50, 30, 20]:
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=quality, optimize=True)
                compressed_bytes = output.getvalue()
                
                if len(compressed_bytes) <= target_size or quality == 20:
                    # Update filename to reflect compression
                    base_name = os.path.splitext(filename)[0]
                    compressed_filename = f"{base_name}_compressed.jpg"
                    
                    compression_ratio = len(compressed_bytes) / original_size
                    logger.info(f"Compressed image {filename}: {original_size:,} → {len(compressed_bytes):,} bytes ({compression_ratio:.1%}, quality={quality})")
                    
                    return compressed_bytes, compressed_filename
                    
        except Exception as e:
            logger.warning(f"Failed to compress image {filename}: {e}")
            return image_bytes, filename
        
        return image_bytes, filename

    def _clean_text_for_email(self, text: str) -> str:
        """
        Clean and normalize text for email compatibility.
        
        Args:
            text: The text to clean
            
        Returns:
            Cleaned text
        """
        if not text:
            return ""
        
        # Convert to string and normalize
        text = str(text)
        
        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # Replace problematic Unicode characters
        text = text.replace('\u00a0', ' ')  # Non-breaking space
        text = text.replace('\u2018', "'").replace('\u2019', "'")  # Smart quotes
        text = text.replace('\u201c', '"').replace('\u201d', '"')  # Smart quotes
        text = text.replace('\u2013', '-').replace('\u2014', '--')  # En/em dashes
        text = text.replace('\u2026', '...')  # Ellipsis
        
        # Clean up excessive whitespace
        import re
        text = re.sub(r'\n{3,}', '\n\n', text)  # Limit consecutive newlines
        text = re.sub(r'[ \t]+', ' ', text)  # Normalize spaces and tabs
        
        return text.strip()

    def _create_html_wrapper(self, text: str) -> str:
        """
        Create an HTML wrapper for plain text with proper styling.
        
        Args:
            text: The plain text to wrap
            
        Returns:
            HTML wrapped text
        """
        if not text:
            return '<p style="color: #FFFFFF;">No content provided.</p>'
        
        # Escape HTML characters for safety
        import html
        safe_text = html.escape(text)
        
        # Convert newlines to proper HTML
        lines = safe_text.split('\n')
        formatted_lines = []
        
        for line in lines:
            line = line.strip()
            if line:
                formatted_lines.append(f'<p style="color: #FFFFFF; margin: 0 0 0.8em 0; word-wrap: break-word; word-break: break-word;">{line}</p>')
            else:
                formatted_lines.append('<br>')
        
        formatted_content = ''.join(formatted_lines)
        
        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <meta http-equiv="X-UA-Compatible" content="IE=edge">
            <title>Email Notification</title>
            <style>
                body {{ 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                    line-height: 1.6; 
                    color: #FFFFFF; 
                    background-color: #121212; 
                    margin: 0; 
                    padding: 20px;
                    word-wrap: break-word;
                    word-break: break-word;
                    -webkit-text-size-adjust: 100%;
                    -ms-text-size-adjust: 100%;
                }}
                .container {{ 
                    max-width: 600px; 
                    margin: 0 auto; 
                    background-color: #1e1e1e; 
                    padding: 20px; 
                    border-radius: 8px;
                    word-wrap: break-word;
                    word-break: break-word;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
                }}
                p {{
                    word-wrap: break-word;
                    word-break: break-word;
                    overflow-wrap: break-word;
                }}
                a {{
                    color: #00FFFF;
                    word-wrap: break-word;
                    word-break: break-all;
                }}
                @media only screen and (max-width: 480px) {{
                    body {{ padding: 10px; }}
                    .container {{ padding: 15px; }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                {formatted_content}
            </div>
        </body>
        </html>
        """

    def send_me_email(
        self, subject: str, body: str, artifact_ids: Optional[List[str]] = None
    ) -> dict:
        """
        Sends an email from the system's configured SMTP account to the user's email address,
        optionally including artifacts as attachments. Automatically converts markdown content to HTML.

        Args:
            subject: The subject line of the email.
            body: The main content of the email (supports both plain text and markdown).
            artifact_ids: An optional list of artifact IDs to attach to the email.

        Returns:
            A dictionary with a success message.
        """
        self._before_execute()
        logger.info(
            f"Preparing to send email with subject '{subject}' to {self.user_email}"
        )

        msg = MIMEMultipart("alternative")
        # Set sender name for better display in email clients
        sender_name = "Autom8"
        msg["From"] = f"{sender_name} <{config.FROM_EMAIL_AGENT}>"
        msg["To"] = self.user_email
        msg["Subject"] = subject
        
        # Check if the body contains markdown-like syntax or special characters
        markdown_indicators = ['#', '*', '_', '```', '[', ']', '|', '>', '-', '+', '**', '__', '~~']
        likely_markdown = any(indicator in body for indicator in markdown_indicators)
        
        # Also check for multiple newlines which might need better formatting
        has_formatting_needs = '\n\n' in body or len(body.split('\n')) > 3
        
        include_logo = False
        
        if likely_markdown or has_formatting_needs:
            try:
                # Convert markdown to HTML with optional logo
                html_body = self._convert_markdown_to_html(body, include_logo=include_logo)
                
                # Create both plain text and HTML versions
                # Clean the plain text version
                clean_text = body.replace('\r\n', '\n').replace('\r', '\n')
                text_part = MIMEText(clean_text, "plain", "utf-8")
                html_part = MIMEText(html_body, "html", "utf-8")
                
                # Add both versions to the email
                msg.attach(text_part)
                msg.attach(html_part)
                
                logger.info("Email body converted from markdown to HTML format")
            except Exception as e:
                logger.warning(f"Failed to process markdown, falling back to enhanced plain text: {e}")
                # Enhanced fallback processing
                clean_text = self._clean_text_for_email(body)
                text_part = MIMEText(clean_text, "plain", "utf-8")
                html_wrapper = self._create_html_wrapper(clean_text)
                html_part = MIMEText(html_wrapper, "html", "utf-8")
                msg.attach(text_part)
                msg.attach(html_part)
        else:
            # Enhanced plain text handling
            clean_text = self._clean_text_for_email(body)
            text_part = MIMEText(clean_text, "plain", "utf-8")
            html_wrapper = self._create_html_wrapper(clean_text)
            html_part = MIMEText(html_wrapper, "html", "utf-8")
            msg.attach(text_part)
            msg.attach(html_part)
            
            logger.info("Email body enhanced as HTML from plain text")

        if artifact_ids:
            total_size = 0
            max_size_bytes = MAX_ATTACHMENT_SIZE_MB * 1024 * 1024

            for artifact_id in artifact_ids:
                file_record = (
                    self.db.query(Artifact).filter(Artifact.id == artifact_id).first()
                )
                if not file_record:
                    continue
                
                logger.info(f"Processing artifact ID: {artifact_id}")
                logger.debug(f"Retrieved file record: {file_record.filename if file_record else 'None'}")

                if (
                    not file_record
                    or file_record.user_id != self.linked_account.user_id
                ):
                    raise ValueError(
                        f"Artifact with ID {artifact_id} not found or access denied."
                    )

                # Get file content first to calculate actual size after compression  
                content_generator, _ = self.file_manager.read_artifact(artifact_id)
                file_content = b"".join(content_generator)
                
                # Check if this is an image file and compress it if needed
                filename = file_record.filename
                file_extension = os.path.splitext(filename)[1].lower()
                is_image = file_extension in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp']
                
                if is_image:
                    # Compress the image to reduce size
                    file_content, filename = self._compress_image(file_content, filename)

                # Use actual size after compression for size check
                actual_size = len(file_content)
                total_size += actual_size
                if total_size > max_size_bytes:
                    raise ValueError(
                        f"Total attachment size exceeds the {MAX_ATTACHMENT_SIZE_MB}MB limit."
                    )

                part = MIMEApplication(file_content, Name=filename)
                part["Content-Disposition"] = (
                    f'attachment; filename="{filename}"'
                )
                msg.attach(part)
                logger.info(
                    f"Attached artifact '{filename}' ({len(file_content)} bytes) to email."
                )

        try:
            with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
                server.starttls()
                server.login(
                    config.SMTP_USERNAME, config.SMTP_PASSWORD.get_secret_value()
                )
                server.send_message(msg)

            logger.info(f"Successfully sent email to {self.user_email}")
            return {"status": "success", "message": f"Email sent to {self.user_email}."}

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP Authentication failed for {config.SMTP_USERNAME}: {e}")
            raise Exception(
                "SMTP login failed. Please check the server configuration."
            ) from e
        except Exception as e:
            logger.error(f"Failed to send email to {self.user_email}: {e}")
            raise Exception(
                f"An unexpected error occurred while sending the email: {e}"
            ) from e

    def send_me_mobile_notification(
        self, title: str, body: str
    ) -> dict:
        """
        Sends a simple mobile notification to the user's mobile device.

        Args:
            title: The title of the notification.
            body: The main content of the notification.

        Returns:
            A dictionary with a success message.
        """
        logger.info(f"Preparing to send mobile notification to {self.user_email}")
        fcm_manager = FCMManager()

        try:
            fcm_manager.send_notification_to_user(
                db=self.db,
                user_id=self.linked_account.user_id,
                title=title,
                body=body
            )
            return {"status": "success", "message": "Mobile notification sent."}
        except Exception as e:
            logger.error(f"Failed to send mobile notification: {e}")
            raise Exception(
                f"An unexpected error occurred while sending the mobile notification: {e}"
            ) from e

    def send_me_whatsapp_notification(
        self, body: str
    ) -> dict:
        """
        Sends a WhatsApp message to the user's phone number.

        Args:
            body: The main content of the message.

        Returns:
            A dictionary with the status and message ID.

        Raises:
            ValueError: If the user has no phone number.
            Exception: If the API call fails.
        """
        if not self.user_phone:
            raise ValueError("User phone number is required for WhatsApp notifications")

        logger.info(f"Sending WhatsApp notification to {self.user_phone}")

        # Remove + from phone number for WhatsApp API
        phone = self.user_phone.lstrip('+')

        url = f"https://graph.facebook.com/v22.0/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {config.WHATSAPP_API_TOKEN}",
            "Content-Type": "application/json"
        }
        data = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": {
                "name": "autom8_alert",
                "language": {"code": "en"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": body}
                        ]
                    }
                ]
            }
        }

        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
            result = response.json()
            message_id = result.get("messages", [{}])[0].get("id")
            return {"status": "success", "message_id": message_id}
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send WhatsApp message: {e}")
            raise Exception(f"Failed to send WhatsApp message: {e}") from e