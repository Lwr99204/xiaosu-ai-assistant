"""Mock 内部 API 集成测试（TDD §4.8 / 考题功能 3 工具 A）。

用 httpx.ASGITransport 直接驱动 ASGI 应用，而不是 fastapi.testclient.TestClient。
TestClient 靠 anyio 的阻塞 portal 另起一个事件循环，请求在别的线程里跑，
中间件绑定的 ContextVar 与测试线程不共享；ASGITransport 在当前事件循环内跑完
整个请求，bind/unbind 与断言处在同一上下文，行为更可控。

关于禁网（TDD「强制 mock + pytest-socket 禁网」）
-----------------------------------------------
pyproject 的 addopts 里有全局 --disable-socket，它把 socket.socket 的**构造**
换成直接抛异常的替身。asyncio 事件循环在 Windows 上要用 socketpair() 建
self-pipe，于是任何 async 测试都会在 fixture 阶段就起不来。本模块因此挂
allow_hosts("127.0.0.1")：pytest-socket 在这个模式下不再替换 socket 构造，
改为只守 connect()，白名单外的任何主机（真实 LLM、embedding 服务、外网）一律
抛 SocketConnectBlockedError。「测试打不到外网」这条约束依然由机制保证，
放宽的只是「不能建 socket」到「只能连本机回环」。

覆盖三件单元测试测不到的事：
1. 全局异常兜底确实生效（404 / 422 / 500 都不裸奔，且都带 request_id）。
2. request_id 在「请求头 -> ContextVar -> 响应体 -> 响应头」之间一致。
3. 业务口径按 data/mock 的真实数据校验：日期闭区间、姓名模糊、销售额只计 paid。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.agent.events import AgentEvent, AgentReply
from app.api.middleware import REQUEST_ID_HEADER, UNEXPECTED_ERROR_CODE
from app.config import REPO_ROOT, Settings
from app.db.engine import init_db
from app.db.entities import ConversationRecord, ToolCallEntry
from app.db.repositories import ConversationRepo
from app.main import APP_VERSION, create_app
from app.mock_api.router import get_store

MOCK_DATA_DIR = REPO_ROOT / "data" / "mock"

#: 只放行回环地址供事件循环 self-pipe 使用；对外连接仍被 pytest-socket 阻断
pytestmark = pytest.mark.allow_hosts("127.0.0.1")


def _settings(tmp_path: Path) -> Settings:
    """隔离配置：不读仓库 .env、日志写临时目录、假 API Key。"""
    return Settings(
        _env_file=None,
        llm_api_key="sk-test-1",
        log_dir=tmp_path / "logs",
        storage_dir=tmp_path / "storage",
        mock_data_dir=MOCK_DATA_DIR,
    )


@pytest.fixture()
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(_settings(tmp_path))),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


# ── 健康检查 ──────────────────────────────────────────────────────


async def test_health_reports_unwired_components(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["version"] == APP_VERSION
    # M1 阶段 llm/embed/vector_store/db 尚未接线，属预期状态而非故障
    assert body["llm"]["status"] == "ok"
    assert body["embed"]["status"] == "not_wired"
    assert body["vector_store"]["status"] == "not_wired"
    assert body["db"]["status"] == "not_wired"
    assert body["im"]["wecom"]["enabled"] is False
    assert body["im"]["wecom"]["connected"] is False


async def test_admin_log_list_detail_and_stats(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.sqlite_path = tmp_path / "storage" / "logs.db"
    init_db(settings.sqlite_path)
    repo = ConversationRepo(settings.sqlite_path)
    conversation_id = repo.insert(
        ConversationRecord(
            request_id="req-log-1",
            session_key="wecom:group:user",
            platform="wecom",
            user_id="user-1",
            user_name="测试员工",
            question="年假几天？",
            answer="每年十天[1]",
            citations_json=(
                '[{"ref_no":1,"filename":"员工手册.md","snippet":"每年十天",'
                '"char_start":10,"char_end":14,"score":0.8}]'
            ),
            model="mock-model",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            cost_cny=0.12,
            latency_ms=345,
            created_at="2026-11-24T14:00:00+00:00",
        )
    )
    repo.insert_tool_calls(
        conversation_id,
        [ToolCallEntry(1, "search_knowledge_base", '{"query":"年假"}', "命中员工手册", True)],
    )
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as test_client:
        listing = await test_client.get("/api/logs?platform=wecom&has_tool_call=true")
        assert listing.status_code == 200
        assert listing.json()["items"][0]["tool_count"] == 1

        detail = await test_client.get(f"/api/logs/{conversation_id}")
        assert detail.status_code == 200
        assert detail.json()["tool_calls"][0]["tool_name"] == "search_knowledge_base"
        assert detail.json()["usage"]["total_tokens"] == 120
        assert detail.json()["citations"][0]["char_start"] == 10

        stats = await test_client.get("/api/logs/stats")
        assert stats.status_code == 200
        assert stats.json()["total"] == {"count": 1, "tokens": 120, "cost_cny": 0.12}


async def test_model_settings_switch_without_exposing_credentials(
    client: httpx.AsyncClient,
) -> None:
    current = await client.get("/api/settings/model")
    assert current.status_code == 200
    assert current.json()["available"]
    assert "key" not in current.text.lower()

    switched = await client.put("/api/settings/model", json={"model_id": "deepseek-v4-pro"})
    assert switched.status_code == 200
    assert switched.json() == {"ok": True, "current": "deepseek-v4-pro"}
    assert (await client.get("/api/settings/model")).json()["current"] == "deepseek-v4-pro"

    rejected = await client.put("/api/settings/model", json={"model_id": "unknown-model"})
    assert rejected.status_code == 422


async def test_chat_stream_uses_shared_agent_service(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))

    class FakeAgentService:
        async def ask(self, question, context):
            assert question == "员工每年有几天年假？"
            assert context.platform == "web"
            yield AgentEvent(kind="delta", text="每年 10 天 [1]。")
            yield AgentEvent(
                kind="done",
                reply=AgentReply(answer="每年 10 天 [1]。", status="ok"),
            )

    app.state.agent_service = FakeAgentService()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as test_client:
        response = await test_client.post(
            "/api/chat/stream",
            json={"question": "员工每年有几天年假？", "session_id": "test-session"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"type": "delta"' in response.text
    assert '"type": "done"' in response.text
    assert "每年 10 天 [1]。" in response.text


# ── 员工查询（考题 Q4：员工 001 是哪个部门）───────────────────────


async def test_read_employee_by_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/employee/001")
    body = response.json()

    assert response.status_code == 200
    assert body["name"] == "张三"
    assert body["dept"] == "技术部"
    assert body["level"] == "P6"
    assert body["status"] == "active"


async def test_read_missing_employee_returns_unified_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/employee/999")
    body = response.json()

    assert response.status_code == 404
    assert body["code"] == "NOT_FOUND"
    # detail 与 message 同值：考题要「员工 999 不存在」这种可读结论，TDD 要统一错误体
    assert body["detail"] == "员工 999 不存在"
    assert body["message"] == "员工 999 不存在"
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]


async def test_list_employees_filters_by_dept(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/employee", params={"dept": "技术部"})).json()

    assert body["count"] == 4
    assert {e["id"] for e in body["employees"]} == {"001", "002", "006", "010"}


async def test_list_employees_matches_name_substring(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/employee", params={"name": "张"})).json()

    assert body["count"] == 3
    assert {e["id"] for e in body["employees"]} == {"001", "006", "007"}


# ── 考勤查询（考题 Q7：他上周来上班几天）─────────────────────────


async def test_attendance_date_range_is_inclusive(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/attendance",
        params={"emp_id": "001", "start_date": "2026-11-16", "end_date": "2026-11-20"},
    )
    body = response.json()

    assert response.status_code == 200
    # 闭区间：11-16 与 11-20 两端都要计入
    assert body["count"] == 5
    assert {r["date"] for r in body["records"]} == {
        "2026-11-16",
        "2026-11-17",
        "2026-11-18",
        "2026-11-19",
        "2026-11-20",
    }


async def test_attendance_single_day_range(client: httpx.AsyncClient) -> None:
    body = (
        await client.get(
            "/api/attendance",
            params={"emp_id": "002", "start_date": "2026-11-20", "end_date": "2026-11-20"},
        )
    ).json()

    assert body["count"] == 1
    assert body["records"][0]["status"] == "absent"


async def test_attendance_for_missing_employee_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/attendance",
        params={"emp_id": "999", "start_date": "2026-11-16", "end_date": "2026-11-20"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "员工 999 不存在"


# ── 订单查询（考题 Q5：上周一共多少订单 / 销售额）────────────────


async def test_orders_returns_items_and_summary(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/orders", params={"start_date": "2026-11-16", "end_date": "2026-11-22"}
    )
    body = response.json()
    summary = body["summary"]

    assert response.status_code == 200
    # 默认返回明细，让模型自己走 calculator 汇总（CURRENT_PLAN 修正 3）
    assert len(body["orders"]) == summary["count"] == 7
    assert body["orders"][0]["items"]
    # 销售额口径只计 paid：15600 + 3000 + 98000 + 4200 + 600 = 121400
    # 退款 790（SO-2026-1118）与取消 1580（SO-2026-1121）单列，不计入销售额
    assert Decimal(summary["total_amount"]) == Decimal("121400.00")
    assert Decimal(summary["refunded_amount"]) == Decimal("790.00")
    assert Decimal(summary["cancelled_amount"]) == Decimal("1580.00")


async def test_orders_filter_by_status(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/orders", params={"status": "refunded"})).json()

    assert body["summary"]["count"] == 4
    assert all(order["status"] == "refunded" for order in body["orders"])


# ── 参数校验与异常兜底（FR-3-07 / FR-6-07）───────────────────────


async def test_reversed_date_range_returns_field_level_422(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/attendance",
        params={"emp_id": "001", "start_date": "2026-11-20", "end_date": "2026-11-16"},
    )
    body = response.json()

    assert response.status_code == 422
    assert body["code"] == "VALIDATION_ERROR"
    # 字段级说明是给模型自纠用的（FR-3-07）
    assert body["context"]["errors"][0]["field"] == "start_date"


async def test_malformed_date_returns_422_not_500(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/attendance",
        params={"emp_id": "001", "start_date": "上周", "end_date": "2026-11-20"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_unexpected_error_is_wrapped_as_internal_error(tmp_path: Path) -> None:
    """任何未捕获异常都要变成统一 500，绝不把栈回给调用方（FR-6-07）。"""
    app = create_app(_settings(tmp_path))

    def explode() -> None:
        raise RuntimeError("boom")

    app.dependency_overrides[get_store] = explode
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as raw_client:
        response = await raw_client.get("/api/employee/001")

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == UNEXPECTED_ERROR_CODE
    assert "boom" not in body["message"]
    assert body["request_id"]


# ── request_id 透传（FR-6-06）────────────────────────────────────


async def test_caller_supplied_request_id_is_echoed_back(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/employee/001", headers={REQUEST_ID_HEADER: "trace-abc-123"})

    assert response.headers[REQUEST_ID_HEADER] == "trace-abc-123"


async def test_generated_request_id_reaches_error_body(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/employee/999")

    header_value = response.headers[REQUEST_ID_HEADER]
    assert header_value
    assert response.json()["request_id"] == header_value


async def test_log_injection_in_request_id_is_rejected(client: httpx.AsyncClient) -> None:
    """带换行的 X-Request-ID 会被丢弃并重新生成，防止伪造日志行。"""
    response = await client.get("/api/employee/001", headers={REQUEST_ID_HEADER: "evil\nfake"})

    assert "\n" not in response.headers[REQUEST_ID_HEADER]
