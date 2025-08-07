"""
WebSocket处理器
处理工具端和小智端的WebSocket连接
"""

import json
from ..core.connection_manager import connection_manager
from ..utils.logger import get_logger
from ..utils.jsonrpc import (
    create_tool_not_connected_error,
    create_forward_failed_error,
    JSONRPCProtocol,
)

logger = get_logger()


class WebSocketHandler:
    """WebSocket处理器"""

    def __init__(self):
        pass

    async def _handle_mcp_server_message(self, agent_id: str, server_id: str, message: str):
        """处理MCP服务器消息"""
        try:
            # 解析消息
            logger.info(f"收到MCP服务器消息: {agent_id}/{server_id} - {message}")

            # 尝试解析JSON-RPC响应消息
            try:
                message_data = json.loads(message)
                logger.info(f"---------MCP服务器消息---------: {message_data}")
                
                # 处理工具列表响应
                if self._is_tools_list_response(message_data):
                    logger.info(f"收到工具列表响应: {agent_id}/{server_id}")
                    self._handle_tools_list_response(agent_id, server_id, message_data)
                
                # 处理初始化响应
                if self._is_initialize_response(message_data):
                    logger.info(f"收到初始化响应: {agent_id}/{server_id}")
                    self._handle_initialize_response(agent_id, server_id, message_data)

                # 检查是否为待处理的响应
                transformed_id = message_data.get("id")
                if transformed_id and transformed_id in connection_manager.pending_responses:
                    # 这是一个待处理的响应
                    pending = connection_manager.add_server_response(transformed_id, server_id, message_data)
                    if pending:
                        # 所有期望的服务器都已响应，聚合响应
                        aggregated_response = connection_manager.aggregate_responses(pending)
                        logger.info(f"---------聚合响应---------: {aggregated_response}")
                        await connection_manager.forward_to_robot_by_uuid(
                            pending.connection_uuid, aggregated_response
                        )
                        # 清理待处理的响应
                        connection_manager.remove_pending_response(transformed_id)
                        # 还原JSON-RPC ID并获取目标连接UUID
                        connection_uuid, restored_message = (
                            connection_manager.restore_jsonrpc_message(aggregated_response)
                        )

                        if connection_uuid:
                            # 有特定的目标连接，发送给该连接
                            success = await connection_manager.forward_to_robot_by_uuid(
                                connection_uuid, restored_message
                            )
                            if not success:
                                logger.error(f"转发消息给特定小智端连接失败: {connection_uuid}")
                        else:
                            logger.error(f"没有特定目标，无法转发消息")
            except json.JSONDecodeError:
                # 如果不是JSON格式，按原来的方式处理
                logger.error(f"由于消息不是JSON格式，已忽略: {message}")

        except json.JSONDecodeError:
            logger.error(f"MCP服务器消息格式错误: {message}")
        except Exception as e:
            logger.error(f"处理MCP服务器消息时发生错误: {e}")

    def _is_tools_list_response(self, message_data: dict) -> bool:
        """检查是否为工具列表响应"""
        return (
            message_data.get("jsonrpc") == "2.0"
            and "result" in message_data
            and isinstance(message_data.get("result"), dict)
            and "tools" in message_data.get("result", {})
        )

    def _is_initialize_response(self, message_data: dict) -> bool:
        """检查是否为初始化响应"""
        return (
            message_data.get("jsonrpc") == "2.0"
            and "result" in message_data
            and isinstance(message_data.get("result"), dict)
            and "protocolVersion" in message_data.get("result", {})
        )

    def _handle_tools_list_response(self, agent_id: str, server_id: str, message_data: dict):
        """处理工具列表响应"""
        try:
            result = message_data.get("result", {})
            tools = result.get("tools", [])
            
            # 更新连接管理器中的工具列表
            connection_manager.update_tool_list(agent_id, server_id, tools)
            logger.info(f"已更新工具列表: {agent_id}/{server_id}, 工具数量: {len(tools)}")
        except Exception as e:
            logger.error(f"处理工具列表响应时发生错误: {e}")

    def _handle_initialize_response(self, agent_id: str, server_id: str, message_data: dict):
        """处理初始化响应"""
        try:
            result = message_data.get("result", {})
            # 更新服务器信息
            connection_manager.update_server_info(agent_id, server_id, result)
            logger.info(f"已更新服务器信息: {agent_id}/{server_id}")
        except Exception as e:
            logger.error(f"处理初始化响应时发生错误: {e}")

    async def _handle_robot_message(
        self, agent_id: str, message: str, connection_uuid: str
    ):
        """处理小智端消息"""
        try:
            # 解析消息
            logger.info(
                f"收到小智端消息: {agent_id} (UUID: {connection_uuid}) - {message}"
            )

            # 尝试解析JSON-RPC消息以获取id
            request_id = None
            transformed_message = message
            try:
                message_data = json.loads(message)
                request_id = message_data.get("id")

                # # 处理工具列表请求
                # if self._is_tools_list_request(message_data):
                #     logger.info(f"收到工具列表请求: {agent_id} (UUID: {connection_uuid})")
                #     await self._handle_tools_list_request(agent_id, connection_uuid, request_id)
                #     return

                # 处理工具调用请求
                if self._is_tool_call_request(message_data):
                    logger.info(f"收到工具调用请求: {agent_id} (UUID: {connection_uuid})")
                    await self._handle_tool_call_request(message_data, agent_id, connection_uuid, request_id)
                    return

                # 转换JSON-RPC ID
                transformed_message_data = connection_manager.transform_jsonrpc_message(
                    message_data, connection_uuid
                )
                transformed_message = json.dumps(
                    transformed_message_data, ensure_ascii=False
                )

                logger.info(
                    f"转换后的消息ID: {message_data.get('id')} -> {transformed_message_data.get('id')}"
                )

            except json.JSONDecodeError:
                logger.warning(f"小智端消息不是有效的JSON格式: {message}")
                # 如果消息不是JSON格式，仍然检查MCP服务器连接状态

            # 检查是否有对应的MCP服务器连接
            if not connection_manager.get_agent_servers(agent_id):
                logger.warning(f"智能体没有连接的MCP服务器: {agent_id}")
                # 发送JSON-RPC格式的错误消息给小智端
                error_message = create_tool_not_connected_error(request_id, agent_id)
                await connection_manager.forward_to_robot_by_uuid(
                    connection_uuid, error_message
                )
                return

            # 获取所有可用的服务器
            servers = connection_manager.get_agent_servers(agent_id)
            connected_servers = [server_id for server_id in servers 
                               if connection_manager.is_mcp_server_connected(agent_id, server_id)]
            
            if not connected_servers:
                logger.error(f"没有可用的MCP服务器: {agent_id}")
                error_message = create_forward_failed_error(request_id, agent_id)
                await connection_manager.forward_to_robot_by_uuid(
                    connection_uuid, error_message
                )
                return

            # 注册待处理的响应
            transformed_request_id = transformed_message_data.get("id")
            if transformed_request_id:
                connection_manager.register_pending_response(
                    transformed_request_id, request_id, connection_uuid, connected_servers
                )

            # 转发消息给所有连接的MCP服务器
            success_count = 0
            for server_id in connected_servers:
                logger.info(f"转发消息给MCP服务器: {agent_id}/{server_id}")
                if connection_manager.is_mcp_server_connected(agent_id, server_id):
                    success = await connection_manager.forward_to_mcp_server(
                        agent_id, server_id, transformed_message
                    )
                    if success:
                        success_count += 1

            if success_count == 0:
                logger.error(f"转发消息给所有MCP服务器失败: {agent_id}")
                error_message = create_forward_failed_error(request_id, agent_id)
                await connection_manager.forward_to_robot_by_uuid(
                    connection_uuid, error_message
                )
                # 清理待处理的响应
                if transformed_request_id:
                    connection_manager.remove_pending_response(transformed_request_id)

        except json.JSONDecodeError:
            logger.error(f"小智端消息格式错误: {message}")
        except Exception as e:
            logger.error(f"处理小智端消息时发生错误: {e}")

    def _is_tools_list_request(self, message_data: dict) -> bool:
        """检查是否为工具列表请求"""
        return (
            message_data.get("jsonrpc") == "2.0"
            and message_data.get("method") == "tools/list"
        )

    def _is_tool_call_request(self, message_data: dict) -> bool:
        """检查是否为工具调用请求"""
        return (
            message_data.get("jsonrpc") == "2.0"
            and message_data.get("method") == "tools/call"
        )

    async def _handle_tools_list_request(self, agent_id: str, connection_uuid: str, request_id):
        """处理工具列表请求"""
        try:
            # 获取指定智能体的所有可用工具
            all_tools = connection_manager.get_all_tools(agent_id)
            
            # 创建响应
            response = JSONRPCProtocol.create_success_response(
                result={"tools": all_tools}, request_id=request_id
            )
            response_dict = JSONRPCProtocol.to_dict(response)
            
            # 发送响应给小智端
            await connection_manager.forward_to_robot_by_uuid(
                connection_uuid, response_dict
            )
            
            logger.info(f"已返回工具列表，工具数量: {len(all_tools)}")
            
        except Exception as e:
            logger.error(f"处理工具列表请求时发生错误: {e}")
            # 发送错误响应
            error_response = JSONRPCProtocol.create_error_response(
                error_code=JSONRPCProtocol.INTERNAL_ERROR,
                error_message="获取工具列表失败",
                request_id=request_id
            )
            await connection_manager.forward_to_robot_by_uuid(
                connection_uuid, JSONRPCProtocol.to_dict(error_response)
            )

    async def _handle_tool_call_request(self, message_data: dict, agent_id: str, connection_uuid: str, request_id):
        """处理工具调用请求"""
        try:
            params = message_data.get("params", {})
            tool_name = params.get("name")
            
            if not tool_name:
                # 发送错误响应
                error_response = JSONRPCProtocol.create_error_response(
                    error_code=JSONRPCProtocol.INVALID_PARAMS,
                    error_message="缺少工具名称",
                    request_id=request_id
                )
                await connection_manager.forward_to_robot_by_uuid(
                    connection_uuid, JSONRPCProtocol.to_dict(error_response)
                )
                return
            
            # 查找工具对应的服务器
            server_id = connection_manager.find_tool_server(agent_id, tool_name)
            
            if not server_id:
                # 发送错误响应
                error_response = JSONRPCProtocol.create_error_response(
                    error_code=JSONRPCProtocol.METHOD_NOT_FOUND,
                    error_message=f"工具 '{tool_name}' 不存在",
                    request_id=request_id
                )
                await connection_manager.forward_to_robot_by_uuid(
                    connection_uuid, JSONRPCProtocol.to_dict(error_response)
                )
                return
            
            # 检查服务器是否连接
            if not connection_manager.is_mcp_server_connected(agent_id, server_id):
                # 发送错误响应
                error_response = JSONRPCProtocol.create_error_response(
                    error_code=JSONRPCProtocol.TOOL_NOT_CONNECTED,
                    error_message=f"MCP服务器 '{server_id}' 未连接",
                    request_id=request_id
                )
                await connection_manager.forward_to_robot_by_uuid(
                    connection_uuid, JSONRPCProtocol.to_dict(error_response)
                )
                return
            
            # 转换消息ID并转发给对应的MCP服务器
            transformed_message_data = connection_manager.transform_jsonrpc_message(
                message_data, connection_uuid
            )
            transformed_message = json.dumps(
                transformed_message_data, ensure_ascii=False
            )
            
            # 注册待处理的响应（单个服务器）
            transformed_request_id = transformed_message_data.get("id")
            if transformed_request_id:
                connection_manager.register_pending_response(
                    transformed_request_id, request_id, connection_uuid, [server_id]
                )
            
            # 转发给MCP服务器
            success = await connection_manager.forward_to_mcp_server(
                agent_id, server_id, transformed_message
            )
            
            if not success:
                # 发送错误响应
                error_response = JSONRPCProtocol.create_error_response(
                    error_code=JSONRPCProtocol.FORWARD_FAILED,
                    error_message=f"转发工具调用请求失败",
                    request_id=request_id
                )
                await connection_manager.forward_to_robot_by_uuid(
                    connection_uuid, JSONRPCProtocol.to_dict(error_response)
                )
                # 清理待处理的响应
                if transformed_request_id:
                    connection_manager.remove_pending_response(transformed_request_id)
            
            logger.info(f"已转发工具调用请求: {tool_name} -> {agent_id}/{server_id}")
            
        except Exception as e:
            logger.error(f"处理工具调用请求时发生错误: {e}")
            # 发送错误响应
            error_response = JSONRPCProtocol.create_error_response(
                error_code=JSONRPCProtocol.INTERNAL_ERROR,
                error_message="处理工具调用请求时发生错误",
                request_id=request_id
            )
            await connection_manager.forward_to_robot_by_uuid(
                connection_uuid, JSONRPCProtocol.to_dict(error_response)
            )


# 全局WebSocket处理器实例
websocket_handler = WebSocketHandler()
