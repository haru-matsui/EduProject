from datetime import UTC, datetime, timedelta

API = "/api/v1"


def project_body(**changes):
    return {"title": "Командный проект", "description": "Описание", "goal": "Учебная цель", "expected_result": "Прототип", "due_at": (datetime.now(UTC) + timedelta(days=5)).isoformat(), **changes}


def task_body(**changes):
    return {"title": "Первая задача", "description": "Реализовать сценарий", "expected_result": "Рабочий результат", "due_at": (datetime.now(UTC) + timedelta(hours=12)).isoformat(), "priority": "high", **changes}


def create_project(client, **changes):
    response = client.post(API + "/projects", json=project_body(**changes))
    assert response.status_code == 201, response.text
    assert response.headers["etag"] == '"1"'
    return response.json()


def invite(client, project_id):
    response = client.post(f"{API}/projects/{project_id}/invitations", json={"role": "student"})
    assert response.status_code == 201, response.text
    body = response.json()
    return body["invite_url"].rsplit("/", 1)[1], body["invitation"]


def accept(client, token):
    response = client.post(f"{API}/invitations/{token}/accept")
    assert response.status_code == 200, response.text
    return response.json()


def create_task(client, project_id, **changes):
    response = client.post(f"{API}/projects/{project_id}/tasks", json=task_body(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def task_path(project_id, task_id):
    return f"{API}/projects/{project_id}/tasks/{task_id}"


def assign(client, project_id, task_id, member_id, version=1):
    return client.put(task_path(project_id, task_id) + "/assignee", json={"assignee_member_id": member_id}, headers={"If-Match": f'"{version}"'})
