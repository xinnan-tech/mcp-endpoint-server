# MCP Endpoint Server

一个高效的xiaozhi mcp接入点服务器，用于[xiaozhi-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)自定义mcp服务注册，方便拓展小智设备工具调用。

本项目是一个基于websocket的MCP注册中心，功能单一，建议使用Docker运行。

## 功能特性

- 🚀 **高性能**: 基于FastAPI和WebSocket的异步架构，支持高并发
- 🔄 **消息转发**: 自动转发工具端和小智端之间的消息
- 🔒 **连接管理**: 智能管理WebSocket连接，支持连接清理
- 📊 **监控统计**: 提供连接统计和健康检查接口
- ⚙️ **配置灵活**: 支持配置文件管理，易于部署和维护
- 🛡️ **错误处理**: 完善的错误处理和日志记录
- 🌐 **多服务器支持**: 支持一个智能体连接多个MCP服务器，统一管理工具列表和调用
- 🎯 **智能路由**: 根据工具名称自动路由到正确的服务器
- 📋 **统一工具列表**: 小智端可以获取所有服务器的工具列表

## 架构设计

![原理图](./docs/schematic.png)

## 接入教程

[MCP 接入点部署使用指南](https://github.com/xinnan-tech/xiaozhi-esp32-server/blob/main/docs/mcp-endpoint-integration.md)

## 技术细节
[技术细节](./README_dev.md)

