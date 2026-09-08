"""add workout set logging and workout summary fields

Revision ID: 7c4e8a1f2b90
Revises: b1a7c4d92f30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "7c4e8a1f2b90"
down_revision: str | Sequence[str] | None = "b1a7c4d92f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ================================================================
    # workout_logs — migrated summary columns
    # ================================================================

    op.alter_column(
        "workout_logs",
        "user_email",
        existing_type=sa.String(length=255),
        nullable=True,
    )

    op.alter_column(
        "workout_logs",
        "exercise_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

    op.alter_column(
        "workout_logs",
        "set_no",
        existing_type=sa.Integer(),
        nullable=True,
    )

    op.add_column(
        "workout_logs",
        sa.Column(
            "client_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "workout_logs",
        sa.Column(
            "total_sets",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "workout_logs",
        sa.Column(
            "completed_sets",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "workout_logs",
        sa.Column(
            "completion_percent",
            sa.Float(),
            nullable=True,
        ),
    )

    op.add_column(
        "workout_logs",
        sa.Column(
            "calories_burned",
            sa.Integer(),
            nullable=True,
        ),
    )

    # ================================================================
    # workout_logs indexes
    # ================================================================

    op.create_index(
        "uq_workout_logs_summary_client_period_day",
        "workout_logs",
        [
            "client_id",
            "month_no",
            "week_no",
            "day_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "client_id IS NOT NULL "
            "AND total_sets IS NOT NULL "
            "AND completed_sets IS NOT NULL"
        ),
    )

    op.create_index(
        "ix_workout_logs_summary_client_period",
        "workout_logs",
        [
            "client_id",
            "month_no",
            "week_no",
            "day_id",
        ],
        unique=False,
        postgresql_where=sa.text(
            "client_id IS NOT NULL "
            "AND total_sets IS NOT NULL"
        ),
    )

    # ================================================================
    # workout_set_logs
    # ================================================================

    op.create_table(
        "workout_set_logs",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "client_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "month_no",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "week_no",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "day_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "exercise_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "set_no",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "completed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "uq_workout_set_logs_client_period_set",
        "workout_set_logs",
        [
            "client_id",
            "month_no",
            "week_no",
            "day_id",
            "exercise_id",
            "set_no",
        ],
        unique=True,
    )

    op.create_index(
        "ix_workout_set_logs_client_period_day",
        "workout_set_logs",
        [
            "client_id",
            "month_no",
            "week_no",
            "day_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_workout_set_logs_client_month",
        "workout_set_logs",
        [
            "client_id",
            "month_no",
        ],
        unique=False,
    )


def downgrade() -> None:
    # ================================================================
    # workout_set_logs
    # ================================================================

    op.drop_index(
        "ix_workout_set_logs_client_month",
        table_name="workout_set_logs",
    )

    op.drop_index(
        "ix_workout_set_logs_client_period_day",
        table_name="workout_set_logs",
    )

    op.drop_index(
        "uq_workout_set_logs_client_period_set",
        table_name="workout_set_logs",
    )

    op.drop_table("workout_set_logs")

    # ================================================================
    # workout_logs indexes
    # ================================================================

    op.drop_index(
        "ix_workout_logs_summary_client_period",
        table_name="workout_logs",
        postgresql_where=sa.text(
            "client_id IS NOT NULL "
            "AND total_sets IS NOT NULL"
        ),
    )

    op.drop_index(
        "uq_workout_logs_summary_client_period_day",
        table_name="workout_logs",
        postgresql_where=sa.text(
            "client_id IS NOT NULL "
            "AND total_sets IS NOT NULL "
            "AND completed_sets IS NOT NULL"
        ),
    )

    # ================================================================
    # workout_logs columns
    # ================================================================

    op.drop_column(
        "workout_logs",
        "calories_burned",
    )

    op.drop_column(
        "workout_logs",
        "completion_percent",
    )

    op.drop_column(
        "workout_logs",
        "completed_sets",
    )

    op.drop_column(
        "workout_logs",
        "total_sets",
    )

    op.drop_column(
        "workout_logs",
        "client_id",
    )

    op.alter_column(
        "workout_logs",
        "set_no",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.alter_column(
        "workout_logs",
        "exercise_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.alter_column(
        "workout_logs",
        "user_email",
        existing_type=sa.String(length=255),
        nullable=False,
    )