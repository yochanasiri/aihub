"""Persist admin retrieval settings."""
from alembic import op
import sqlalchemy as sa

revision = "aa491de2f010"
down_revision = "4023c48f95c9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("retrieval_profile", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("top_k", sa.Integer(), nullable=False),
                    sa.Column("min_score", sa.Integer(), nullable=False))


def downgrade():
    op.drop_table("retrieval_profile")
