"""discovery sources: integrations, source_records, geo_areas

Revision ID: a1f3c9d2e4b5
Revises: 8bc8da4bb797
Create Date: 2026-10-01 12:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a1f3c9d2e4b5'
down_revision: Union[str, None] = '8bc8da4bb797'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('integrations',
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('api_key', sa.Text(), nullable=True),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('source_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('mission_id', sa.UUID(), nullable=True),
    sa.Column('run_id', sa.UUID(), nullable=True),
    sa.Column('source', sa.String(length=40), nullable=False),
    sa.Column('external_id', sa.String(length=200), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=True),
    sa.Column('website_domain', sa.String(length=253), nullable=True),
    sa.Column('website_url', sa.String(length=1000), nullable=True),
    sa.Column('phone', sa.String(length=40), nullable=True),
    sa.Column('address', sa.String(length=500), nullable=True),
    sa.Column('city', sa.String(length=100), nullable=True),
    sa.Column('region', sa.String(length=100), nullable=True),
    sa.Column('postal_code', sa.String(length=20), nullable=True),
    sa.Column('country_code', sa.String(length=2), nullable=True),
    sa.Column('lat', sa.Float(), nullable=True),
    sa.Column('lng', sa.Float(), nullable=True),
    sa.Column('categories', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('matched_business_id', sa.UUID(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['matched_business_id'], ['business_nodes.id'], ),
    sa.ForeignKeyConstraint(['mission_id'], ['missions.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source', 'external_id', name='uq_source_record')
    )
    op.create_index('ix_source_records_domain', 'source_records', ['website_domain'], unique=False)
    op.create_index('ix_source_records_mission', 'source_records', ['mission_id'], unique=False)
    op.create_index(op.f('ix_source_records_matched_business_id'), 'source_records', ['matched_business_id'], unique=False)
    op.create_table('geo_areas',
    sa.Column('query', sa.String(length=300), nullable=False),
    sa.Column('display_name', sa.String(length=500), nullable=True),
    sa.Column('osm_type', sa.String(length=20), nullable=True),
    sa.Column('osm_id', sa.BigInteger(), nullable=True),
    sa.Column('bbox', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('lat', sa.Float(), nullable=True),
    sa.Column('lng', sa.Float(), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('query')
    )


def downgrade() -> None:
    op.drop_table('geo_areas')
    op.drop_index(op.f('ix_source_records_matched_business_id'), table_name='source_records')
    op.drop_index('ix_source_records_mission', table_name='source_records')
    op.drop_index('ix_source_records_domain', table_name='source_records')
    op.drop_table('source_records')
    op.drop_table('integrations')
