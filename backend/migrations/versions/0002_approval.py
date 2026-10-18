"""Stage 5 approval states, atomic audit and local mock receipts."""
from alembic import op
import sqlalchemy as sa

revision = "0002_approval"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("purchase_requests") as batch:
        batch.drop_constraint("ck_request_draft_only", type_="check")
        batch.add_column(sa.Column("mock_executed_at", sa.DateTime(), nullable=True))
        batch.create_check_constraint("ck_request_status", "status IN ('DRAFT', 'WAITING_APPROVAL', 'APPROVED', 'REJECTED')")
        batch.create_check_constraint("ck_request_execution_approved", "mock_executed_at IS NULL OR status = 'APPROVED'")
    op.create_table(
        "approval_events",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("request_id", sa.String(40), sa.ForeignKey("purchase_requests.id"), nullable=False),
        sa.Column("event", sa.String(20), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("comment", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("event IN ('SUBMITTED', 'APPROVED', 'REJECTED', 'MOCK_EXECUTED')", name="ck_approval_event"),
    )
    op.create_index("ix_approval_events_request_id", "approval_events", ["request_id"])
    op.create_table(
        "mock_actions",
        sa.Column("request_id", sa.String(40), sa.ForeignKey("purchase_requests.id"), primary_key=True),
        sa.Column("reference", sa.String(40), nullable=False, unique=True),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    # Never silently discard approval/audit data to satisfy the old DRAFT-only schema.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM purchase_requests WHERE status <> 'DRAFT'")) or connection.scalar(sa.text("SELECT COUNT(*) FROM approval_events")) or connection.scalar(sa.text("SELECT COUNT(*) FROM mock_actions")):
        raise RuntimeError("Cannot downgrade while approval or mock action data exists; restore a verified backup instead")
    op.drop_table("mock_actions")
    op.drop_index("ix_approval_events_request_id", table_name="approval_events")
    op.drop_table("approval_events")
    with op.batch_alter_table("purchase_requests") as batch:
        batch.drop_constraint("ck_request_execution_approved", type_="check")
        batch.drop_constraint("ck_request_status", type_="check")
        batch.drop_column("mock_executed_at")
        batch.create_check_constraint("ck_request_draft_only", "status = 'DRAFT'")
