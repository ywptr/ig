"""Add explicit provenance, media metadata and normalized artifact lineage."""
from alembic import op
import sqlalchemy as sa

revision = "1a0003000003"
down_revision = "1a0009000009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("artifacts", sa.Column("provenance", sa.String(9), nullable=False, server_default="unknown"))
    for name in ("width", "height"):
        op.add_column("artifacts", sa.Column(name, sa.Integer()))
    for name in ("duration_seconds", "fps"):
        op.add_column("artifacts", sa.Column(name, sa.Float()))
    op.add_column("artifacts", sa.Column("sha256", sa.String(64)))
    op.create_index("ix_artifacts_library_order", "artifacts", ["tenant_id", "user_id", "created_at", "artifact_id"])
    op.create_table("artifact_generations",
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.artifact_id"), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.job_id"), nullable=False),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("executions.execution_id"), nullable=False))
    op.create_index("ix_artifact_generations_job_id", "artifact_generations", ["job_id"])
    op.create_index("ix_artifact_generations_execution_id", "artifact_generations", ["execution_id"])
    op.create_table("artifact_inputs",
        sa.Column("output_artifact_id", sa.String(36), sa.ForeignKey("artifacts.artifact_id"), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("input_artifact_id", sa.String(36), sa.ForeignKey("artifacts.artifact_id"), nullable=False),
        sa.Column("relation", sa.String(30), nullable=False),
        sa.Column("role", sa.String(30), nullable=False), sa.Column("strength", sa.String(20)),
        sa.CheckConstraint("output_artifact_id != input_artifact_id", name="ck_artifact_input_not_self"))
    op.create_index("ix_artifact_inputs_input_artifact_id", "artifact_inputs", ["input_artifact_id"])
    postgres = op.get_context().dialect.name == "postgresql"
    def field(alias, key):
        return f"{alias}.metadata_json->>'{key}'" if postgres else f"json_extract({alias}.metadata_json, '$.{key}')"
    uploaded = field('a', 'ingest_method')
    op.execute(f"UPDATE artifacts AS a SET provenance='uploaded' WHERE {uploaded}='upload'")
    output_id = field('e', 'artifact_id')
    # Only a unique, owner-consistent producer is evidence. Upload evidence wins
    # and ambiguous historical output pointers remain explicitly unknown.
    op.execute(f"""INSERT INTO artifact_generations (artifact_id, job_id, execution_id)
        SELECT a.artifact_id, MIN(j.job_id), MIN(e.execution_id)
        FROM artifacts a JOIN executions e ON {output_id}=a.artifact_id
        JOIN jobs j ON j.job_id=e.job_id
        WHERE a.provenance='unknown' AND a.tenant_id=e.tenant_id AND a.user_id=e.user_id
          AND a.tenant_id=j.tenant_id AND a.user_id=j.user_id
        GROUP BY a.artifact_id HAVING COUNT(*)=1""")
    op.execute("UPDATE artifacts SET provenance='generated' WHERE artifact_id IN (SELECT artifact_id FROM artifact_generations)")
    if postgres:
        refs = "CASE WHEN json_typeof(j.input_json->'request'->'references')='array' THEN j.input_json->'request'->'references' WHEN json_typeof(j.input_json->'references')='array' THEN j.input_json->'references' ELSE '[]'::json END"
        expansion = f"CROSS JOIN LATERAL json_array_elements({refs}) WITH ORDINALITY AS r(value, ordinal)"
        refid, role, strength, position = "r.value->>'artifact_id'", "r.value->>'role'", "r.value->>'strength'", "r.ordinal-1"
    else:
        refs = "CASE WHEN json_type(j.input_json, '$.request.references')='array' THEN json_extract(j.input_json, '$.request.references') WHEN json_type(j.input_json, '$.references')='array' THEN json_extract(j.input_json, '$.references') ELSE '[]' END"
        expansion = f"JOIN json_each({refs}) AS r"
        obj = "CASE WHEN r.type='object' THEN r.value ELSE '{}' END"
        refid, role, strength, position = f"json_extract({obj}, '$.artifact_id')", f"json_extract({obj}, '$.role')", f"json_extract({obj}, '$.strength')", "CAST(r.key AS INTEGER)"
    role = f"COALESCE({role}, 'previous_output')"
    op.execute(f"""INSERT INTO artifact_inputs (output_artifact_id, position, input_artifact_id, relation, role, strength)
        SELECT a.artifact_id, {position}, i.artifact_id, 'reference', {role}, {strength}
        FROM artifact_generations g JOIN artifacts a ON a.artifact_id=g.artifact_id
        JOIN jobs j ON j.job_id=g.job_id {expansion}
        JOIN artifacts i ON i.artifact_id={refid}
        WHERE i.tenant_id=a.tenant_id AND i.user_id=a.user_id AND i.artifact_id!=a.artifact_id
        AND {role} IN ('reference','character','outfit','style','pose','composition','environment','previous_output','first_frame')
        AND ({strength} IS NULL OR {strength} IN ('loose','normal','strong'))""")


def downgrade():
    op.drop_table("artifact_inputs")
    op.drop_table("artifact_generations")
    op.drop_index("ix_artifacts_library_order", table_name="artifacts")
    for name in ("sha256", "fps", "duration_seconds", "height", "width", "provenance"):
        op.drop_column("artifacts", name)
