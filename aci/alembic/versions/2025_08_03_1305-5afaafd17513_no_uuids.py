"""
Create a synced public.users table and convert all app-level UUIDs to Strings.

Revision ID: a1b2c3d4e5f6
Revises: c1a9b8d7e6f5
Create Date: 2025-08-03 18:30:00.123456+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '5afaafd17513'
down_revision: Union[str, None] = 'b9ad9e601e6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Define all foreign keys that will be affected.
# Format: (constraint_name, source_table, referent_table, local_cols, remote_cols, optional_referent_schema)
FOREIGN_KEYS = [
    # These now point to the NEW public.users table
    ('profiles_id_fkey', 'profiles', 'users', ['id'], ['id']),
    ('linked_accounts_user_id_fkey', 'linked_accounts', 'users', ['user_id'], ['id']),
    
    # These are the original app-level foreign keys
    ('functions_app_id_fkey', 'functions', 'apps', ['app_id'], ['id']),
    ('app_configurations_app_id_fkey', 'app_configurations', 'apps', ['app_id'], ['id']),
    ('default_app_credentials_app_id_fkey', 'default_app_credentials', 'apps', ['app_id'], ['id']),
    ('linked_accounts_app_id_fkey', 'linked_accounts', 'apps', ['app_id'], ['id']),
    ('secrets_linked_account_id_fkey', 'secrets', 'linked_accounts', ['linked_account_id'], ['id']),
]

# Define all columns that will be converted from UUID to String.
# Format: (table_name, column_name)
COLUMNS_TO_MIGRATE = [
    ('profiles', 'id'),
    ('linked_accounts', 'user_id'),
    ('apps', 'id'),
    ('app_configurations', 'id'),
    ('app_configurations', 'app_id'),
    ('default_app_credentials', 'id'),
    ('default_app_credentials', 'app_id'),
    ('functions', 'id'),
    ('functions', 'app_id'),
    ('linked_accounts', 'id'),
    ('linked_accounts', 'app_id'),
    ('secrets', 'id'),
    ('secrets', 'linked_account_id'),
]


def upgrade() -> None:
    # ### Manually scripted migration ###

    # Step 1: Create the new public.users table to mirror auth.users
    op.create_table(
        'users',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Step 2: Create the trigger function to sync data from auth.users
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

    # Step 3: Create the trigger on the auth.users table
    op.execute("""
        CREATE TRIGGER on_auth_user_change
        AFTER INSERT OR UPDATE ON auth.users
        FOR EACH ROW EXECUTE FUNCTION public.sync_auth_user_to_public();
    """)

    # Step 4: Backfill the new public.users table with existing data
    op.execute("""
        INSERT INTO public.users (id, email, created_at, updated_at)
        SELECT id::text, email, created_at, updated_at FROM auth.users
        ON CONFLICT (id) DO NOTHING;
    """)

    # Step 5: Drop all existing foreign key constraints
    for fk in FOREIGN_KEYS:
        try:
            op.drop_constraint(fk[0], fk[1], type_='foreignkey')
        except Exception as e:
            print(f"Could not drop constraint {fk[0]} on table {fk[1]}, it might not exist. Error: {e}")


    # Step 6: Alter all column types from UUID to String
    for table, column in COLUMNS_TO_MIGRATE:
        op.alter_column(
            table,
            column,
            existing_type=postgresql.UUID(as_uuid=True),
            type_=sa.String(),
            postgresql_using=f"{column}::text",
            existing_nullable=False
        )

    # Step 7: Re-create all foreign key constraints, now pointing to the correct tables
    for fk in FOREIGN_KEYS:
        referent_schema = fk[5] if len(fk) > 5 else None
        op.create_foreign_key(
            fk[0], fk[1], fk[2], fk[3], fk[4], referent_schema=referent_schema
        )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### Manually scripted downgrade ###

    # Step 1: Drop all foreign key constraints
    for fk in FOREIGN_KEYS:
        try:
            op.drop_constraint(fk[0], fk[1], type_='foreignkey')
        except Exception as e:
            print(f"Could not drop constraint {fk[0]} on table {fk[1]}. Error: {e}")

    # Step 2: Alter column types back from String to UUID
    for table, column in COLUMNS_TO_MIGRATE:
        op.alter_column(
            table,
            column,
            existing_type=sa.String(),
            type_=postgresql.UUID(as_uuid=True),
            postgresql_using=f"{column}::uuid",
            existing_nullable=False
        )

    # Step 3: Re-create old foreign keys pointing back to auth.users
    # This is a best-effort reversal.
    original_fks = [
        ('profiles_id_fkey', 'profiles', 'users', ['id'], ['id'], 'auth'),
        ('linked_accounts_user_id_fkey', 'linked_accounts', 'users', ['user_id'], ['id'], 'auth'),
        ('functions_app_id_fkey', 'functions', 'apps', ['app_id'], ['id']),
        ('app_configurations_app_id_fkey', 'app_configurations', 'apps', ['app_id'], ['id']),
        ('default_app_credentials_app_id_fkey', 'default_app_credentials', 'apps', ['app_id'], ['id']),
        ('linked_accounts_app_id_fkey', 'linked_accounts', 'apps', ['app_id'], ['id']),
        ('secrets_linked_account_id_fkey', 'secrets', 'linked_accounts', ['linked_account_id'], ['id']),
    ]
    for fk in original_fks:
        referent_schema = fk[5] if len(fk) > 5 else None
        op.create_foreign_key(
            fk[0], fk[1], fk[2], fk[3], fk[4], referent_schema=referent_schema
        )

    # Step 4: Drop the trigger, function, and the new users table
    op.execute("DROP TRIGGER IF EXISTS on_auth_user_change ON auth.users;")
    op.execute("DROP FUNCTION IF EXISTS public.sync_auth_user_to_public();")
    op.drop_table('users')
    # ### end Alembic commands ###
