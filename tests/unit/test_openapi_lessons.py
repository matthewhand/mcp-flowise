"""Regressions for the compatible lessons ported from mcp-openapi-proxy."""
import asyncio
import hashlib
import logging
from logging.handlers import RotatingFileHandler
from unittest.mock import Mock

import pytest
from mcp import types

from mcp_flowise import server_lowlevel as dynamic, utils
from mcp_flowise.protocol import advertised_server_name, list_annotations, prediction_annotations


@pytest.fixture
def fresh_registry(monkeypatch):
    monkeypatch.setattr(dynamic, '_tools_loaded', False)
    monkeypatch.setattr(dynamic, '_tools_load_lock', asyncio.Lock())
    monkeypatch.setattr(dynamic, 'tools', [])
    monkeypatch.setattr(dynamic, 'NAME_TO_ID_MAPPING', {})
    monkeypatch.delenv('TOOL_NAME_PREFIX', raising=False)
    monkeypatch.delenv('TOOL_NAME_MAX_LENGTH', raising=False)


def test_tool_annotations_describe_actual_effects():
    listing = list_annotations()
    prediction = prediction_annotations()
    assert listing.readOnlyHint and listing.idempotentHint
    assert listing.destructiveHint is False
    assert prediction.readOnlyHint is False
    assert prediction.idempotentHint is False
    assert prediction.destructiveHint


def test_server_name_is_explicit_or_friendly_default(monkeypatch):
    monkeypatch.delenv('MCP_SERVER_NAME', raising=False)
    assert advertised_server_name() == 'mcp-flowise'
    monkeypatch.setenv('MCP_SERVER_NAME', '  writing-flows  ')
    assert advertised_server_name() == 'writing-flows'


@pytest.mark.parametrize('value', ['0', '-1', 'NaN', 'inf', 'invalid'])
def test_invalid_timeouts_rejected(monkeypatch, value):
    monkeypatch.setenv('FLOWISE_HTTP_TIMEOUT', value)
    with pytest.raises(ValueError, match='finite positive'):
        utils.request_timeout()


def test_configured_timeout(monkeypatch):
    monkeypatch.setenv('FLOWISE_HTTP_TIMEOUT', '125.5')
    assert utils.request_timeout() == 125.5


@pytest.mark.parametrize('value', ['15', '65', 'nope'])
def test_invalid_name_limits_rejected(monkeypatch, value):
    monkeypatch.setenv('TOOL_NAME_MAX_LENGTH', value)
    with pytest.raises(ValueError, match='16 to 64'):
        utils.tool_name_limit()


def test_prefix_and_collisions_respect_limit(monkeypatch, fresh_registry):
    monkeypatch.setenv('TOOL_NAME_MAX_LENGTH', '16')
    monkeypatch.setenv('TOOL_NAME_PREFIX', 'voice_')
    flows = [{'id': str(i), 'name': 'a long duplicated name'} for i in range(3)]
    tools = dynamic.register_tools(flows, {})
    assert len({t.name for t in tools}) == 3
    assert all(t.name.startswith('voice_') and len(t.name) <= 16 for t in tools)


def test_generated_suffix_cannot_overwrite_another_flow(fresh_registry):
    digest = hashlib.sha256(b'b').hexdigest()[:12]
    flows = [{'id': 'a', 'name': 'Same'}, {'id': 'b', 'name': 'Same'},
             {'id': 'c', 'name': 'same_' + digest}]
    tools = dynamic.register_tools(flows, {})
    first = dict(dynamic.NAME_TO_ID_MAPPING)
    assert len(tools) == len(first) == 3
    dynamic.register_tools(list(reversed(flows)), {})
    assert dynamic.NAME_TO_ID_MAPPING == first


def test_discovery_is_shared_by_concurrent_requests(monkeypatch, fresh_registry):
    fetch = Mock(return_value=[{'id': 'flow', 'name': 'Flow'}])
    monkeypatch.setattr(dynamic, 'fetch_chatflows', fetch)

    async def exercise():
        await asyncio.gather(*(dynamic.ensure_tools_loaded() for _ in range(8)))

    asyncio.run(exercise())
    assert fetch.call_count == 1
    assert len(dynamic.tools) == 1


def test_discovery_retries_failure_and_accepts_empty_inventory(monkeypatch, fresh_registry):
    fetch = Mock(side_effect=[utils.FlowiseAPIError('unavailable'), []])
    monkeypatch.setattr(dynamic, 'fetch_chatflows', fetch)

    async def exercise():
        with pytest.raises(utils.FlowiseAPIError):
            await dynamic.ensure_tools_loaded()
        await dynamic.ensure_tools_loaded()
        await dynamic.ensure_tools_loaded()

    asyncio.run(exercise())
    assert fetch.call_count == 2
    assert dynamic._tools_loaded and dynamic.tools == []


def test_dynamic_schema_and_handler_reject_unknown_arguments(fresh_registry):
    tools = dynamic.register_tools([{'id': 'flow', 'name': 'Flow'}], {})
    assert tools[0].inputSchema['additionalProperties'] is False
    request = types.CallToolRequest(method='tools/call', params=types.CallToolRequestParams(
        name='flow', arguments={'question': 'hello', 'unexpected': True}))
    assert asyncio.run(dynamic.dispatcher_handler(request)).root.isError


def test_logs_are_optional_and_bounded(monkeypatch, tmp_path):
    monkeypatch.delenv('FLOWISE_LOGFILE_PATH', raising=False)
    logger = utils.setup_logging()
    assert not any(isinstance(handler, logging.FileHandler) for handler in logger.handlers)
    monkeypatch.setenv('FLOWISE_LOGFILE_PATH', str(tmp_path / 'nested' / 'adapter.log'))
    logger = utils.setup_logging()
    files = [handler for handler in logger.handlers if isinstance(handler, RotatingFileHandler)]
    assert len(files) == 1
    assert files[0].maxBytes == 5 * 1024 * 1024 and files[0].backupCount == 2
    monkeypatch.delenv('FLOWISE_LOGFILE_PATH')
    utils.setup_logging()


@pytest.mark.parametrize('variable', ['FLOWISE_WHITELIST_NAME_REGEX', 'FLOWISE_BLACKLIST_NAME_REGEX'])
def test_invalid_filter_patterns_are_configuration_errors(monkeypatch, variable):
    monkeypatch.setenv(variable, '[')
    with pytest.raises(ValueError, match=variable):
        utils.filter_chatflows([])


def test_startup_configuration_error_has_no_traceback(monkeypatch, capsys):
    from mcp_flowise.__main__ import main
    monkeypatch.setenv('FLOWISE_HTTP_TIMEOUT', 'NaN')
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert 'Invalid configuration' in captured.err
    assert 'Traceback' not in captured.err
    utils.setup_logging()


def test_cached_discovery_cannot_authorize_removed_flow(monkeypatch, fresh_registry):
    dynamic.register_tools([{'id': 'flow', 'name': 'Flow'}], {})
    monkeypatch.setattr(utils, 'fetch_chatflows', Mock(return_value=[]))
    post = Mock()
    monkeypatch.setattr(utils.requests, 'post', post)
    request = types.CallToolRequest(method='tools/call', params=types.CallToolRequestParams(
        name='flow', arguments={'question': 'hello'}))
    assert asyncio.run(dynamic.dispatcher_handler(request)).root.isError
    post.assert_not_called()
