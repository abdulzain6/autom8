import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from aci.common.db.crud import fcm_tokens
from aci.common.db.sql_models import FCMToken
from aci.common.logging_setup import get_logger
from aci.server import config



logger = get_logger(__name__)


class FCMManager:
    """
    Handles the logic for sending Firebase Cloud Messaging (FCM) push notifications.
    """

    def __init__(self):
        """
        Initializes the Firebase Admin SDK.
        This should only be done once per application lifecycle.
        """
        try:
            # Prevent re-initialization if it's already been done.
            if not firebase_admin._apps:
                cred = credentials.Certificate(config.FIREBASE_SERVICE_ACCOUNT_KEY_PATH)
                firebase_admin.initialize_app(cred)
                logger.info("Firebase Admin SDK initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Firebase Admin SDK: {e}", exc_info=True)
            raise

    def _build_platform_specific_message(
        self,
        title: str,
        body: str,
        data: Optional[Dict[str, str]] = None,
        icon: Optional[str] = None,
    ) -> messaging.MulticastMessage:
        """
        Constructs a message with platform-specific configurations.
        """
        notification = messaging.Notification(title=title, body=body)

        # Common configurations for different platforms
        apns_config = messaging.APNSConfig(
            payload=messaging.APNSPayload(aps=messaging.Aps(badge=1, sound="default"))
        )
        android_config = messaging.AndroidConfig(priority="high")
        webpush_config = messaging.WebpushConfig(
            notification=messaging.WebpushNotification(icon=icon)
        )

        return messaging.MulticastMessage(
            notification=notification,
            data=data or {},
            apns=apns_config,
            android=android_config,
            webpush=webpush_config,
            tokens=[],  # Tokens will be added just before sending
        )

    def _cleanup_stale_tokens(
        self, db: Session, response: messaging.BatchResponse, tokens: List[FCMToken]
    ):
        """
        Checks the FCM response for errors and deletes stale/invalid tokens from the database.
        """
        if response.failure_count > 0:
            stale_tokens_to_delete = []
            for i, send_response in enumerate(response.responses):
                if not send_response.success:
                    error_code = send_response.exception.code
                    if error_code in (
                        "UNREGISTERED",
                        "INVALID_ARGUMENT",
                        "NOT_FOUND",
                    ):
                        stale_token = tokens[i]
                        stale_tokens_to_delete.append(stale_token)
                        logger.warning(
                            f"Identified stale FCM token {stale_token.id} for user {stale_token.user_id} "
                            f"due to error: {error_code}. Marking for deletion."
                        )

            if stale_tokens_to_delete:
                for token in stale_tokens_to_delete:
                    fcm_tokens.delete_token(db=db, token=token)
                logger.info(f"Deleted {len(stale_tokens_to_delete)} stale FCM tokens.")

    def send_notification_to_user(
        self,
        db: Session,
        user_id: str,
        title: str,
        body: str,
        icon: Optional[str] = None,
        data: Optional[Dict[str, str]] = None,
    ):
        """
        Sends a push notification to all registered devices for a specific user.
        """
        logger.info(f"Preparing to send push notification to user {user_id}.")

        # 1. Get all active tokens for the user
        user_tokens = fcm_tokens.get_tokens_for_user(db=db, user_id=user_id)
        if not user_tokens:
            logger.warning(
                f"No FCM tokens found for user {user_id}. Cannot send notification."
            )
            return

        # 2. Build the base message
        message = self._build_platform_specific_message(title, body, data, icon=icon)

        # 3. Send the notification and clean up any stale tokens
        try:
            # Firebase requires sending to a list of token strings
            token_strings = [token.token for token in user_tokens]
            message.tokens = token_strings

            response = messaging.send_each_for_multicast(message)

            logger.info(
                f"FCM response for user {user_id}: "
                f"{response.success_count} success, {response.failure_count} failure."
            )

            # 4. Clean up any invalid tokens based on the response
            self._cleanup_stale_tokens(db=db, response=response, tokens=user_tokens)

        except Exception as e:
            logger.error(
                f"Failed to send FCM notification for user {user_id}: {e}",
                exc_info=True,
            )
