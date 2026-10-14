"""widen totp_secret for Fernet ciphertext + add recovery_codes (M9 2FA)

Revision ID: a1f5c8e20934
Revises: 7b8a03a36663
Create Date: 2026-10-11 10:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1f5c8e20934'
down_revision: Union[str, Sequence[str], None] = '7b8a03a36663'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # totp_secret now stores Fernet ciphertext (v1: + base64) → widen 64 → 255
    op.alter_column(
        'users', 'totp_secret',
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=True,
        existing_comment='Base32-encoded TOTP secret (nullable when TOTP is disabled).',
        comment='Fernet ciphertext of the Base32 TOTP secret (M9; plaintext never stored).',
    )
    op.add_column(
        'users',
        sa.Column(
            'recovery_codes', postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'[]'::jsonb"),
            comment='SHA-256 digests of the issued one-time recovery codes (M9).',
        ),
    )


def downgrade() -> None:
    op.drop_column('users', 'recovery_codes')
    op.alter_column(
        'users', 'totp_secret',
        existing_type=sa.String(length=255),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
