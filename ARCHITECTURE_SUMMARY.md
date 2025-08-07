# MCP Endpoint Server 架构总结

## 设计原则

1. **保持现有接口不变**：MCP服务器通过 `/mcp_endpoint/mcp/` 连接，小智端通过 `/mcp_endpoint/call/` 连接
2. **多服务器支持**：一个智能体可以连接多个MCP服务器
3. **统一工具管理**：小智端可以获取和调用所有服务器的工具
4. **智能路由**：根据工具名称自动路由到正确的服务器
5. **工具列表请求**：支持小智端通过WebSocket请求获取所有可用工具
6. **聚合响应**：支持收集多个服务器的响应并统一返回

## 核心概念

### 智能体 (Agent)
- 标识符：`agent_id`
- 一个智能体可以连接多个MCP服务器
- 小智端通过 `agent_id` 连接到智能体

### MCP服务器 (MCP Server)
- 标识符：`server_id`
- 每个MCP服务器有唯一的 `server_id`
- 同一智能体下可以有多个MCP服务器

### 工具 (Tools)
- 每个工具属于特定的MCP服务器
- 工具名称在同一智能体下必须唯一
- 系统根据工具名称自动路由

## 连接架构

```
智能体 (agent_id: "my_agent")
├── MCP服务器1 (server_id: "calculator_server")
│   ├── 工具: calculator, scientific_calculator
│   └── 连接: ws://host:port/mcp_endpoint/mcp/?token=token&server_id=calculator_server
├── MCP服务器2 (server_id: "weather_server")  
│   ├── 工具: weather, forecast
│   └── 连接: ws://host:port/mcp_endpoint/mcp/?token=token&server_id=weather_server
└── MCP服务器3 (server_id: "translator_server")
    ├── 工具: translate, detect_language
    └── 连接: ws://host:port/mcp_endpoint/mcp/?token=token&server_id=translator_server

小智端
└── 连接: ws://host:port/mcp_endpoint/call/?token=token
```

## 接口设计

### 1. MCP服务器连接
```bash
ws://host:port/mcp_endpoint/mcp/?token=your_token&server_id=your_server_id
```

### 2. 小智端连接
```bash
ws://host:port/mcp_endpoint/call/?token=your_token
```

### 3. 工具列表请求（WebSocket）
```json
{
  "id": 1,
  "jsonrpc": "2.0",
  "method": "tools/list",
  "params": {}
}
```

响应示例：
```json
{
  "jsonrpc": "2.0",
  "result": {
    "tools": [
      {
        "name": "calculator",
        "description": "数学计算器",
        "server_id": "calculator_server"
      },
      {
        "name": "weather",
        "description": "天气查询",
        "server_id": "weather_server"
      }
    ]
  },
  "id": 1
}
```

### 4. 工具调用请求（WebSocket）
```json
{
  "id": 10,
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "calculator",
    "arguments": {
      "expression": "2 + 3 * 4"
    }
  }
}
```

系统会自动将请求路由到对应的MCP服务器。

### 5. 健康检查（HTTP）
```bash
GET /mcp_endpoint/health?key=your_server_key
```

响应包含多服务器统计信息：
```json
{
  "jsonrpc": "2.0",
  "result": {
    "status": "success",
    "connections": {
      "mcp_server_connections": 3,
      "robot_connections": 1,
      "total_connections": 4,
      "multi_server_support": true,
      "available_agents": ["my_agent"],
      "total_tools": 6,
      "mcp_servers": {
        "my_agent": {
          "calculator_server": {
            "tools_count": 2,
            "tools": ["calculator", "scientific_calculator"]
          },
          "weather_server": {
            "tools_count": 1,
            "tools": ["weather"]
          }
        }
      }
    }
  },
  "id": null
}
```

## 数据流

### 1. 工具列表请求流程
```
小智端 → MCP Endpoint Server → 收集所有服务器工具 → 返回统一工具列表
```

### 2. 工具调用流程
```
小智端 → MCP Endpoint Server → 查找工具对应服务器 → 转发到对应服务器 → 返回结果
```

### 3. 广播消息流程（聚合响应）
```
小智端 → MCP Endpoint Server → 转发给所有服务器 → 收集所有响应 → 聚合响应 → 返回给小智端
```

### 4. 服务器注册流程
```
MCP服务器 → 连接并发送初始化 → 发送工具列表 → 注册到连接管理器
```

## 关键特性

### 1. 智能路由
- 根据工具名称自动找到对应的服务器
- 支持工具名称冲突处理
- 自动处理服务器断开连接
- 错误处理和重试机制

### 2. 统一管理
- 自动收集所有服务器的工具列表
- 为每个工具添加 `server_id` 标识
- 支持动态添加/移除服务器
- 工具列表实时更新

### 3. 聚合响应
- 收集多个MCP服务器的响应
- 统一聚合后发送给小智端
- 支持错误响应处理
- 自动清理超时的待处理响应
- 支持不同类型的响应聚合

### 4. 错误处理
- 完善的错误处理和日志记录
- 自动清理断开的连接
- 友好的错误响应
- 详细的错误码和消息

### 5. 性能优化
- 异步处理提高并发性能
- 连接池管理减少资源消耗
- 智能路由避免不必要的消息转发
- 聚合响应优化减少网络开销

### 6. 监控和统计
- 详细的连接统计信息
- 每个服务器的工具数量统计
- 健康检查接口
- 实时状态监控

## 使用示例

### 1. 启动服务器
```bash
python main.py
```

### 2. 连接MCP服务器
```bash
# 计算器服务器
ws://localhost:8004/mcp_endpoint/mcp/?token=my_agent_token&server_id=calculator_server

# 天气服务器
ws://localhost:8004/mcp_endpoint/mcp/?token=my_agent_token&server_id=weather_server

# 翻译服务器
ws://localhost:8004/mcp_endpoint/mcp/?token=my_agent_token&server_id=translator_server
```

### 3. 小智端连接
```bash
ws://localhost:8004/mcp_endpoint/call/?token=my_agent_token
```

### 4. 测试功能
```bash
python test_multi_server.py
```

## 监控和调试

### 健康检查
```bash
curl "http://localhost:8004/mcp_endpoint/health?key=your_server_key"
```

### 日志查看
```bash
tail -f logs/mcp_server.log
```

### 工具列表查询
```json
{
  "id": 1,
  "jsonrpc": "2.0",
  "method": "tools/list",
  "params": {}
}
```

## 扩展性

1. **动态服务器管理**：支持运行时添加/移除MCP服务器
2. **负载均衡**：可以扩展为支持负载均衡和故障转移
3. **自定义路由**：可以扩展为支持更复杂的路由策略
4. **插件化**：可以扩展为支持插件化的工具管理
5. **自定义聚合策略**：可以扩展为支持不同的聚合策略
6. **监控和告警**：可以扩展为支持更详细的监控和告警功能

## 错误处理

### 错误码定义

- `-32601`: 工具不存在
- `-32001`: MCP服务器未连接
- `-32002`: 转发失败
- `-32603`: 内部错误
- `-32602`: 无效参数

### 错误响应示例

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32601,
    "message": "工具 'nonexistent_tool' 不存在"
  },
  "id": 10
}
```

这个架构设计保持了现有接口的兼容性，同时提供了强大的多服务器支持功能，包括工具列表请求、智能路由和聚合响应等特性。 