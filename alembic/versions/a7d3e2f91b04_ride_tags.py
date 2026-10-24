"""ride_tags

Revision ID: a7d3e2f91b04
Revises: c40accf0e466
Create Date: 2026-10-24 10:00:00.000000

`Ride.excluded` becomes a tag. Rides excluded today keep being excluded: they
are moved onto a seeded "Not training" tag whose effect excludes them, so the
set of rides in analysis — and therefore every stored route average — is the
same before and after.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = 'a7d3e2f91b04'
down_revision: Union[str, Sequence[str], None] = 'c40accf0e466'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen here rather than imported from the app: a migration must keep meaning
# what it meant when it was written.
EXCLUDE = 'exclude_from_analysis'


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('tag',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('color', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('effect', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tag', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tag_name'), ['name'], unique=True)
        batch_op.create_index(batch_op.f('ix_tag_effect'), ['effect'], unique=False)

    op.create_table('ridetag',
    sa.Column('ride_id', sa.Integer(), nullable=False),
    sa.Column('tag_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['ride_id'], ['ride.id'], ),
    sa.ForeignKeyConstraint(['tag_id'], ['tag.id'], ),
    sa.PrimaryKeyConstraint('ride_id', 'tag_id')
    )
    with op.batch_alter_table('ridetag', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ridetag_tag_id'), ['tag_id'], unique=False)

    # Seeded on every database, not only where rides are excluded: it is the
    # obvious entry point for the feature the switch used to provide. Arne can
    # rename or delete it in Settings.
    op.execute(
        sa.text(
            "INSERT INTO tag (name, color, effect, created_at) "
            "VALUES ('Not training', 'gray', :effect, CURRENT_TIMESTAMP)"
        ).bindparams(effect=EXCLUDE)
    )
    op.execute(
        "INSERT INTO ridetag (ride_id, tag_id) "
        "SELECT ride.id, tag.id FROM ride, tag "
        "WHERE ride.excluded = 1 AND tag.name = 'Not training'"
    )

    with op.batch_alter_table('ride', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ride_excluded'))
        batch_op.drop_column('excluded')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('ride', schema=None) as batch_op:
        batch_op.add_column(sa.Column('excluded', sa.Boolean(), server_default=sa.text('0'), nullable=False))
        batch_op.create_index(batch_op.f('ix_ride_excluded'), ['excluded'], unique=False)

    # Any excluding tag, not just the seeded one: that is what "excluded"
    # meant while the tags existed.
    op.execute(
        sa.text(
            "UPDATE ride SET excluded = 1 WHERE id IN ("
            "SELECT ridetag.ride_id FROM ridetag JOIN tag ON tag.id = ridetag.tag_id "
            "WHERE tag.effect = :effect)"
        ).bindparams(effect=EXCLUDE)
    )

    with op.batch_alter_table('ridetag', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ridetag_tag_id'))
    op.drop_table('ridetag')

    with op.batch_alter_table('tag', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tag_effect'))
        batch_op.drop_index(batch_op.f('ix_tag_name'))
    op.drop_table('tag')
