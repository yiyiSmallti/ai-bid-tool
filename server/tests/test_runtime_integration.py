import asyncio
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import httpx
import pytest


@pytest.fixture(scope="session")
def real_queue_schema(admin_engine):
    subprocess.run(
        [sys.executable, "-m", "app.admin", "init-db"], check=True, stdout=subprocess.DEVNULL
    )


async def execute_cli(arguments: list[str], environment: dict) -> tuple[int, dict]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bid_cli.main",
        *arguments,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode is not None
    try:
        body = json.loads(stdout)
    except ValueError as exc:
        raise AssertionError("CLI did not emit one JSON object: " + stderr.decode()[:500]) from exc
    return process.returncode, body


async def test_real_background_worker_and_both_cli_modes(
    database, real_queue_schema, tmp_path, pdf_bytes
):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = {**os.environ, "BID_DATA_DIR": str(tmp_path / "files")}
    pdf = tmp_path / "fixture.pdf"
    pdf.write_bytes(pdf_bytes)
    worker_log, api_log = (tmp_path / "worker.log").open("w"), (tmp_path / "api.log").open("w")
    worker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.jobs.worker",
        env=environment,
        stdout=worker_log,
        stderr=worker_log,
    )
    server = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "app.api.main:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
        "--no-access-log",
        env=environment,
        stdout=api_log,
        stderr=api_log,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
            for _ in range(100):
                try:
                    if (await http.get("/health")).status_code == 200:
                        break
                except httpx.NetworkError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("Local test API did not start")
        receipts = []
        for mode in ("local", "remote"):
            base = ["--mode", mode, "--server", f"http://127.0.0.1:{port}"]
            status, task = await execute_cli(
                [
                    *base,
                    "task",
                    "create",
                    "--name",
                    "Synthetic CLI task",
                    "--tender",
                    str(pdf),
                    "--json",
                ],
                environment,
            )
            assert status == 0, task
            document = task["data"]["document_id"]
            status, parsed = await execute_cli(
                [
                    *base,
                    "tender",
                    "parse",
                    "--document",
                    document,
                    "--wait",
                    "--timeout",
                    "20",
                    "--json",
                ],
                environment,
            )
            assert status == 0, parsed
            status, listing = await execute_cli([*base, "task", "list", "--json"], environment)
            assert status == 0 and any(row["id"] == task["data"]["id"] for row in listing["items"])
            receipts.append(
                {
                    "mode": mode,
                    "task_id": task["data"]["id"],
                    "document_id": document,
                    "parse": parsed,
                }
            )
        base = ["--mode", "remote", "--server", f"http://127.0.0.1:{port}"]
        key_file = tmp_path / "access-key"
        key_file.touch(mode=0o600)
        key_file.write_text(environment["BID_ACCESS_KEY"])
        file_environment = {
            key: value for key, value in environment.items() if key != "BID_ACCESS_KEY"
        }
        file_environment["BID_ACCESS_KEY_FILE"] = str(key_file)
        try:
            status, listed = await execute_cli([*base, "task", "list", "--json"], file_environment)
            assert status == 0 and listed["items"]
            key_file.chmod(0o644)
            status, denied = await execute_cli([*base, "task", "list", "--json"], file_environment)
            assert status == 2 and denied["data"]["error"]["code"] == "invalid_access_key_file"
            priority_environment = {
                **file_environment,
                "BID_ACCESS_KEY": environment["BID_ACCESS_KEY"],
            }
            status, listed = await execute_cli(
                [*base, "task", "list", "--json"], priority_environment
            )
            assert status == 0
            key_file.chmod(0o600)
            link = tmp_path / "key-link"
            link.symlink_to(key_file)
            status, denied = await execute_cli(
                [*base, "task", "list", "--json"],
                {**file_environment, "BID_ACCESS_KEY_FILE": str(link)},
            )
            assert status == 2 and denied["data"]["error"]["code"] == "invalid_access_key_file"
            link.unlink()
        finally:
            key_file.unlink()
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
            minted = await http.post(
                "/tokens",
                headers={"Authorization": "Bearer " + environment["BID_ACCESS_KEY"]},
                json={
                    "name": "Synthetic CLI token",
                    "scopes": ["task:read"],
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            )
        assert minted.status_code == 200
        token_environment = {
            key: value for key, value in file_environment.items() if key != "BID_ACCESS_KEY_FILE"
        }
        token_environment["BID_TOKEN"] = minted.json()["data"]["token"]
        status, listed = await execute_cli([*base, "task", "list", "--json"], token_environment)
        assert status == 0 and listed["items"]
        receipts.append(
            {
                "cli_auth": ["environment", "owner_only_file", "token"],
                "rejected": ["group_readable_file", "symlink"],
            }
        )
        (tmp_path / "runtime-receipts.json").write_text(json.dumps(receipts, indent=2))
    finally:
        for process in (server, worker):
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()
        worker_log.close()
        api_log.close()


async def test_unexpected_server_error_is_a_result_in_both_modes(
    database, real_queue_schema, tmp_path, monkeypatch, capsys
):
    import app.api.main as api_main
    import app.services.documents as documents
    from bid_cli.main import main

    async def failing_task(*args, **kwargs):
        raise RuntimeError("synthetic internal detail")

    monkeypatch.setattr(documents, "create_task", failing_task)
    monkeypatch.setenv("BID_DATA_DIR", str(tmp_path / "files"))
    application = api_main.create_app()
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
            base_url="http://test",
        ) as http,
    ):
        response = await http.post(
            "/tasks",
            headers={"Authorization": "Bearer " + os.environ["BID_ACCESS_KEY"]},
            json={"name": "Synthetic failure"},
        )
    remote = response.json()
    assert response.status_code == 500 and "synthetic internal detail" not in response.text
    assert set(remote) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert remote["data"]["error"] == {
        "code": "internal_error",
        "message": "Unexpected server error",
        "exit_code": 4,
    }

    arguments = ["--mode", "local", "task", "create", "--name", "Synthetic failure", "--json"]
    with pytest.raises(SystemExit) as exit_info:
        await asyncio.to_thread(main, arguments)
    captured = capsys.readouterr()
    local = json.loads(captured.out)
    assert exit_info.value.code == 4 and "Traceback" not in captured.err
    assert "synthetic internal detail" not in captured.out + captured.err
    assert local["ok"] is False and local["command"] == "task create"
    assert local["data"]["error"] == remote["data"]["error"]
