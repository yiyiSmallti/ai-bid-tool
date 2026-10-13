"""Task creation and explicit migration-owner setup for API acceptance tests."""

from task_fixtures import owner_session


async def new_task(api, headers):
    response = await api.post("/tasks", headers=headers, json={"name": "Synthetic task"})
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def fixture_session():
    return owner_session()
