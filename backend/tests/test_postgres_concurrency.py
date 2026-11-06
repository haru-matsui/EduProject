from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.models import Member
from .helpers import API, accept, assign, create_project, create_task, invite

pytestmark = pytest.mark.postgres


def test_concurrent_acceptance_is_idempotent_and_cas_rejects_lost_update(postgres_env):
    env = postgres_env
    lead, _ = env.identity("Lead")
    student, student_user = env.identity("Student")
    project = create_project(lead)
    token, _ = invite(lead, project["id"])
    barrier = Barrier(2)

    def accept_once():
        client = env.client()
        client.cookies.update(student.cookies)
        client.headers.update(student.headers)
        barrier.wait(timeout=10)
        return client.post(f"{API}/invitations/{token}/accept")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(accept_once) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert [response.status_code for response in results] == [200, 200]
    assert results[0].json()["id"] == results[1].json()["id"]
    with env.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Member).where(Member.project_id == UUID(project["id"]), Member.user_id == student_user.id)) == 1
    student_id = results[0].json()["id"]
    lead_id = next(row["id"] for row in lead.get(f"{API}/projects/{project['id']}/members").json()["items"] if row["role"] == "lead")
    task = create_task(lead, project["id"])
    barrier = Barrier(2)

    def assign_once(member_id):
        client = env.client()
        client.cookies.update(lead.cookies)
        client.headers.update(lead.headers)
        barrier.wait(timeout=10)
        return assign(client, project["id"], task["id"], member_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(assign_once, member_id) for member_id in (student_id, lead_id)]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(response.status_code for response in results) == [200, 412]
    saved = lead.get(f"{API}/projects/{project['id']}/tasks/{task['id']}").json()
    assert saved["version"] == 2
    assert saved["assignee_member_id"] in {student_id, lead_id}
