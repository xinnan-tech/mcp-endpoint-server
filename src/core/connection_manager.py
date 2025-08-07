"""
连接管理器
负责管理WebSocket连接和消息转发
"""

import asyncio
import json
import time
import uuid
from typing import Dict, Any, List, Optional, Tuple, Set
from websockets.server import WebSocketServerProtocol
from websockets.exceptions import ConnectionClosed
from ..utils.logger import get_logger

logger = get_logger()


class RobotConnection:
    """小智端连接信息"""

    def __init__(self, websocket: WebSocketServerProtocol, agent_id: str):
        self.websocket = websocket
        self.agent_id = agent_id
        self.connection_uuid = str(uuid.uuid4())
        self.timestamp = time.time()


class MCPServerConnection:
    """MCP服务器连接信息"""

    def __init__(self, websocket: WebSocketServerProtocol, agent_id: str, server_id: str):
        self.websocket = websocket
        self.agent_id = agent_id
        self.server_id = server_id
        self.connection_uuid = str(uuid.uuid4())
        self.timestamp = time.time()
        self.tools: Dict[str, Any] = {}  # 存储工具列表
        self.server_info: Dict[str, Any] = {}  # 存储服务器信息


class PendingResponse:
    """待处理的响应信息"""

    def __init__(self, request_id: Any, connection_uuid: str, expected_servers: List[str]):
        self.request_id = request_id
        self.connection_uuid = connection_uuid
        self.expected_servers = set(expected_servers)
        self.received_responses = {}
        self.timestamp = time.time()


class ConnectionManager:
    """连接管理器"""

    def __init__(self):
        # MCP服务器连接: {agent_id: {server_id: MCPServerConnection}}
        self.mcp_server_connections: Dict[str, Dict[str, MCPServerConnection]] = {}
        # 小智端连接: {connection_uuid: RobotConnection}
        self.robot_connections: Dict[str, RobotConnection] = {}
        # 连接时间戳: {agentId: timestamp}
        self.connection_timestamps: Dict[str, float] = {}
        # 待处理的响应: {transformed_request_id: PendingResponse}
        self.pending_responses: Dict[str, PendingResponse] = {}
        # 连接锁
        self._lock = asyncio.Lock()

    async def register_mcp_server_connection(
        self, agent_id: str, server_id: str, websocket: WebSocketServerProtocol
    ):
        """注册MCP服务器连接"""
        async with self._lock:
            # 初始化agent_id的连接字典
            if agent_id not in self.mcp_server_connections:
                self.mcp_server_connections[agent_id] = {}
            
            # 如果已存在连接，先关闭旧连接
            if server_id in self.mcp_server_connections[agent_id]:
                old_connection = self.mcp_server_connections[agent_id][server_id]
                try:
                    await old_connection.websocket.close(1000, "新连接替换")
                except Exception as e:
                    logger.warning(f"关闭旧MCP服务器连接失败: {e}")

            # 创建新的MCP服务器连接
            mcp_conn = MCPServerConnection(websocket, agent_id, server_id)
            self.mcp_server_connections[agent_id][server_id] = mcp_conn
            self.connection_timestamps[agent_id] = time.time()
            logger.info(f"MCP服务器连接已注册: {agent_id}/{server_id} (UUID: {mcp_conn.connection_uuid})")

    async def register_robot_connection(
        self, agent_id: str, websocket: WebSocketServerProtocol
    ) -> str:
        """注册小智端连接，返回分配的UUID"""
        async with self._lock:
            robot_conn = RobotConnection(websocket, agent_id)
            self.robot_connections[robot_conn.connection_uuid] = robot_conn
            self.connection_timestamps[agent_id] = time.time()
            logger.info(
                f"小智端连接已注册: {agent_id}, UUID: {robot_conn.connection_uuid}"
            )
            return robot_conn.connection_uuid

    async def unregister_mcp_server_connection(self, agent_id: str, server_id: str):
        """注销MCP服务器连接"""
        async with self._lock:
            if agent_id in self.mcp_server_connections:
                if server_id in self.mcp_server_connections[agent_id]:
                    del self.mcp_server_connections[agent_id][server_id]
                    # 如果该agent_id下没有其他服务器连接，清理agent_id
                    if not self.mcp_server_connections[agent_id]:
                        del self.mcp_server_connections[agent_id]
                    logger.info(f"MCP服务器连接已注销: {agent_id}/{server_id}")

    async def unregister_robot_connection(self, connection_uuid: str):
        """注销小智端连接"""
        async with self._lock:
            if connection_uuid in self.robot_connections:
                robot_conn = self.robot_connections[connection_uuid]
                del self.robot_connections[connection_uuid]
                logger.info(
                    f"小智端连接已注销: {robot_conn.agent_id}, UUID: {connection_uuid}"
                )

    def _transform_jsonrpc_id(self, original_id: Any, connection_uuid: str) -> str:
        """转换JSON-RPC ID"""
        if isinstance(original_id, int):
            return f"{connection_uuid}_n_{original_id}"
        elif isinstance(original_id, str):
            return f"{connection_uuid}_s_{original_id}"
        else:
            # 其他类型转换为字符串
            return f"{connection_uuid}_s_{str(original_id)}"

    def _restore_jsonrpc_id(self, transformed_id: str) -> Tuple[Optional[str], Any]:
        """还原JSON-RPC ID，返回(connection_uuid, original_id)"""
        if not transformed_id or "_" not in transformed_id:
            return None, None

        # 解析格式: uuid_type_original_id
        parts = transformed_id.split("_", 2)
        if len(parts) < 3:
            return None, None

        connection_uuid = parts[0]
        id_type = parts[1]
        original_id_part = parts[2]

        if id_type == "n":
            # 数字类型
            try:
                original_id = int(original_id_part)
            except ValueError:
                original_id = original_id_part
        elif id_type == "s":
            # 字符串类型
            if original_id_part == "null":
                original_id = None
            else:
                original_id = original_id_part
        else:
            # 未知类型，保持原样
            original_id = original_id_part

        return connection_uuid, original_id

    def transform_jsonrpc_message(
        self, message: Dict[str, Any], connection_uuid: str
    ) -> Dict[str, Any]:
        """转换JSON-RPC消息的ID"""
        if not isinstance(message, dict):
            return message

        # 创建消息副本
        transformed_message = message.copy()

        # 转换ID
        if "id" in transformed_message:
            original_id = transformed_message["id"]
            if original_id:
                transformed_message["id"] = self._transform_jsonrpc_id(
                    original_id, connection_uuid
                )

        return transformed_message

    def restore_jsonrpc_message(
        self, message: Dict[str, Any]
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        """还原JSON-RPC消息的ID，返回(connection_uuid, restored_message)"""
        if not isinstance(message, dict):
            return None, message

        # 创建消息副本
        restored_message = message.copy()

        # 还原ID
        if "id" in restored_message:
            transformed_id = restored_message["id"]
            connection_uuid, original_id = self._restore_jsonrpc_id(transformed_id)
            if connection_uuid:
                restored_message["id"] = original_id
                return connection_uuid, restored_message

        return None, restored_message

    async def forward_to_mcp_server(self, agent_id: str, server_id: str, message: Any) -> bool:
        """转发消息给指定的MCP服务器"""
        async with self._lock:
            if agent_id not in self.mcp_server_connections:
                logger.warning(f"智能体连接不存在: {agent_id}")
                return False
            
            if server_id not in self.mcp_server_connections[agent_id]:
                logger.warning(f"MCP服务器连接不存在: {agent_id}/{server_id}")
                return False

            mcp_conn = self.mcp_server_connections[agent_id][server_id]
            try:
                # 确保消息是字符串格式
                if isinstance(message, dict):
                    message_str = json.dumps(message, ensure_ascii=False)
                elif isinstance(message, str):
                    message_str = message
                else:
                    message_str = str(message)

                await mcp_conn.websocket.send_text(message_str)
                logger.info(f"消息已转发给MCP服务器 {agent_id}/{server_id}: {message_str}")
                return True
            except ConnectionClosed:
                logger.warning(f"MCP服务器连接已关闭: {agent_id}/{server_id}")
                await self.unregister_mcp_server_connection(agent_id, server_id)
                return False
            except Exception as e:
                logger.error(f"转发消息给MCP服务器失败: {e}")
                return False

    async def forward_to_robot_by_uuid(
        self, connection_uuid: str, message: Any
    ) -> bool:
        """根据UUID转发消息给特定的小智端连接"""
        async with self._lock:
            if connection_uuid not in self.robot_connections:
                logger.warning(f"小智端连接不存在: {connection_uuid}")
                return False

            robot_conn = self.robot_connections[connection_uuid]
            try:
                # 确保消息是字符串格式
                if isinstance(message, dict):
                    message_str = json.dumps(message, ensure_ascii=False)
                elif isinstance(message, str):
                    message_str = message
                else:
                    message_str = str(message)

                await robot_conn.websocket.send_text(message_str)
                logger.info(
                    f"消息已转发给小智端 {robot_conn.agent_id} (UUID: {connection_uuid}): {message_str}"
                )
                return True
            except ConnectionClosed:
                logger.warning(f"小智端连接已关闭: {connection_uuid}")
                await self.unregister_robot_connection(connection_uuid)
                return False
            except Exception as e:
                logger.error(f"转发消息给小智端失败: {e}")
                return False

    def register_pending_response(self, transformed_request_id: str, request_id: Any, connection_uuid: str, expected_servers: List[str]):
        """注册待处理的响应"""
        self.pending_responses[transformed_request_id] = PendingResponse(
            request_id, connection_uuid, expected_servers
        )
        logger.info(f"注册待处理响应: {transformed_request_id}, 期望服务器: {expected_servers}")

    def add_server_response(self, transformed_request_id: str, server_id: str, response: Dict[str, Any]):
        """添加服务器响应"""
        if transformed_request_id in self.pending_responses:
            pending = self.pending_responses[transformed_request_id]
            pending.received_responses[server_id] = response
            logger.info(f"收到服务器响应: {transformed_request_id}/{server_id}")
            
            # 检查是否所有期望的服务器都已响应
            if pending.expected_servers.issubset(set(pending.received_responses.keys())):
                return pending
        return None

    def remove_pending_response(self, transformed_request_id: str):
        """移除待处理的响应"""
        if transformed_request_id in self.pending_responses:
            del self.pending_responses[transformed_request_id]
            logger.info(f"移除待处理响应: {transformed_request_id}")

    def aggregate_responses(self, pending: PendingResponse) -> Dict[str, Any]:
        """聚合多个服务器的响应"""
        try:
            # 收集所有响应
            all_responses = []
            id = ""
            flag = ""
            
            for server_id, response in pending.received_responses.items():
                id = response.get("id", "")
                
                if "result" in response:
                    result = response["result"].copy()
                    
                    # 检查是否为工具列表响应
                    if "tools" in result:
                        flag = "tools"
                        tools = result["tools"]
                        # 为每个工具添加服务器标识
                        for tool in tools:
                            tool["server_id"] = server_id
                        all_responses.extend(tools)
                    elif "content" in result:
                        flag = "content"
                        content = result["content"]
                        all_responses.extend(content)
                    else:
                        # 其他类型的响应，添加服务器标识
                        result["server_id"] = server_id
                        all_responses.append(result)
                        
                elif "error" in response:
                    # 处理错误响应
                    error = response["error"].copy()
                    error["server_id"] = server_id
                    all_responses.append({"error": error})

            # 创建聚合响应
            if flag == "tools" or flag == "content":
                # 工具列表响应
                aggregated_response = {
                    "jsonrpc": "2.0",
                    "id": id,
                    "result": {
                        flag: all_responses,
                        "total_servers": len(pending.expected_servers),
                        "responded_servers": len(pending.received_responses)
                    }
                }
            else:
                # 其他类型的响应
                aggregated_response = {
                    "jsonrpc": "2.0",
                    "id": id,
                    "result": {
                        "responses": all_responses,
                        "total_servers": len(pending.expected_servers),
                        "responded_servers": len(pending.received_responses)
                    }
                }

            logger.info(f"聚合响应完成: {len(all_responses)} 个响应")
            return aggregated_response

        except Exception as e:
            logger.error(f"聚合响应时发生错误: {e}")
            # 返回错误响应
            return {
                "jsonrpc": "2.0",
                "id": pending.request_id,
                "error": {
                    "code": -32603,
                    "message": "聚合响应时发生错误",
                    "data": {"details": str(e)}
                }
            }

    def update_tool_list(self, agent_id: str, server_id: str, tools: List[Dict[str, Any]]):
        """更新工具列表"""
        if agent_id in self.mcp_server_connections and server_id in self.mcp_server_connections[agent_id]:
            mcp_conn = self.mcp_server_connections[agent_id][server_id]
            # 为每个工具添加服务器标识
            for tool in tools:
                tool["server_id"] = server_id
            mcp_conn.tools = {tool["name"]: tool for tool in tools}
            logger.info(f"已更新工具列表: {agent_id}/{server_id}, 工具数量: {len(tools)}")

    def update_server_info(self, agent_id: str, server_id: str, server_info: Dict[str, Any]):
        """更新服务器信息"""
        if agent_id in self.mcp_server_connections and server_id in self.mcp_server_connections[agent_id]:
            mcp_conn = self.mcp_server_connections[agent_id][server_id]
            mcp_conn.server_info = server_info
            logger.info(f"已更新服务器信息: {agent_id}/{server_id}")

    def get_all_tools(self, agent_id: str) -> List[Dict[str, Any]]:
        """获取指定智能体的所有可用工具列表"""
        all_tools = []
        if agent_id in self.mcp_server_connections:
            for server_id, mcp_conn in self.mcp_server_connections[agent_id].items():
                for tool_name, tool_info in mcp_conn.tools.items():
                    all_tools.append(tool_info)
        return all_tools

    def find_tool_server(self, agent_id: str, tool_name: str) -> Optional[str]:
        """根据工具名称查找对应的服务器ID"""
        if agent_id in self.mcp_server_connections:
            for server_id, mcp_conn in self.mcp_server_connections[agent_id].items():
                if tool_name in mcp_conn.tools:
                    return server_id
        return None

    def get_connection_stats(self) -> Dict[str, Any]:
        """获取连接统计信息"""
        # 统计每个agent_id的连接数
        agent_connection_counts = {}
        for robot_conn in self.robot_connections.values():
            agent_id = robot_conn.agent_id
            agent_connection_counts[agent_id] = (
                agent_connection_counts.get(agent_id, 0) + 1
            )

        # 统计MCP服务器信息
        mcp_server_stats = {}
        total_mcp_connections = 0
        total_tools = 0
        
        for agent_id, servers in self.mcp_server_connections.items():
            mcp_server_stats[agent_id] = {}
            for server_id, mcp_conn in servers.items():
                total_mcp_connections += 1
                tool_count = len(mcp_conn.tools)
                total_tools += tool_count
                mcp_server_stats[agent_id][server_id] = {
                    "tools_count": tool_count,
                    "tools": list(mcp_conn.tools.keys()),
                    "server_info": mcp_conn.server_info
                }

        return {
            "mcp_server_connections": total_mcp_connections,
            "robot_connections": len(self.robot_connections),
            "total_connections": total_mcp_connections + len(self.robot_connections),
            "robot_connections_by_agent": agent_connection_counts,
            "mcp_servers": mcp_server_stats,
            "total_tools": total_tools,
        }

    def is_mcp_server_connected(self, agent_id: str, server_id: str) -> bool:
        """检查MCP服务器是否已连接"""
        return (agent_id in self.mcp_server_connections and 
                server_id in self.mcp_server_connections[agent_id])

    def is_robot_connected(self, agent_id: str) -> bool:
        """检查小智端是否已连接"""
        return any(
            conn.agent_id == agent_id for conn in self.robot_connections.values()
        )

    def get_robot_connections_by_agent(self, agent_id: str) -> List[RobotConnection]:
        """获取指定agent_id的所有小智端连接"""
        return [
            conn
            for conn in self.robot_connections.values()
            if conn.agent_id == agent_id
        ]

    def get_available_agents(self) -> List[str]:
        """获取所有可用的智能体ID列表"""
        return list(self.mcp_server_connections.keys())

    def get_agent_servers(self, agent_id: str) -> List[str]:
        """获取指定智能体的所有服务器ID列表"""
        if agent_id in self.mcp_server_connections:
            return list(self.mcp_server_connections[agent_id].keys())
        return []


# 全局连接管理器实例
connection_manager = ConnectionManager()
