from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_db
from .errors import APIError
from .models import AuditEvent, Invitation, Member, Project, User, now
from .schemas import InvitationCreate, InvitationCreated, InvitationPreview, InvitationView, MemberView, Page, ProjectCreate, ProjectView, UserView
from .security import AuthContext, current_auth, get_membership, optional_auth, random_token, require_lead, require_open, token_hash, write_auth

router = APIRouter(tags=["projects"])
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
InviteToken = Annotated[str, Path(min_length=20, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")]


def project_view(project: Project, role: str) -> ProjectView:
    return ProjectView(**{name: getattr(project, name) for name in ProjectView.model_fields if name != "current_role"}, current_role=role)


def member_view(db: Session, member: Member) -> MemberView:
    return MemberView(**{name: getattr(member, name) for name in MemberView.model_fields if name != "user"}, user=UserView.model_validate(db.get(User, member.user_id)))


def audit(db: Session, project_id: UUID, member_id: UUID, action: str, object_id: UUID):
    db.add(AuditEvent(project_id=project_id, actor_member_id=member_id, action=action, object_id=object_id))


@router.get("/projects", response_model=Page[ProjectView])
def projects(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db), page: PageNumber = 1, page_size: PageSize = 20):
    query = select(Project, Member).join(Member, Member.project_id == Project.id).where(Member.user_id == auth.user.id, Member.active.is_(True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(Project.created_at, Project.id).offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[project_view(project, member.role) for project, member in rows], page=page, page_size=page_size, total=total)


@router.post("/projects", status_code=201, response_model=ProjectView)
def create_project(body: ProjectCreate, response: Response, auth: AuthContext = Depends(write_auth), db: Session = Depends(get_db)):
    project = Project(**body.model_dump(), created_by=auth.user.id)
    db.add(project)
    db.flush()
    member = Member(project_id=project.id, user_id=auth.user.id, role="lead")
    db.add(member)
    db.flush()
    audit(db, project.id, member.id, "project.created", project.id)
    db.commit()
    response.headers["ETag"] = f'"{project.version}"'
    return project_view(project, member.role)


@router.get("/projects/{project_id}", response_model=ProjectView)
def project_detail(project_id: UUID, response: Response, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    project, member = get_membership(db, project_id, auth.user.id)
    response.headers["ETag"] = f'"{project.version}"'
    return project_view(project, member.role)


@router.get("/projects/{project_id}/members", response_model=Page[MemberView])
def members(project_id: UUID, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db), page: PageNumber = 1, page_size: PageSize = 20, include_inactive: bool = False):
    get_membership(db, project_id, auth.user.id)
    query = select(Member).where(Member.project_id == project_id)
    if not include_inactive:
        query = query.where(Member.active.is_(True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Member.joined_at, Member.id).offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[member_view(db, member) for member in rows], page=page, page_size=page_size, total=total)


@router.get("/projects/{project_id}/invitations", response_model=Page[InvitationView])
def invitations(project_id: UUID, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db), page: PageNumber = 1, page_size: PageSize = 20):
    _, member = get_membership(db, project_id, auth.user.id)
    require_lead(member)
    query = select(Invitation).where(Invitation.project_id == project_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Invitation.created_at, Invitation.id).offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[InvitationView.model_validate(row) for row in rows], page=page, page_size=page_size, total=total)


@router.post("/projects/{project_id}/invitations", status_code=201, response_model=InvitationCreated)
def create_invitation(project_id: UUID, body: InvitationCreate, request: Request, auth: AuthContext = Depends(write_auth), db: Session = Depends(get_db)):
    project, member = get_membership(db, project_id, auth.user.id, lock=True)
    require_lead(member)
    require_open(project)
    token = random_token()
    invitation = Invitation(project_id=project.id, created_by=member.id, role=body.role, token_hash=token_hash(token), expires_at=now() + timedelta(days=7))
    db.add(invitation)
    db.flush()
    audit(db, project.id, member.id, "invitation.created", invitation.id)
    db.commit()
    return InvitationCreated(invitation=InvitationView.model_validate(invitation), invite_url=request.app.state.settings.public_origin + "/invitations/" + token)


@router.delete("/projects/{project_id}/invitations/{invitation_id}", status_code=204)
def revoke_invitation(project_id: UUID, invitation_id: UUID, auth: AuthContext = Depends(write_auth), db: Session = Depends(get_db)):
    project, member = get_membership(db, project_id, auth.user.id, lock=True)
    require_lead(member)
    require_open(project)
    invitation = db.scalar(select(Invitation).where(Invitation.id == invitation_id, Invitation.project_id == project_id).with_for_update())
    if not invitation:
        raise APIError(404, "not_found", "Объект не найден или недоступен.")
    if invitation.revoked_at is None:
        invitation.revoked_at = now()
        audit(db, project_id, member.id, "invitation.revoked", invitation.id)
    db.commit()
    return Response(status_code=204)


def get_valid_invitation(db: Session, token: str, lock: bool = False) -> Invitation:
    query = select(Invitation).where(Invitation.token_hash == token_hash(token))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    invitation = db.scalar(query)
    if not invitation:
        raise APIError(404, "not_found", "Приглашение не найдено.")
    if invitation.revoked_at is not None or invitation.expires_at <= now():
        raise APIError(410, "invitation_unavailable", "Приглашение отозвано или срок его действия истёк.")
    return invitation


@router.get("/invitations/{token}", response_model=InvitationPreview, tags=["invitations"])
def invitation_preview(token: InviteToken, request: Request, db: Session = Depends(get_db)):
    invitation = get_valid_invitation(db, token)
    project = db.get(Project, invitation.project_id)
    return InvitationPreview(project_title=project.title, role=invitation.role, expires_at=invitation.expires_at, requires_login=optional_auth(request, db) is None)


@router.post("/invitations/{token}/accept", response_model=MemberView, tags=["invitations"])
def accept_invitation(token: InviteToken, auth: AuthContext = Depends(write_auth), db: Session = Depends(get_db)):
    invitation = get_valid_invitation(db, token)
    project = db.scalar(select(Project).where(Project.id == invitation.project_id).with_for_update())
    require_open(project)
    # Recheck after waiting for the project lock: concurrent revoke wins safely.
    invitation = get_valid_invitation(db, token, lock=True)
    member = db.scalar(select(Member).where(Member.project_id == project.id, Member.user_id == auth.user.id))
    if member and member.role != invitation.role:
        raise APIError(409, "role_conflict", "Приглашение не изменяет существующую роль в проекте.")
    changed = False
    if not member:
        member = Member(project_id=project.id, user_id=auth.user.id, role=invitation.role)
        db.add(member)
        db.flush()
        changed = True
    elif not member.active:
        member.active, member.left_at = True, None
        changed = True
    if changed:
        audit(db, project.id, member.id, "member.joined", member.id)
    db.commit()
    return member_view(db, member)
