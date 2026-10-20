"""Real MCP subprocess tests against a local HTTP fixture (no external service)."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest


@pytest.mark.parametrize('simple', [True, False], ids=['simple', 'dynamic'])
def test_stdio_success_and_upstream_errors(simple, tmp_path):
    state = {'get_status': 200, 'post_status': 200}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, value):
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def do_GET(self):
            self.reply(state['get_status'], [{'id': 'fixture-flow', 'name': 'Fixture Flow'}])

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            self.reply(state['post_status'], {'text': 'FIXTURE_OK'})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def exercise():
        env = {k: v for k, v in os.environ.items() if not k.startswith('FLOWISE_')}
        env.update(FLOWISE_API_ENDPOINT=f'http://127.0.0.1:{server.server_port}',
                   FLOWISE_API_KEY='fixture-only', FLOWISE_SIMPLE_MODE=str(simple).lower(),
                   FLOWISE_LOGFILE_PATH=str(tmp_path / 'adapter.log'))
        params = StdioServerParameters(command=sys.executable, args=['-m', 'mcp_flowise'],
                                       cwd=str(Path(__file__).resolve().parents[2]), env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                assert initialized.capabilities.tools is not None
                tools = (await session.list_tools()).tools
                name = 'create_prediction' if simple else 'fixture_flow'
                args = {'question': 'hello'}
                if simple:
                    args['chatflow_id'] = 'fixture-flow'
                result = await session.call_tool(name, args)
                assert not result.isError
                assert json.loads(result.content[0].text)['text'] == 'FIXTURE_OK'
                state['post_status'] = 503
                result = await session.call_tool(name, args)
                assert result.isError
                assert 'HTTP 503' in result.content[0].text
                state['post_status'] = 200
                state['get_status'] = 401
                result = await session.call_tool(name, args)
                assert result.isError
                assert 'HTTP 401' in result.content[0].text
                # Error diagnostics must not corrupt the next stdio response.
                assert len((await session.list_tools()).tools) == len(tools)

    try:
        asyncio.run(asyncio.wait_for(exercise(), timeout=45))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
