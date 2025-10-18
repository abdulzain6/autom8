#!/usr/bin/env python3
"""
Test script for WhatsApp file sending functionality.
This script sends an existing file via WhatsApp using the existing Notifyme connector.
"""

import os
import sys
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from aci.common.db.sql_models import Artifact, LinkedAccount, UserProfile, App
from aci.server.app_connectors.notifyme import Notifyme
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.common.logging_setup import get_logger
from aci.server import config
from aci.common.utils import create_db_session

logger = get_logger(__name__)

def main():
    """Main test function."""
    try:
        logger.info("Starting WhatsApp file test...")

        # Check if WhatsApp config is available
        try:
            whatsapp_token = config.WHATSAPP_API_TOKEN
            whatsapp_phone_id = config.WHATSAPP_PHONE_NUMBER_ID
            logger.info("WhatsApp configuration found")
        except Exception as e:
            logger.error(f"WhatsApp configuration not found: {e}")
            logger.error("Please set SERVER_WHATSAPP_API_TOKEN and SERVER_WHATSAPP_PHONE_NUMBER_ID environment variables.")
            return

        # Create database session
        db = create_db_session(config.DB_FULL_URL)

        # Use the existing user from database
        user_id = "eddffda4-4d00-4725-aec0-444276917c71"
        test_user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
        if not test_user:
            logger.error(f"User with ID {user_id} not found in database")
            return

        logger.info(f"Found user: {test_user.name if hasattr(test_user, 'name') else 'Unknown'} with phone {test_user.phone_number}")

        # Find NOTIFYME app
        notifyme_app = db.query(App).filter(App.name == "NOTIFYME").first()
        if not notifyme_app:
            logger.error("NOTIFYME app not found in database")
            return

        # Check if linked account exists, create if not
        test_linked_account = db.query(LinkedAccount).filter(
            LinkedAccount.user_id == user_id,
            LinkedAccount.app_id == notifyme_app.id
        ).first()

        if not test_linked_account:
            # Create a linked account for NOTIFYME
            from aci.common.enums import SecurityScheme
            test_linked_account = LinkedAccount(
                user_id=user_id,
                app_id=notifyme_app.id,
                security_scheme=SecurityScheme.NO_AUTH,
                security_credentials={},
                disabled_functions=[]
            )
            db.add(test_linked_account)
            db.commit()
            logger.info("Created NOTIFYME linked account")

        # Use the provided artifact ID
        artifact_id = "5e2d3cb1-c8c4-4f85-9cb2-5690834fe86e"
        artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
        if not artifact:
            logger.error(f"Artifact with ID {artifact_id} not found in database")
            return

        logger.info(f"Found artifact: {artifact.filename} ({artifact.size_bytes} bytes)")

        # Create Notifyme connector
        security_scheme = NoAuthScheme()
        security_credentials = NoAuthSchemeCredentials()

        notifyme = Notifyme(
            linked_account=test_linked_account,
            security_scheme=security_scheme,
            security_credentials=security_credentials
        )

        # Send WhatsApp message with file
        logger.info("Sending WhatsApp message with file attachment...")
        result = notifyme.send_me_whatsapp_notification(
            body="Test WhatsApp file attachment from CLI test script",
            artifact_id=artifact_id
        )

        logger.info(f"WhatsApp send result: {result}")

        logger.info("Test completed successfully!")

    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()