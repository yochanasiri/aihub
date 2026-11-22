"""Add user-scoped, encrypted Gmail connections and imports."""
from alembic import op
import sqlalchemy as sa

revision = "59d4c84547ac"
down_revision = "aa491de2f010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gmail_connections",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("import_page_token", sa.Text(), nullable=True),
        sa.Column("imported_count", sa.Integer(), nullable=False),
        sa.Column("import_complete", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "gmail_oauth_states",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("state_hash"),
    )
    op.create_index("ix_gmail_oauth_states_user_id", "gmail_oauth_states", ["user_id"])
    op.create_index("ix_gmail_oauth_states_expires_at", "gmail_oauth_states", ["expires_at"])
    op.create_table(
        "gmail_imported_messages",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=False),
        sa.Column("encrypted_content", sa.Text(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider_message_id"),
    )

def downgrade():
    op.drop_table("gmail_imported_messages")
    op.drop_index("ix_gmail_oauth_states_expires_at", table_name="gmail_oauth_states")
    op.drop_index("ix_gmail_oauth_states_user_id", table_name="gmail_oauth_states")
    op.drop_table("gmail_oauth_states")
    op.drop_table("gmail_connections")
