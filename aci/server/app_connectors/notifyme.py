import markdown 
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from typing import Optional, List
from aci.common.fcm import FCMManager
from aci.common.utils import create_db_session
from aci.server import config
from aci.server.file_management import FileManager
from aci.common.db.sql_models import Artifact, LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase

logger = get_logger(__name__)

# Define a constant for the maximum total size of attachments in megabytes.
MAX_ATTACHMENT_SIZE_MB = 10


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

    def _convert_markdown_to_html(self, text: str) -> str:
        """
        Converts markdown text to HTML format for email compatibility.
        
        Args:
            text: The markdown text to convert.
            
        Returns:
            HTML formatted text.
        """
        try:
            # Configure markdown with extensions for better email compatibility
            md = markdown.Markdown(
                extensions=[
                    'markdown.extensions.tables',
                    'markdown.extensions.fenced_code',
                    'markdown.extensions.nl2br',
                    'markdown.extensions.codehilite',
                    'markdown.extensions.sane_lists'  # Better list handling
                ],
                extension_configs={
                    'codehilite': {
                        'use_pygments': False,  # Disable syntax highlighting for better email compatibility
                        'noclasses': True
                    }
                }
            )
            html_content = md.convert(text)
            
            # Dark theme CSS with cyan accents for modern email appearance
            email_css = """
            <style>
                body { 
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                    line-height: 1.6; 
                    color: #FFFFFF; 
                    background-color: #121212; 
                    margin: 0; 
                    padding: 20px; 
                }
                .email-container {
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #1e1e1e;
                    border-radius: 8px;
                    padding: 20px;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
                }
                h1, h2, h3, h4, h5, h6 { 
                    color: #00FFFF; 
                    margin-top: 1.5em; 
                    margin-bottom: 0.5em; 
                    font-weight: 600;
                }
                h1 { font-size: 28px; }
                h2 { font-size: 24px; }
                h3 { font-size: 20px; }
                h4 { font-size: 18px; }
                p { 
                    margin-bottom: 1em; 
                    color: #FFFFFF;
                }
                /* Improved list styling with proper numbering for nested lists */
                ol, ul { 
                    margin-bottom: 1em; 
                    padding-left: 25px; 
                    color: #FFFFFF;
                }
                ol {
                    list-style-type: decimal;
                    padding-left: 30px;
                }
                ul {
                    list-style-type: disc;
                    padding-left: 25px;
                }
                li { 
                    margin-bottom: 0.5em; 
                    color: #FFFFFF;
                    line-height: 1.5;
                }
                /* Nested list styling */
                ol ol, ol ul, ul ol, ul ul {
                    margin-top: 0.5em;
                    margin-bottom: 0.5em;
                    padding-left: 20px;
                }
                ol ol {
                    list-style-type: lower-alpha;
                }
                ol ol ol {
                    list-style-type: lower-roman;
                }
                ul ul {
                    list-style-type: circle;
                }
                blockquote { 
                    border-left: 4px solid #00FFFF; 
                    margin: 1em 0; 
                    padding-left: 1em; 
                    color: #CCCCCC; 
                    font-style: italic; 
                    background-color: #232323;
                    border-radius: 0 4px 4px 0;
                }
                code { 
                    background-color: #232323; 
                    color: #00FFFF;
                    padding: 2px 6px; 
                    border-radius: 4px; 
                    font-family: 'Courier New', monospace; 
                    font-size: 0.9em; 
                    border: 1px solid #00FFFF;
                }
                pre { 
                    background-color: #232323; 
                    color: #FFFFFF;
                    padding: 15px; 
                    border-radius: 6px; 
                    border: 1px solid #00FFFF;
                    overflow-x: auto; 
                    font-family: 'Courier New', monospace; 
                    margin: 1em 0;
                }
                table { 
                    border-collapse: collapse; 
                    width: 100%; 
                    margin-bottom: 1em; 
                    background-color: #232323;
                    border-radius: 6px;
                    overflow: hidden;
                }
                th, td { 
                    border: 1px solid #00FFFF; 
                    padding: 12px; 
                    text-align: left; 
                    color: #FFFFFF;
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
                }
            </style>
            """
            
            # Wrap in a complete HTML document with dark theme container
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
                    {html_content}
                </div>
            </body>
            </html>
            """
            
            return full_html
            
        except Exception as e:
            logger.warning(f"Failed to convert markdown to HTML: {e}. Using plain text fallback.")
            # Fallback to plain text if markdown conversion fails
            return text

    def _before_execute(self) -> None:
        """
        A hook for pre-execution logic.
        """
        if not self.user_email:
            raise ValueError("User email is not available in the linked account.")

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
        msg["From"] = config.FROM_EMAIL_AGENT
        msg["To"] = self.user_email
        msg["Subject"] = subject
        
        # Check if the body contains markdown-like syntax
        markdown_indicators = ['#', '*', '_', '```', '[', ']', '|', '>', '-', '+']
        likely_markdown = any(indicator in body for indicator in markdown_indicators)
        
        if likely_markdown:
            # Convert markdown to HTML
            html_body = self._convert_markdown_to_html(body)
            
            # Create both plain text and HTML versions
            text_part = MIMEText(body, "plain", "utf-8")
            html_part = MIMEText(html_body, "html", "utf-8")
            
            # Add both versions to the email
            msg.attach(text_part)
            msg.attach(html_part)
            
            logger.info("Email body converted from markdown to HTML format")
        else:
            # Use plain text only
            text_part = MIMEText(body, "plain", "utf-8")
            msg.attach(text_part)
            
            logger.info("Email body sent as plain text")

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

                total_size += file_record.size_bytes
                if total_size > max_size_bytes:
                    raise ValueError(
                        f"Total attachment size exceeds the {MAX_ATTACHMENT_SIZE_MB}MB limit."
                    )

                content_generator, _ = self.file_manager.read_artifact(artifact_id)
                file_content = b"".join(content_generator)

                part = MIMEApplication(file_content, Name=file_record.filename)
                part["Content-Disposition"] = (
                    f'attachment; filename="{file_record.filename}"'
                )
                msg.attach(part)
                logger.info(
                    f"Attached artifact '{file_record.filename}' ({file_record.size_bytes} bytes) to email."
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