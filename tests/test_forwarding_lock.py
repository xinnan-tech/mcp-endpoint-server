"""Dependency-free regression test of the actual forwarding method bodies."""
import ast
import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace


class Closed(Exception):
    pass


class ForwardingTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_connections_release_registry_lock(self):
        source = Path(__file__).parents[1] / 'src/core/connection_manager.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ConnectionManager')
        names = ('forward_to_mcp_server', 'forward_to_robot_by_uuid')
        methods = [n for n in cls.body if getattr(n, 'name', '') in names]
        logger = SimpleNamespace(**{k: lambda *a: None for k in ('debug', 'warning', 'error')})
        namespace = dict(Any=object, json=json, ConnectionClosed=Closed, logger=logger)
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)

        async def send(message):
            raise Closed()

        connection = SimpleNamespace(websocket=SimpleNamespace(send_text=send), agent_id='agent')
        manager = SimpleNamespace(
            _lock=asyncio.Lock(),
            mcp_server_connections={'agent': {'connection': connection}},
            robot_connections={'connection': connection},
        )
        removed = []

        async def unregister(*args):
            async with manager._lock:
                removed.append(args)

        manager.unregister_mcp_server_connection = unregister
        manager.unregister_robot_connection = unregister
        for name, args in zip(names, [('agent', 'connection', {}), ('connection', {})]):
            result = await asyncio.wait_for(namespace[name](manager, *args), timeout=1)
            self.assertFalse(result)
            self.assertFalse(manager._lock.locked())
        self.assertEqual(len(removed), 2)


if __name__ == '__main__':
    unittest.main()

