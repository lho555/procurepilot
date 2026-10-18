"""Transactional application checkpoints and workflow start idempotency."""
from alembic import op
import sqlalchemy as sa

revision = "0003_workflow"
down_revision = "0002_approval"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("workflow_runs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("idempotency_key", sa.String(80), nullable=False, unique=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(40), sa.ForeignKey("purchase_requests.id"), nullable=True, unique=True),
        sa.Column("owner_token", sa.String(32), nullable=True),
        sa.Column("lock_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table("workflow_checkpoints",
        sa.Column("run_id", sa.String(40), sa.ForeignKey("workflow_runs.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM workflow_runs")):
        raise RuntimeError("Cannot downgrade while durable workflow records exist; restore a verified backup")
    op.drop_table("workflow_checkpoints")
    op.drop_table("workflow_runs")
