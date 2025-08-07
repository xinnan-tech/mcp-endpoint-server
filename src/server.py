"""
MCP Endpoint Server
主服务器文件
"""

import sys
import json
import signal
import uvicorn
from urllib.parse import quote
from .utils.config import config
from .utils.logger import get_logger
from .utils.aes_utils import decrypt, encrypt
from .utils.jsonrpc import (
    JSONRPCProtocol,
)
from src.utils.util import get_local_ip
from .utils import __version__
from contextlib import asynccontextmanager
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from .core.connection_manager import connection_manager
from .handlers.websocket_handler import websocket_handler
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logger = get_logger()


async def validate_token_and_get_agent_id(websocket: WebSocket) -> str:
    """
    验证token并获取agentId的公共方法

    Args:
        websocket: WebSocket连接对象

    Returns:
        str: 验证成功返回agentId，失败返回None
    """
    token = websocket.query_params.get("token")
    if not token:
        logger.error("缺少token参数")
        await websocket.close(code=1008, reason="缺少token参数")
        return None

    data = decrypt(config.get("server", "key", ""), token)
    if not data:
        logger.error(f"token解密失败: {token}")
        await websocket.close(code=1008, reason="token解密失败")
        return None

    try:
        data = json.loads(data)
        agent_id = data.get("agentId")
        if not agent_id:
            logger.error("无对应agentId")
            await websocket.close(code=1008, reason="无对应agentId")
            return None
        return agent_id
    except json.JSONDecodeError:
        logger.error("token数据格式错误")
        await websocket.close(code=1008, reason="token数据格式错误")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    logger.info("MCP Endpoint Server 正在启动...")
    logger.info(f"=====下面的地址分别是智控台/多模块MCP接入点地址====")
    local_ip = get_local_ip()
    logger.info(
        f"智控台MCP参数配置: http://{local_ip}:{config.getint('server', 'port', 8004)}/mcp_endpoint/health?key={config.get('server', 'key', '')}"
    )
    encrypted_token = encrypt(
        config.get("server", "key", ""), '{"agentId":"single_module"}'
    )
    token = quote(encrypted_token)
    logger.info(
        f"单模块部署MCP接入点: ws://{local_ip}:{config.getint('server', 'port', 8004)}/mcp_endpoint/mcp/?token={token}"
    )
    logger.info(
        f"多模块部署MCP接入点: ws://{local_ip}:{config.getint('server', 'port', 8004)}/mcp_endpoint/mcp/?token={token}&server_id=your_server_id"
    )
    logger.info(
        "=====请根据具体部署选择使用，请勿泄露给任何人======",
    )
    yield
    # 关闭时
    logger.info("MCP Endpoint Server 已关闭")


# 创建FastAPI应用
app = FastAPI(
    title="MCP Endpoint Server",
    description="高效的WebSocket中转服务器",
    version=__version__,
    lifespan=lifespan,
)

# 配置CORS
if config.getboolean("security", "enable_cors", True):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.get("security", "allowed_origins", "*")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/")
async def redirect_root():
    """根路径重定向到 /mcp_endpoint/"""
    return RedirectResponse(url="/mcp_endpoint/")


@app.get("/mcp_endpoint/")
async def root():
    """根路径"""
    response = JSONRPCProtocol.create_success_response(
        result={
            "message": "MCP Endpoint Server",
            "version": __version__,
            "status": "running",
        }
    )
    return JSONRPCProtocol.to_dict(response)


@app.get("/mcp_endpoint/health")
async def health_check(key: str = None):
    """健康检查"""
    # 验证key参数
    expected_key = config.get("server", "key", "")
    if not key or key != expected_key:
        response = JSONRPCProtocol.create_error_response(
            error_code=JSONRPCProtocol.AUTHENTICATION_ERROR,
            error_message="密钥验证失败",
            error_data={"details": "提供的密钥无效或缺失"},
        )
        return JSONRPCProtocol.to_dict(response)

    stats = connection_manager.get_connection_stats()
    
    # 添加多服务器支持信息
    stats["multi_server_support"] = True
    stats["available_agents"] = connection_manager.get_available_agents()
    
    response = JSONRPCProtocol.create_success_response(
        result={"status": "success", "connections": stats}
    )
    return JSONRPCProtocol.to_dict(response)


@app.websocket("/mcp_endpoint/mcp/")
async def websocket_tool_endpoint(websocket: WebSocket):
    """MCP服务器端WebSocket端点"""
    await websocket.accept()

    # 获取agentId和serverId参数
    agent_id = await validate_token_and_get_agent_id(websocket)
    if not agent_id:
        return

    # 从查询参数获取server_id，如果没有则使用默认值
    server_id = websocket.query_params.get("server_id", "default")
    
    try:
        # 注册连接
        await connection_manager.register_mcp_server_connection(agent_id, server_id, websocket)
        logger.info(f"MCP服务器连接已建立: {agent_id}/{server_id}")

        # 处理消息
        while True:
            try:
                message = await websocket.receive_text()
                await websocket_handler._handle_mcp_server_message(agent_id, server_id, message)
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"处理MCP服务器消息时发生错误: {e}")
                break

    except Exception as e:
        logger.error(f"处理MCP服务器连接时发生错误: {e}")
    finally:
        await connection_manager.unregister_mcp_server_connection(agent_id, server_id)
        logger.info(f"MCP服务器连接已关闭: {agent_id}/{server_id}")


@app.websocket("/mcp_endpoint/call/")
async def websocket_robot_endpoint(websocket: WebSocket):
    """小智端WebSocket端点"""
    await websocket.accept()

    # 获取agentId参数
    agent_id = await validate_token_and_get_agent_id(websocket)
    if not agent_id:
        return

    try:
        # 注册连接并获取UUID
        connection_uuid = await connection_manager.register_robot_connection(
            agent_id, websocket
        )
        logger.info(f"小智端连接已建立: {agent_id} (UUID: {connection_uuid})")

        # 处理消息
        while True:
            try:
                message = await websocket.receive_text()
                await websocket_handler._handle_robot_message(
                    agent_id, message, connection_uuid
                )
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"处理小智端消息时发生错误: {e}")
                break

    except Exception as e:
        logger.error(f"处理小智端连接时发生错误: {e}")
    finally:
        await connection_manager.unregister_robot_connection(connection_uuid)
        logger.info(f"小智端连接已关闭: {agent_id} (UUID: {connection_uuid})")


def signal_handler(signum, frame):
    """信号处理器"""
    logger.info(f"收到信号 {signum}，正在关闭服务器...")
    sys.exit(0)


def main():
    """主函数"""
    # 设置uvicorn日志拦截
    from .utils.logger import logger_manager

    logger_manager.setup_uvicorn_logging()

    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 获取配置
    host = config.get("server", "host", "127.0.0.1")
    port = config.getint("server", "port", 8004)
    debug = config.getboolean("server", "debug", False)

    logger.info(f"启动MCP Endpoint Server: {host}:{port}")

    # 启动服务器
    uvicorn.run(
        "src.server:app",
        host=host,
        port=port,
        reload=debug,
        log_level=config.get("server", "log_level", "INFO").lower(),
        access_log=False,
        log_config=None,  # 禁用uvicorn默认日志配置
        use_colors=True,  # 启用颜色支持
    )


if __name__ == "__main__":
    main()
