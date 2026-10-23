"""Isolated data/test DB mirror of Backend V11. Never run Alembic on the shared DB."""
from alembic import op

revision = "0002_model_scope"
down_revision = "0001_rag"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE public.rag_request_contexts ADD COLUMN model_scope jsonb")
    op.execute("ALTER TABLE public.rag_request_contexts ADD CONSTRAINT model_scope_object "
               "CHECK (model_scope IS NULL OR jsonb_typeof(model_scope) = 'object')")


def downgrade():
    op.execute("ALTER TABLE public.rag_request_contexts DROP COLUMN model_scope")
