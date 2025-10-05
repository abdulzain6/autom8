"""update_user_sync_trigger

Revision ID: f42c9c6c2096
Revises: 43951112ff89
Create Date: 2025-10-05 18:59:44.895020+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f42c9c6c2096'
down_revision: Union[str, None] = '43951112ff89'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Update the trigger function to automatically enable NO_AUTH apps for new users
    op.execute("""
        CREATE OR REPLACE FUNCTION public.sync_auth_user_to_public()
        RETURNS TRIGGER AS $$
        DECLARE
            app_record RECORD;
        BEGIN
            -- Sync user data from auth.users to public.users
            INSERT INTO public.users (id, email, created_at, updated_at)
            VALUES (NEW.id::text, NEW.email, NEW.created_at, NEW.updated_at)
            ON CONFLICT (id) DO UPDATE
            SET email = EXCLUDED.email, updated_at = EXCLUDED.updated_at;

            -- Automatically enable all NO_AUTH apps for the new user
            FOR app_record IN
                SELECT id, name FROM apps WHERE security_schemes ? 'NO_AUTH'
            LOOP
                -- Check if user already has a linked account for this app
                IF NOT EXISTS (
                    SELECT 1 FROM linked_accounts
                    WHERE user_id = NEW.id::text AND app_id = app_record.id
                ) THEN
                    -- Create LinkedAccount for NO_AUTH app
                    INSERT INTO linked_accounts (
                        user_id, app_id, security_scheme, security_credentials, disabled_functions
                    ) VALUES (
                        NEW.id::text,
                        app_record.id,
                        'NO_AUTH',
                        '{}'::jsonb,
                        '[]'::jsonb
                    );
                END IF;
            END LOOP;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
    """)


def downgrade() -> None:
    # Revert to the original trigger function that only syncs user data
    op.execute("""
        CREATE OR REPLACE FUNCTION public.sync_auth_user_to_public()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO public.users (id, email, created_at, updated_at)
            VALUES (NEW.id::text, NEW.email, NEW.created_at, NEW.updated_at)
            ON CONFLICT (id) DO UPDATE
            SET email = EXCLUDED.email, updated_at = EXCLUDED.updated_at;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
    """)