"""Opt-in real MCP/Flowise checks; no network access during normal unit tests.

Set FLOWISE_LIVE_TESTS=1, FLOWISE_API_ENDPOINT, FLOWISE_API_KEY and
FLOWISE_TEST_CHATFLOW_ID to a harmless flow capable of answering a short prompt.
"""
import asyncio
import json
import os
from pathlib import Path
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.skipif(os.getenv('FLOWISE_LIVE_TESTS') != '1', reason='Real Flowise tests are opt-in')
ROOT = Path(__file__).resolve().parents[2]


async def exercise(simple):
    flow_id = os.environ['FLOWISE_TEST_CHATFLOW_ID']
    env = dict(os.environ, FLOWISE_SIMPLE_MODE=str(simple).lower(), FLOWISE_WHITELIST_ID=flow_id,
               FLOWISE_WHITELIST_NAME_REGEX='', FLOWISE_BLACKLIST_ID='', FLOWISE_BLACKLIST_NAME_REGEX='',
               FLOWISE_CHATFLOW_WHITELIST='', FLOWISE_CHATFLOW_BLACKLIST='', FLOWISE_CHATFLOW_ID=flow_id,
               FLOWISE_ASSISTANT_ID='')
    params = StdioServerParameters(command=sys.executable, args=['-m', 'mcp_flowise'], cwd=str(ROOT), env=env)
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            assert initialized.capabilities.tools is not None
            tools = (await session.list_tools()).tools
            assert len(tools) == (2 if simple else 1)
            if simple:
                listing = await session.call_tool('list_chatflows', {})
                assert not listing.isError
                assert [flow['id'] for flow in json.loads(listing.content[0].text)] == [flow_id]
            name = 'create_prediction' if simple else tools[0].name
            arguments = {'question': 'Reply with exactly FLOWISE_MCP_OK. This is a connection check.'}
            if simple:
                arguments['chatflow_id'] = flow_id
            result = await session.call_tool(name, arguments)
            assert not result.isError, result
            prediction = json.loads(result.content[0].text)
            assert isinstance(prediction.get('text'), str) and prediction['text'].strip(), prediction.keys()
            assert 'error' not in prediction
            # Schema errors and hidden flow calls must never appear as success.
            invalid = await session.call_tool(name, {'question': ''})
            assert invalid.isError
            if simple:
                hidden = await session.call_tool(name, {'chatflow_id': 'not-visible', 'question': 'hello'})
                assert hidden.isError
            unknown = await session.call_tool('not_a_registered_tool', {'question': 'hello'})
            assert unknown.isError


@pytest.mark.parametrize('simple', [True, False], ids=['simple', 'dynamic'])
def test_real_stdio_prediction(simple):
    asyncio.run(asyncio.wait_for(exercise(simple), timeout=150))
