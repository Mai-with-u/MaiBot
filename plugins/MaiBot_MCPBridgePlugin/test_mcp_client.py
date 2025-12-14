#!/usr/bin/env python3
"""
MCP 客户端测试脚本
测试 mcp_client.py 的基本功能
"""

import asyncio
import sys
import os

# 确保当前目录在 path 中
sys.path.insert(0, os.path.dirname(__file__))

from mcp_client import (
    MCPClientManager,
    MCPServerConfig,
    TransportType,
    ToolCallStats,
    ServerStats,
)


async def test_stats():
    """测试统计类"""
    print("\n=== 测试统计类 ===")

    # 测试 ToolCallStats
    stats = ToolCallStats(tool_key="test_tool")
    stats.record_call(True, 100.0)
    stats.record_call(True, 200.0)
    stats.record_call(False, 50.0, "timeout")

    assert stats.total_calls == 3
    assert stats.success_calls == 2
    assert stats.failed_calls == 1
    assert stats.success_rate == (2 / 3) * 100
    assert stats.avg_duration_ms == 150.0
    assert stats.last_error == "timeout"

    print(f"✅ ToolCallStats: {stats.to_dict()}")

    # 测试 ServerStats
    server_stats = ServerStats(server_name="test_server")
    server_stats.record_connect()
    server_stats.record_heartbeat()
    server_stats.record_disconnect()
    server_stats.record_failure()
    server_stats.record_failure()

    assert server_stats.connect_count == 1
    assert server_stats.disconnect_count == 1
    assert server_stats.consecutive_failures == 2

    print(f"✅ ServerStats: {server_stats.to_dict()}")

    return True


async def test_manager_basic():
    """测试管理器基本功能"""
    print("\n=== 测试管理器基本功能 ===")

    # 创建新的管理器实例（绕过单例）
    manager = MCPClientManager.__new__(MCPClientManager)
    manager._initialized = False
    manager.__init__()

    # 配置
    manager.configure(
        {
            "tool_prefix": "mcp",
            "call_timeout": 30.0,
            "retry_attempts": 1,
            "retry_interval": 1.0,
            "heartbeat_enabled": False,
        }
    )

    # 测试状态
    status = manager.get_status()
    assert status["total_servers"] == 0
    assert status["connected_servers"] == 0
    print(f"✅ 初始状态: {status}")

    # 测试添加禁用的服务器
    config = MCPServerConfig(
        name="disabled_server", enabled=False, transport=TransportType.HTTP, url="https://example.com/mcp"
    )
    result = await manager.add_server(config)
    assert result == True
    assert "disabled_server" in manager._clients
    assert manager._clients["disabled_server"].is_connected == False
    print("✅ 添加禁用服务器成功")

    # 测试重复添加
    result = await manager.add_server(config)
    assert result == False
    print("✅ 重复添加被拒绝")

    # 测试移除
    result = await manager.remove_server("disabled_server")
    assert result == True
    assert "disabled_server" not in manager._clients
    print("✅ 移除服务器成功")

    # 清理
    await manager.shutdown()
    print("✅ 管理器关闭成功")

    return True


async def test_http_connection():
    """测试 HTTP 连接（使用真实的 MCP 服务器）"""
    print("\n=== 测试 HTTP 连接 ===")

    # 创建新的管理器实例
    manager = MCPClientManager.__new__(MCPClientManager)
    manager._initialized = False
    manager.__init__()

    manager.configure(
        {
            "tool_prefix": "mcp",
            "call_timeout": 30.0,
            "retry_attempts": 2,
            "retry_interval": 2.0,
            "heartbeat_enabled": False,
        }
    )

    # 使用 HowToCook MCP 服务器测试
    config = MCPServerConfig(
        name="howtocook",
        enabled=True,
        transport=TransportType.HTTP,
        url="https://mcp.api-inference.modelscope.net/c9b55951d4ed47/mcp",
    )

    print(f"正在连接 {config.url} ...")
    result = await manager.add_server(config)

    if result:
        print("✅ 连接成功!")

        # 检查工具
        tools = manager.all_tools
        print(f"✅ 发现 {len(tools)} 个工具:")
        for tool_key in tools:
            print(f"   - {tool_key}")

        # 测试心跳
        client = manager._clients["howtocook"]
        healthy = await client.check_health()
        print(f"✅ 心跳检测: {'健康' if healthy else '异常'}")

        # 测试工具调用
        if "mcp_howtocook_whatToEat" in tools:
            print("\n正在调用 whatToEat 工具...")
            call_result = await manager.call_tool("mcp_howtocook_whatToEat", {})
            if call_result.success:
                print(f"✅ 工具调用成功 (耗时: {call_result.duration_ms:.0f}ms)")
                print(
                    f"   结果: {call_result.content[:200]}..."
                    if len(str(call_result.content)) > 200
                    else f"   结果: {call_result.content}"
                )
            else:
                print(f"❌ 工具调用失败: {call_result.error}")

        # 查看统计
        stats = manager.get_all_stats()
        print("\n📊 统计信息:")
        print(f"   全局调用: {stats['global']['total_tool_calls']}")
        print(f"   成功: {stats['global']['successful_calls']}")
        print(f"   失败: {stats['global']['failed_calls']}")

    else:
        print("❌ 连接失败")

    # 清理
    await manager.shutdown()
    return result


async def test_heartbeat():
    """测试心跳检测功能"""
    print("\n=== 测试心跳检测 ===")

    # 创建新的管理器实例
    manager = MCPClientManager.__new__(MCPClientManager)
    manager._initialized = False
    manager.__init__()

    manager.configure(
        {
            "tool_prefix": "mcp",
            "call_timeout": 30.0,
            "retry_attempts": 1,
            "retry_interval": 1.0,
            "heartbeat_enabled": True,
            "heartbeat_interval": 5.0,  # 5秒间隔用于测试
            "auto_reconnect": True,
            "max_reconnect_attempts": 2,
        }
    )

    # 添加一个测试服务器
    config = MCPServerConfig(
        name="heartbeat_test",
        enabled=True,
        transport=TransportType.HTTP,
        url="https://mcp.api-inference.modelscope.net/c9b55951d4ed47/mcp",
    )

    print("正在连接服务器...")
    result = await manager.add_server(config)

    if result:
        print("✅ 服务器连接成功")

        # 启动心跳检测
        await manager.start_heartbeat()
        print("✅ 心跳检测已启动")

        # 等待一个心跳周期
        print("等待心跳检测...")
        await asyncio.sleep(2)

        # 检查状态
        status = manager.get_status()
        print(f"✅ 心跳运行状态: {status['heartbeat_running']}")

        # 停止心跳
        await manager.stop_heartbeat()
        print("✅ 心跳检测已停止")
    else:
        print("❌ 服务器连接失败，跳过心跳测试")

    await manager.shutdown()
    return True


async def main():
    """运行所有测试"""
    print("=" * 50)
    print("MCP 客户端测试")
    print("=" * 50)

    try:
        # 基础测试
        await test_stats()
        await test_manager_basic()

        # 网络测试
        print("\n是否进行网络连接测试? (需要网络) [y/N]: ", end="")
        # 自动进行网络测试
        await test_http_connection()

        # 心跳测试
        await test_heartbeat()

        print("\n" + "=" * 50)
        print("✅ 所有测试通过!")
        print("=" * 50)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        return False

    return True


if __name__ == "__main__":
    asyncio.run(main())
