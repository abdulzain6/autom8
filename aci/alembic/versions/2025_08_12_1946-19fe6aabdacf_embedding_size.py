"""Recreate embedding columns with 1536 dimensions

Revision ID: d9e8f7g6h5i4
Revises: a1b2c3d4e5f6
Create Date: 2025-08-13 00:47:36.123456+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR

# revision identifiers, used by Alembic.
revision: str = '19fe6aabdacf'
down_revision: Union[str, None] = 'b28ecb778605'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """
    Drops and recreates the embedding columns with 1536 dimensions.
    """
    print("Recreating embedding columns as vector(1536)...")

    # --- Recreate column for 'apps' table ---
    # Step 1: Drop the old column
    op.drop_column('apps', 'embedding')
    # Step 2: Add the new column as nullable first, to handle existing rows
    op.add_column('apps', sa.Column('embedding', VECTOR(1536), nullable=True))
    # Step 3: Populate all rows with a placeholder zero vector
    op.execute("UPDATE apps SET embedding = array_fill(0, ARRAY[1536])")
    # Step 4: Alter the column to be non-nullable as per the original schema
    op.alter_column('apps', 'embedding', nullable=False)

    # --- Recreate column for 'functions' table ---
    # Step 1: Drop the old column
    op.drop_column('functions', 'embedding')
    # Step 2: Add the new column as nullable first
    op.add_column('functions', sa.Column('embedding', VECTOR(1536), nullable=True))
    # Step 3: Populate all rows with a placeholder zero vector
    op.execute("UPDATE functions SET embedding = array_fill(0, ARRAY[1536])")
    # Step 4: Alter the column to be non-nullable
    op.alter_column('functions', 'embedding', nullable=False)

    print("Upgrade complete.")


def downgrade() -> None:
    """
    Reverts the change by recreating columns with 768 dimensions.
    """
    print("Recreating embedding columns back to vector(768)...")

    # --- Recreate column for 'apps' table ---
    op.drop_column('apps', 'embedding')
    op.add_column('apps', sa.Column('embedding', VECTOR(768), nullable=True))
    op.execute("UPDATE apps SET embedding = array_fill(0, ARRAY[768])")
    op.alter_column('apps', 'embedding', nullable=False)

    # --- Recreate column for 'functions' table ---
    op.drop_column('functions', 'embedding')
    op.add_column('functions', sa.Column('embedding', VECTOR(768), nullable=True))
    op.execute("UPDATE functions SET embedding = array_fill(0, ARRAY[768])")
    op.alter_column('functions', 'embedding', nullable=False)

    print("Downgrade complete.")