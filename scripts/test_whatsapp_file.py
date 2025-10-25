#!/usr/bin/env python3
"""
Test script for WhatsApp file sending functionality.
This script GENERATES A RANDOM IMAGE and sends it via WhatsApp
using the existing Notifyme connector.

**Updated to use the provided FileManager class and its methods.**
"""

import logging
import sys
from pathlib import Path
import io
import random
from datetime import datetime

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("Pillow library not found. Please install it: pip install Pillow")
    sys.exit(1)

# Artifact is needed for the cleanup step
from aci.common.db.sql_models import Artifact, LinkedAccount, UserProfile, App
from aci.server.app_connectors.notifyme import Notifyme
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.common.logging_setup import get_logger
from aci.server import config
from aci.common.utils import create_db_session
# Import the specific FileManager you provided
from aci.server.file_management import FileManager 

logging.basicConfig(level=logging.INFO)
logger = get_logger(__name__)


def generate_random_image() -> tuple[bytes, str, str]:
    """Generates a simple random image."""
    logger.info("Generating a random image...")
    # Create a new image with a random color
    img = Image.new('RGB', (200, 200), color=(
        random.randint(0, 255),
        random.randint(0, 255),
        random.randint(0, 255)
    ))
    d = ImageDraw.Draw(img)
    d.text((10, 10), "Test Image", fill=(255, 255, 255))
    
    # Generate a unique filename
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"test_image_{timestamp}.png"
    mime_type = "image/png"
    
    # Save image to an in-memory buffer
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    file_bytes = buffer.getvalue()
    
    logger.info(f"Generated {filename} ({len(file_bytes)} bytes)")
    return file_bytes, filename, mime_type


def main():
    """Main test function."""
    db = None
    file_manager = None
    new_artifact_id = None
    
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

        # --- Create a new random image artifact ---
        file_bytes, filename, mime_type = generate_random_image()
        
        # Instantiate FileManager
        file_manager = FileManager(db)
        
        # Wrap the bytes in a BinaryIO object for the new function
        file_object = io.BytesIO(file_bytes)
        
        logger.info(f"Uploading new artifact {filename}...")
        
        # Use the `upload_artifact` function from the provided file_management.py
        new_artifact_id = file_manager.upload_artifact(
            user_id=user_id,
            run_id=None,  # This is a test, no run_id
            file_object=file_object,
            filename=filename,
            ttl_seconds=3600  # Set a 1-hour expiration for the test artifact
        )
        
        logger.info(f"Successfully created artifact with ID: {new_artifact_id}")
        
        # --- End of new artifact creation ---

        # Create Notifyme connector
        security_scheme = NoAuthScheme()
        security_credentials = NoAuthSchemeCredentials()

        notifyme = Notifyme(
            linked_account=test_linked_account,
            security_scheme=security_scheme,
            security_credentials=security_credentials
        )

        # Send WhatsApp message with the *new* file
        logger.info("Sending WhatsApp message with new image attachment...")
        result = notifyme.send_me_whatsapp_notification(
            body="Test WhatsApp *image* attachment from CLI test script",
            artifact_id=new_artifact_id  # <-- Use the new artifact's ID
        )

        logger.info(f"WhatsApp send result: {result}")

        logger.info("Test completed successfully!")

    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
        
    finally:
        # Optional: Clean up the created artifact
        if new_artifact_id and file_manager and db:
            try:
                # Need to fetch the record to get its file_path for deletion
                artifact_record = db.query(Artifact).filter(Artifact.id == new_artifact_id).first()
                if artifact_record:
                    logger.info(f"Cleaning up test artifact {new_artifact_id}...")
                    
                    # 1. Delete from Supabase Storage
                    file_manager.delete_from_storage(
                        bucket=FileManager.ARTIFACT_BUCKET,
                        path_in_bucket=artifact_record.file_path
                    )
                    
                    # 2. Delete from Database
                    db.delete(artifact_record)
                    db.commit()
                    logger.info("Cleanup successful.")
                else:
                    logger.warning(f"Could not find artifact {new_artifact_id} for cleanup.")
            except Exception as e:
                logger.warning(f"Could not clean up artifact: {e}")
                db.rollback()


if __name__ == "__main__":
    main()