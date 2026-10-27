"""GuardService 指令处理与状态管理单元测试."""

import pytest
from unittest.mock import MagicMock
from guard.config import AppConfig, WeChatConfig, ServerConfig, GuardConfig
from guard.service import GuardService


@pytest.fixture
def mock_service():
    cfg = AppConfig(
        wechat=WeChatConfig(
            corp_id="test_corp",
            agent_id=1000006,
            secret="test_secret",
            token="test_token",
            encoding_aes_key="test_aes_key"
        ),
        server=ServerConfig(port=8089),
        guard=GuardConfig(alert_cooldown_seconds=10)
    )
    mock_client = MagicMock()
    mock_client.send_dual_notification.return_value = True
    mock_client.send_markdown.return_value = True
    mock_client.send_text.return_value = True
    mock_client.send_textcard.return_value = True
    svc = GuardService(cfg, wechat_client=mock_client)
    return svc


def test_help_command(mock_service):
    reply = mock_service.handle_wechat_command("/help")
    assert "/status" in reply
    assert "/timer" in reply
    assert "/on" in reply
    assert "/off" in reply
    mock_service.client.send_dual_notification.assert_called()


def test_status_command(mock_service):
    mock_service.session_info["session_name"] = "测试会话-123"
    reply = mock_service.handle_wechat_command("/status")
    assert "测试会话-123" in reply
    assert "运行状态报告" in reply


def test_on_off_command(mock_service):
    # 关闭
    mock_service.handle_wechat_command("/off")
    assert mock_service.is_enabled is False
    assert mock_service.is_notifications_active() is False

    # 开启
    mock_service.handle_wechat_command("/on")
    assert mock_service.is_enabled is True
    assert mock_service.is_notifications_active() is True


def test_timer_command(mock_service):
    # 测试 /timer 30m
    reply = mock_service.handle_wechat_command("/timer 30m")
    assert "已开启免打扰" in reply
    assert mock_service.quiet_until > 0
    assert mock_service.is_notifications_active() is False


def test_option_selection_flow(mock_service):
    # 模拟接收到 ask 事件
    ask_event = {
        "id": "q_1001",
        "session_name": "数据库迁移决策",
        "questions": [{
            "question": "是否执行生产环境迁移？",
            "options": [
                {"label": "执行迁移 (Recommended)", "description": "带自动快照"},
                {"label": "取消并退出", "description": "终止操作"}
            ],
            "recommended": 0
        }],
        "collab_url": "https://my.omp.sh/#test-room"
    }
    mock_service.on_ask_event(ask_event)

    assert mock_service.pending_question is not None
    assert mock_service.pending_question["id"] == "q_1001"
    assert len(mock_service.pending_question["options"]) == 2

    # 用户在微信回复 "1"
    reply = mock_service.handle_wechat_command("1", from_user="bowen")
    assert "已确认选择" in reply
    assert "选项 1: 执行迁移" in reply

    # 检查待消费答复
    ans = mock_service.pop_pending_answer()
    assert ans is not None
    assert ans["choice_index"] == 1
    assert ans["choice_label"] == "执行迁移 (Recommended)"
    assert ans["from_user"] == "bowen"

    # 消费后为空
    assert mock_service.pop_pending_answer() is None


def test_error_alert_cooldown(mock_service):
    err_data = {
        "session_id": "sess_1",
        "session_name": "任务一",
        "error": "503 Service Unavailable",
        "detail": "Upstream model API timed out"
    }

    # 第一次报警成功发送
    ok1 = mock_service.on_error_event(err_data)
    assert ok1 is True

    # 立即再次报警，触发冷却被拦截
    ok2 = mock_service.on_error_event(err_data)
    assert ok2 is False

def test_custom_notification(mock_service):
    ok = mock_service.send_custom_notification(
        title="H100 显卡空闲通知",
        content="GPU 0 利用率降至 0%，显存已释放，可以开始训练。",
        host="Redhat -> H100",
        level="success"
    )
    assert ok is True
    mock_service.client.send_dual_notification.assert_called()
