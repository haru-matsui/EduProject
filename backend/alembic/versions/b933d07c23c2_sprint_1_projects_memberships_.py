"""Sprint 1 projects memberships invitations tasks sessions

Revision ID: b933d07c23c2
Revises: 
"""
from alembic import op
import sqlalchemy as sa


revision = 'b933d07c23c2'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Frozen initial schema; no runtime model imports.
    op.create_table('oidc_flows',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('state_hash', sa.String(length=64), nullable=False),
    sa.Column('browser_hash', sa.String(length=64), nullable=False),
    sa.Column('verifier', sa.String(length=128), nullable=False),
    sa.Column('nonce', sa.String(length=128), nullable=False),
    sa.Column('return_path', sa.String(length=400), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('state_hash')
    )
    op.create_index(op.f('ix_oidc_flows_expires_at'), 'oidc_flows', ['expires_at'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('telegram_issuer', sa.String(length=200), nullable=False),
    sa.Column('telegram_sub', sa.String(length=256), nullable=False),
    sa.Column('telegram_user_id', sa.BigInteger(), nullable=True),
    sa.Column('display_name', sa.String(length=120), nullable=False),
    sa.Column('group_name', sa.String(length=80), nullable=True),
    sa.Column('timezone', sa.String(length=80), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('telegram_issuer', 'telegram_sub', name='uq_users_identity'),
    sa.UniqueConstraint('telegram_user_id')
    )
    op.create_table('projects',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('goal', sa.Text(), nullable=False),
    sa.Column('expected_result', sa.Text(), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('active','final_review','changes_requested','completed')", name='ck_project_status'),
    sa.CheckConstraint('version >= 1', name='ck_project_version'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('sessions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('csrf_token', sa.String(length=128), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_sessions_expires_at'), 'sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_table('project_members',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("role IN ('lead','student','teacher')", name='ck_member_role'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'project_id', name='uq_member_id_project'),
    sa.UniqueConstraint('project_id', 'user_id', name='uq_member_project_user')
    )
    op.create_index(op.f('ix_project_members_project_id'), 'project_members', ['project_id'], unique=False)
    op.create_index(op.f('ix_project_members_user_id'), 'project_members', ['user_id'], unique=False)
    op.create_index('uq_active_lead', 'project_members', ['project_id'], unique=True, postgresql_where=sa.text("active AND role = 'lead'"), sqlite_where=sa.text("active = 1 AND role = 'lead'"))
    op.create_table('audit_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('actor_member_id', sa.Uuid(), nullable=False),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('object_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_member_id'], ['project_members.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_events_project_id'), 'audit_events', ['project_id'], unique=False)
    op.create_table('invitations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("role IN ('student','teacher')", name='ck_invitation_role'),
    sa.ForeignKeyConstraint(['created_by'], ['project_members.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_invitations_project_id'), 'invitations', ['project_id'], unique=False)
    op.create_table('tasks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('assignee_member_id', sa.Uuid(), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('expected_result', sa.Text(), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('priority', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("priority IN ('low','medium','high')", name='ck_task_priority'),
    sa.CheckConstraint("status IN ('todo','in_progress','in_review','done')", name='ck_task_status'),
    sa.CheckConstraint('version >= 1', name='ck_task_version'),
    sa.ForeignKeyConstraint(['assignee_member_id', 'project_id'], ['project_members.id', 'project_members.project_id'], name='fk_task_assignee_same_project'),
    sa.ForeignKeyConstraint(['created_by'], ['project_members.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tasks_project_id'), 'tasks', ['project_id'], unique=False)



def downgrade():
    # Frozen initial schema; no runtime model imports.
    op.drop_index(op.f('ix_tasks_project_id'), table_name='tasks')
    op.drop_table('tasks')
    op.drop_index(op.f('ix_invitations_project_id'), table_name='invitations')
    op.drop_table('invitations')
    op.drop_index(op.f('ix_audit_events_project_id'), table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_index('uq_active_lead', table_name='project_members', postgresql_where=sa.text("active AND role = 'lead'"), sqlite_where=sa.text("active = 1 AND role = 'lead'"))
    op.drop_index(op.f('ix_project_members_user_id'), table_name='project_members')
    op.drop_index(op.f('ix_project_members_project_id'), table_name='project_members')
    op.drop_table('project_members')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_expires_at'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_table('projects')
    op.drop_table('users')
    op.drop_index(op.f('ix_oidc_flows_expires_at'), table_name='oidc_flows')
    op.drop_table('oidc_flows')

