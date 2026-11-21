"""Track the embedding/chunking configuration used to index each document."""
from alembic import op
import sqlalchemy as sa
revision = "b72f2a"
down_revision = "ed24174a2537"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("indexed_signature", sa.String(32), nullable=True))


def downgrade():
    op.drop_column("documents", "indexed_signature")
