from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .db import get_db
from .errors import APIError
from .models import Member, Task, now
from .projects import PageNumber, PageSize, audit
from .schemas import AssigneeUpdate, Page, TaskCreate, TaskView
from .security import AuthContext, check_version, current_auth, expected_version, get_membership, require_lead, require_open, write_auth

router = APIRouter(tags=["tasks"])


def task_flags(due_at, status, clock=None):
    clock = clock or now()
    return (status != "done" and due_at < clock, status != "done" and clock < due_at <= clock + timedelta(hours=24))


def task_view(task: Task, clock=None) -> TaskView:
    overdue, due_soon = task_flags(task.due_at, task.status, clock)
    return TaskView(**{name: getattr(task, name) for name in TaskView.model_fields if name not in {"is_overdue", "is_due_soon"}}, is_overdue=overdue, is_due_soon=due_soon)


def get_task(db: Session, project_id: UUID, task_id: UUID):
    task = db.scalar(select(Task).where(Task.id == task_id, Task.project_id == project_id))
    if not task:
        raise APIError(404, "not_found", "Объект не найден или недоступен.")
    return task


def update_task(db: Session, task: Task, version: int, **values):
    changed = db.execute(update(Task).where(Task.id == task.id, Task.version == version).values(**values, version=Task.version + 1, updated_at=now()))
    if changed.rowcount != 1:
        raise APIError(412, "version_conflict", "Объект изменился. Обновите данные и повторите действие.")
    db.flush()
    db.refresh(task)


@router.get("/projects/{project_id}/tasks", response_model=Page[TaskView], description="Sprint 1: paginated board; advanced filters and later transitions are deferred.")
def tasks(project_id: UUID, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db), page: PageNumber = 1, page_size: PageSize = 20):
    get_membership(db, project_id, auth.user.id)
    total = db.scalar(select(func.count()).select_from(Task).where(Task.project_id == project_id))
    rows = db.scalars(select(Task).where(Task.project_id == project_id).order_by(Task.created_at, Task.id).offset((page - 1) * page_size).limit(page_size)).all()
    clock = now()
    return Page(items=[task_view(task, clock) for task in rows], page=page, page_size=page_size, total=total)


@router.post("/projects/{project_id}/tasks", status_code=201, response_model=TaskView)
def create_task(project_id: UUID, body: TaskCreate, response: Response, auth: AuthContext = Depends(write_auth), db: Session = Depends(get_db)):
    project, member = get_membership(db, project_id, auth.user.id, lock=True)
    require_lead(member)
    require_open(project)
    task = Task(**body.model_dump(), project_id=project_id, created_by=member.id)
    db.add(task)
    db.flush()
    audit(db, project_id, member.id, "task.created", task.id)
    db.commit()
    response.headers["ETag"] = f'"{task.version}"'
    return task_view(task)


@router.get("/projects/{project_id}/tasks/{task_id}", response_model=TaskView)
def task_detail(project_id: UUID, task_id: UUID, response: Response, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    get_membership(db, project_id, auth.user.id)
    task = get_task(db, project_id, task_id)
    response.headers["ETag"] = f'"{task.version}"'
    return task_view(task)


@router.put("/projects/{project_id}/tasks/{task_id}/assignee", response_model=TaskView,
            description="US-12 slice: assignment/removal only; Telegram notification delivery (US-23) is deferred.")
def assign_task(project_id: UUID, task_id: UUID, body: AssigneeUpdate, request: Request, response: Response, auth: AuthContext = Depends(write_auth), db: Session = Depends(get_db)):
    project, member = get_membership(db, project_id, auth.user.id, lock=True)
    require_lead(member)
    require_open(project)
    task = get_task(db, project_id, task_id)
    version = expected_version(request)
    check_version(task.version, version)
    if task.status not in {"todo", "in_progress"}:
        raise APIError(409, "invalid_transition", "Назначение разрешено для задач к выполнению или в работе.")
    if body.assignee_member_id is not None:
        assignee = db.scalar(select(Member).where(Member.id == body.assignee_member_id, Member.project_id == project_id, Member.active.is_(True), Member.role.in_(["student", "lead"])))
        if not assignee:
            raise APIError(422, "invalid_assignee", "Выберите активного студента или тимлида этого проекта.")
    if task.assignee_member_id != body.assignee_member_id:
        update_task(db, task, version, assignee_member_id=body.assignee_member_id)
        audit(db, project_id, member.id, "task.assignee_changed", task.id)
    db.commit()
    response.headers["ETag"] = f'"{task.version}"'
    return task_view(task)


@router.post("/projects/{project_id}/tasks/{task_id}/start", response_model=TaskView)
def start_task(project_id: UUID, task_id: UUID, request: Request, response: Response, auth: AuthContext = Depends(write_auth), db: Session = Depends(get_db)):
    project, member = get_membership(db, project_id, auth.user.id, lock=True)
    require_open(project)
    task = get_task(db, project_id, task_id)
    if member.role not in {"student", "lead"} or task.assignee_member_id != member.id:
        raise APIError(403, "assignee_required", "Начать выполнение может только текущий исполнитель.")
    version = expected_version(request)
    check_version(task.version, version)
    if task.status != "todo":
        raise APIError(409, "invalid_transition", "Начать можно только задачу к выполнению.")
    update_task(db, task, version, status="in_progress")
    audit(db, project_id, member.id, "task.started", task.id)
    db.commit()
    response.headers["ETag"] = f'"{task.version}"'
    return task_view(task)
