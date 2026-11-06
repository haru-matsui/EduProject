from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.models import AuditEvent, Invitation, Member, Project, Task
from .helpers import API, accept, assign, create_project, create_task, invite, project_body, task_body, task_path


def run_shared_flow(env):
    lead, lead_user = env.identity("Тимлид")
    student, student_user = env.identity("Студент")
    outsider, _ = env.identity("Посторонний")
    assert lead.get(API + "/projects").json()["items"] == []
    project = create_project(lead)
    project_id = project["id"]
    assert project["current_role"] == "lead"
    assert student.get(f"{API}/projects/{project_id}").status_code == 404
    token, invitation = invite(lead, project_id)
    anonymous = env.client()
    preview = anonymous.get(f"{API}/invitations/{token}")
    assert preview.json()["requires_login"] is True
    assert set(preview.json()) == {"project_title", "role", "expires_at", "requires_login"}
    member = accept(student, token)
    assert member["role"] == "student"
    assert accept(student, token)["id"] == member["id"]
    assert student.get(f"{API}/projects/{project_id}").json()["current_role"] == "student"
    task = create_task(lead, project_id)
    task_id = task["id"]
    assert task["status"] == "todo" and task["assignee_member_id"] is None
    assert task["is_due_soon"] is True and task["is_overdue"] is False
    assigned = assign(lead, project_id, task_id, member["id"])
    assert assigned.status_code == 200, assigned.text
    assert assigned.headers["etag"] == '"2"'
    assert lead.post(task_path(project_id, task_id) + "/start", headers={"If-Match": '"2"'}).status_code == 403
    started = student.post(task_path(project_id, task_id) + "/start", headers={"If-Match": '"2"'})
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress"
    assert started.headers["etag"] == '"3"'
    assert student.post(task_path(project_id, task_id) + "/start", headers={"If-Match": '"3"'}).status_code == 409
    assert student.post(task_path(project_id, task_id) + "/start", headers={"If-Match": '"2"'}).status_code == 412
    for path in (f"/projects/{project_id}", f"/projects/{project_id}/members", f"/projects/{project_id}/tasks", f"/projects/{project_id}/invitations", task_path(project_id, task_id)[len(API):]):
        assert outsider.get(API + path).status_code == 404
    # A fresh app instance uses the same persisted rows and server session.
    fresh = TestClient(create_app(env.settings, env.engine), base_url="https://testserver")
    fresh.cookies.update(student.cookies)
    board = fresh.get(f"{API}/projects/{project_id}/tasks").json()
    assert board["total"] == 1 and board["items"][0]["status"] == "in_progress"
    with env.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Member).where(Member.project_id == UUID(project_id))) == 2
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.project_id == UUID(project_id))) == 6


def test_shared_flow_persistence_and_object_isolation(env):
    run_shared_flow(env)


@pytest.mark.postgres
def test_postgres_shared_flow(postgres_env):
    run_shared_flow(postgres_env)


@pytest.mark.parametrize("field,value", [("title", "  "), ("description", ""), ("goal", "\n"), ("expected_result", ""), ("due_at", "2026-10-28T10:00:00"), ("due_at", 1700000000), ("title", "x" * 201)])
def test_project_validation_no_side_effects(env, field, value):
    lead, _ = env.identity()
    response = lead.post(API + "/projects", json=project_body(**{field: value}))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert lead.get(API + "/projects").json()["total"] == 0


@pytest.mark.parametrize("due_at", ["1700000000", "1700000000.5", "-1700000000", "20261201"])
def test_numeric_string_deadlines_rejected_without_creating_rows(env, due_at):
    lead, _ = env.identity()
    response = lead.post(API + "/projects", json=project_body(due_at=due_at))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert lead.get(API + "/projects").json()["total"] == 0

    project = create_project(lead)
    path = f"{API}/projects/{project['id']}/tasks"
    response = lead.post(path, json=task_body(due_at=due_at))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert lead.get(path).json()["total"] == 0


@pytest.mark.parametrize("due_at", ["2026-12-01T12:30:00Z", "2026-12-01T17:30:00+05:00"])
def test_rfc3339_deadlines_preserve_instant_for_project_and_task(env, due_at):
    lead, _ = env.identity()
    project = create_project(lead, due_at=due_at)
    task = create_task(lead, project["id"], due_at=due_at)
    expected = datetime(2026, 12, 1, 12, 30, tzinfo=UTC)
    assert datetime.fromisoformat(project["due_at"]) == expected
    assert datetime.fromisoformat(task["due_at"]) == expected


def test_trim_and_reject_identity_role_and_unknown_fields(env):
    lead, _ = env.identity()
    project = create_project(lead, title="  Trim title  ")
    assert project["title"] == "Trim title"
    response = lead.post(API + "/projects", json=project_body(created_by=str(uuid4()), current_role="teacher"))
    assert response.status_code == 422
    for payload in ({"role": "teacher"}, {"role": "lead"}):
        assert lead.post(f"{API}/projects/{project['id']}/invitations", json=payload).status_code == 422


def test_local_roles_and_member_permissions(env):
    lead, _ = env.identity()
    student, _ = env.identity()
    project = create_project(lead)
    token, _ = invite(lead, project["id"])
    member = accept(student, token)
    task = create_task(lead, project["id"])
    assert student.post(f"{API}/projects/{project['id']}/tasks", json=task_body()).status_code == 403
    assert student.post(f"{API}/projects/{project['id']}/invitations", json={"role": "student"}).status_code == 403
    assert student.get(f"{API}/projects/{project['id']}/invitations").status_code == 403
    assert assign(student, project["id"], task["id"], member["id"]).status_code == 403
    own = create_project(student)
    assert own["current_role"] == "lead"
    assert student.get(f"{API}/projects/{project['id']}").json()["current_role"] == "student"


def test_invitation_multiuser_replay_role_conflict_and_hash_storage(env):
    lead, _ = env.identity()
    project = create_project(lead)
    token, invitation = invite(lead, project["id"])
    first, _ = env.identity("First")
    second, _ = env.identity("Second")
    one, two = accept(first, token), accept(second, token)
    assert one["id"] != two["id"]
    assert accept(first, token)["id"] == one["id"]
    assert lead.post(f"{API}/invitations/{token}/accept").status_code == 409
    listed = lead.get(f"{API}/projects/{project['id']}/invitations").json()
    assert token not in str(listed) and "token_hash" not in str(listed)
    with env.app.state.session_factory() as db:
        row = db.get(Invitation, UUID(invitation["id"]))
        assert row.token_hash != token and len(row.token_hash) == 64
        assert timedelta(days=7) - timedelta(seconds=5) <= row.expires_at - row.created_at <= timedelta(days=7)


def test_invitation_revocation_expiration_and_project_lock(env):
    lead, _ = env.identity()
    student, _ = env.identity()
    project = create_project(lead)
    token, invitation = invite(lead, project["id"])
    route = f"{API}/projects/{project['id']}/invitations/{invitation['id']}"
    assert lead.delete(route).status_code == 204
    assert lead.delete(route).status_code == 204
    assert student.post(f"{API}/invitations/{token}/accept").status_code == 410
    assert student.get(f"{API}/invitations/{token}").status_code == 410
    token, invitation = invite(lead, project["id"])
    with env.app.state.session_factory() as db:
        db.get(Invitation, UUID(invitation["id"])).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert student.post(f"{API}/invitations/{token}/accept").status_code == 410
    token, _ = invite(lead, project["id"])
    with env.app.state.session_factory() as db:
        db.get(Project, UUID(project["id"])).status = "final_review"
        db.commit()
    assert student.post(f"{API}/invitations/{token}/accept").status_code == 409
    assert lead.post(f"{API}/projects/{project['id']}/tasks", json=task_body()).status_code == 409
    assert lead.post(f"{API}/projects/{project['id']}/invitations", json={"role": "student"}).status_code == 409


def test_inactive_membership_restore_only_same_role_and_no_access(env):
    lead, _ = env.identity()
    student, _ = env.identity()
    project = create_project(lead)
    token, _ = invite(lead, project["id"])
    member = accept(student, token)
    with env.app.state.session_factory() as db:
        row = db.get(Member, UUID(member["id"]))
        row.active = False
        row.left_at = datetime.now(UTC)
        db.commit()
    assert student.get(f"{API}/projects/{project['id']}").status_code == 404
    assert accept(student, token)["id"] == member["id"]
    with env.app.state.session_factory() as db:
        row = db.get(Member, UUID(member["id"]))
        row.active = False
        row.role = "teacher"
        db.commit()
    assert student.post(f"{API}/invitations/{token}/accept").status_code == 409


def test_assignment_replacement_removal_foreign_inactive_teacher_and_etag(env):
    lead, _ = env.identity()
    student, _ = env.identity()
    other, _ = env.identity()
    project = create_project(lead)
    other_project = create_project(other)
    token, _ = invite(lead, project["id"])
    student_member = accept(student, token)
    lead_member = lead.get(f"{API}/projects/{project['id']}/members").json()["items"][0]
    foreign_member = other.get(f"{API}/projects/{other_project['id']}/members").json()["items"][0]
    task = create_task(lead, project["id"])
    path = task_path(project["id"], task["id"]) + "/assignee"
    assert lead.put(path, json={"assignee_member_id": student_member["id"]}).status_code == 428
    for value in ("1", "*", 'W/"1"', '"0"', '"' + "9" * 5000 + '"'):
        assert lead.put(path, json={"assignee_member_id": None}, headers={"If-Match": value}).status_code == 422
    assert assign(lead, project["id"], task["id"], foreign_member["id"]).status_code == 422
    with env.app.state.session_factory() as db:
        row = db.get(Member, UUID(student_member["id"]))
        row.active = False
        db.commit()
    assert assign(lead, project["id"], task["id"], student_member["id"]).status_code == 422
    with env.app.state.session_factory() as db:
        row = db.get(Member, UUID(student_member["id"]))
        row.active, row.role = True, "teacher"
        db.commit()
    assert assign(lead, project["id"], task["id"], student_member["id"]).status_code == 422
    with env.app.state.session_factory() as db:
        db.get(Member, UUID(student_member["id"])).role = "student"
        db.commit()
    assert assign(lead, project["id"], task["id"], student_member["id"]).status_code == 200
    assert assign(lead, project["id"], task["id"], lead_member["id"]).status_code == 412
    assert assign(lead, project["id"], task["id"], lead_member["id"], 2).status_code == 200
    removed = assign(lead, project["id"], task["id"], None, 3)
    assert removed.status_code == 200 and removed.json()["assignee_member_id"] is None
    assert student.post(task_path(project["id"], task["id"]) + "/start", headers={"If-Match": '"4"'}).status_code == 403


def test_pagination_completeness_and_past_due(env):
    lead, _ = env.identity()
    project = create_project(lead)
    for index in range(5):
        create_task(lead, project["id"], title=f"Task {index}", due_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat())
    seen = set()
    for page in range(1, 4):
        response = lead.get(f"{API}/projects/{project['id']}/tasks", params={"page": page, "page_size": 2})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 5
        for task in body["items"]:
            assert task["id"] not in seen
            seen.add(task["id"])
            assert task["is_overdue"] is True and task["is_due_soon"] is False
    assert len(seen) == 5
    assert lead.get(f"{API}/projects/{project['id']}/tasks?page_size=101").status_code == 422


def test_task_cannot_cross_project_path_and_invalid_transition(env):
    lead, _ = env.identity()
    one, two = create_project(lead), create_project(lead)
    task = create_task(lead, one["id"])
    assert lead.get(task_path(two["id"], task["id"])).status_code == 404
    assert assign(lead, two["id"], task["id"], None).status_code == 404
    with env.app.state.session_factory() as db:
        db.get(Task, UUID(task["id"])).status = "done"
        db.commit()
    assert assign(lead, one["id"], task["id"], None).status_code == 409
    assert lead.get(task_path(one["id"], task["id"])).json()["is_overdue"] is False
