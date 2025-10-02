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
from aci.common.db.sql_models import Artifact, LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase

try:
    from PIL import Image
except ImportError:
    Image = None

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

        # Store user's email
        self.user_email = str(self.linked_account.user.email)
        self.db = create_db_session(config.DB_FULL_URL)
        self.file_manager = FileManager(self.db)

        logger.info(f"NotifyMe connector initialized for user: {self.user_email}")

    def _convert_markdown_to_html(self, text: str, include_logo: bool = False) -> str:
        """
        Converts markdown text to HTML format for email compatibility.
        
        Args:
            text: The markdown text to convert.
            include_logo: Whether to include the logo image tag.
            
        Returns:
            HTML formatted text.
        """
        try:
            # Ensure text has proper newlines for markdown processing
            if not text.endswith('\n'):
                text += '\n'
            
            # Configure markdown with extensions for better email compatibility and line breaks
            md = markdown.Markdown(
                extensions=[
                    'markdown.extensions.tables',
                    'markdown.extensions.fenced_code',
                    'markdown.extensions.nl2br',  # Convert newlines to <br>
                    'markdown.extensions.codehilite',
                    'markdown.extensions.sane_lists'  # Better list handling
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
                output_format='html'  # Better HTML output
            )
            html_content = md.convert(text)
            
            # Logo HTML if requested
            logo_html = ""
            if include_logo:
                logo_html = '<img src="cid:logo" alt="Autom8 Logo" style="max-width: 150px; margin-bottom: 20px; display: block; margin-left: auto; margin-right: auto;">'
            
            # Enhanced dark theme CSS with improved line break and text wrapping
            email_css = """
            <style>
                body { 
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                    line-height: 1.7; /* Increased for better readability */
                    color: #FFFFFF; 
                    background-color: #121212; 
                    margin: 0; 
                    padding: 20px; 
                    word-wrap: break-word; /* Ensure long words break */
                    white-space: pre-wrap; /* Preserve whitespace and wrap */
                }
                .email-container {
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #1e1e1e;
                    border-radius: 8px;
                    padding: 20px;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
                    word-wrap: break-word;
                }
                h1, h2, h3, h4, h5, h6 { 
                    color: #00FFFF; 
                    margin-top: 1.5em; 
                    margin-bottom: 0.8em; 
                    font-weight: 600;
                    line-height: 1.3;
                }
                h1 { font-size: 28px; }
                h2 { font-size: 24px; }
                h3 { font-size: 20px; }
                h4 { font-size: 18px; }
                p { 
                    margin-bottom: 1.2em; 
                    color: #FFFFFF;
                    white-space: pre-line; /* Preserve newlines in paragraphs */
                    word-wrap: break-word;
                }
                /* Improved list styling with proper numbering for nested lists and better spacing */
                ol, ul { 
                    margin-bottom: 1.2em; 
                    padding-left: 30px; 
                    color: #FFFFFF;
                    line-height: 1.6;
                }
                ol {
                    list-style-type: decimal;
                    padding-left: 35px;
                }
                ul {
                    list-style-type: disc;
                    padding-left: 30px;
                }
                li { 
                    margin-bottom: 0.6em; 
                    color: #FFFFFF;
                    line-height: 1.6;
                    word-wrap: break-word;
                }
                li p {
                    margin: 0.3em 0; /* Ensure paragraphs in list items have spacing */
                }
                /* Nested list styling with proper indentation */
                ol ol, ol ul, ul ol, ul ul {
                    margin-top: 0.5em;
                    margin-bottom: 0.5em;
                    padding-left: 25px;
                }
                ol ol {
                    list-style-type: lower-alpha;
                    padding-left: 30px;
                }
                ol ol ol {
                    list-style-type: lower-roman;
                    padding-left: 35px;
                }
                ul ul {
                    list-style-type: circle;
                    padding-left: 30px;
                }
                blockquote { 
                    border-left: 4px solid #00FFFF; 
                    margin: 1.2em 0; 
                    padding-left: 1.2em; 
                    color: #CCCCCC; 
                    font-style: italic; 
                    background-color: #232323;
                    border-radius: 0 4px 4px 0;
                    line-height: 1.5;
                }
                code { 
                    background-color: #232323; 
                    color: #00FFFF;
                    padding: 2px 6px; 
                    border-radius: 4px; 
                    font-family: 'Courier New', monospace; 
                    font-size: 0.9em; 
                    border: 1px solid #00FFFF;
                    white-space: pre-wrap;
                    word-wrap: break-word;
                }
                pre { 
                    background-color: #232323; 
                    color: #FFFFFF;
                    padding: 15px; 
                    border-radius: 6px; 
                    border: 1px solid #00FFFF;
                    overflow-x: auto; 
                    font-family: 'Courier New', monospace; 
                    margin: 1.2em 0;
                    white-space: pre-wrap;
                    word-wrap: normal;
                    line-height: 1.4;
                }
                table { 
                    border-collapse: collapse; 
                    width: 100%; 
                    margin-bottom: 1.2em; 
                    background-color: #232323;
                    border-radius: 6px;
                    overflow: hidden;
                    word-wrap: break-word;
                }
                th, td { 
                    border: 1px solid #00FFFF; 
                    padding: 12px; 
                    text-align: left; 
                    color: #FFFFFF;
                    word-wrap: break-word;
                    vertical-align: top;
                }
                th { 
                    background-color: #1e1e1e; 
                    color: #00FFFF;
                    font-weight: bold; 
                }
                tr:nth-child(even) {
                    background-color: #232323;
                }
                tr:hover {
                    background-color: #1e1e1e;
                }
                a { 
                    color: #00FFFF; 
                    text-decoration: none; 
                    border-bottom: 1px solid transparent;
                    transition: border-bottom 0.2s;
                    word-wrap: break-word;
                }
                a:hover { 
                    text-decoration: underline; 
                    border-bottom: 1px solid #00FFFF;
                }
                hr { 
                    border: none; 
                    border-top: 1px solid #00FFFF; 
                    margin: 2em 0; 
                    opacity: 0.5;
                }
                /* Button-like styling for links that look like buttons */
                .btn {
                    display: inline-block;
                    background-color: #00FFFF;
                    color: #121212 !important;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 6px;
                    font-weight: bold;
                    margin: 10px 0;
                    transition: background-color 0.2s;
                    word-wrap: break-word;
                }
                .btn:hover {
                    background-color: #00CCCC;
                    text-decoration: none;
                }
                /* Card-like styling for sections */
                .card {
                    background-color: #232323;
                    border: 1px solid #00FFFF;
                    border-radius: 8px;
                    padding: 15px;
                    margin: 15px 0;
                    word-wrap: break-word;
                }
            </style>
            """
            
            # Wrap in a complete HTML document with dark theme container and optional logo
            full_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Email Notification</title>
                {email_css}
            </head>
            <body>
                <div class="email-container">
                    {logo_html}
                    {html_content}
                </div>
            </body>
            </html>
            """
            
            return full_html
            
        except Exception as e:
            logger.warning(f"Failed to convert markdown to HTML: {e}. Using plain text fallback.")
            # Enhanced plain text fallback with manual line breaks
            lines = text.split('\n')
            formatted_text = '<br>'.join(line.strip() for line in lines if line.strip())
            return f'<div style="white-space: pre-line; line-height: 1.6; color: #FFFFFF; background-color: #1e1e1e; padding: 20px; border-radius: 8px;">{formatted_text}</div>'

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
        
        # Check if the body contains markdown-like syntax
        markdown_indicators = ['#', '*', '_', '```', '[', ']', '|', '>', '-', '+']
        likely_markdown = any(indicator in body for indicator in markdown_indicators)
        include_logo = False
        
        if likely_markdown:
            # Convert markdown to HTML with optional logo
            html_body = self._convert_markdown_to_html(body, include_logo=include_logo)
            
            # Create both plain text and HTML versions
            text_part = MIMEText(body, "plain", "utf-8")
            html_part = MIMEText(html_body, "html", "utf-8")
            
            # Add both versions to the email
            msg.attach(text_part)
            msg.attach(html_part)
            
            logger.info("Email body converted from markdown to HTML format")
        else:
            # Enhanced plain text handling
            text_part = MIMEText(body, "plain", "utf-8")
            # For plain text, create a simple HTML wrapper
            payload_bytes = text_part.get_payload(decode=True)
            payload_text = payload_bytes.decode('utf-8', errors='ignore') if isinstance(payload_bytes, bytes) else str(payload_bytes)
            html_wrapper = f"""
            <div style="font-family: 'Segoe UI', sans-serif; line-height: 1.7; color: #FFFFFF; background-color: #1e1e1e; padding: 20px; border-radius: 8px; white-space: pre-line; word-wrap: break-word;">
                {payload_text}
            </div>
            """
            html_part = MIMEText(html_wrapper, "html", "utf-8")
            msg.attach(text_part)  # Keep original plain text
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