import asyncio
import logging
import sys
from unittest.mock import patch

import pytest
from mcp import types
from mcp_flowise import utils, server_lowlevel as dynamic


def test_logs_use_stderr(tmp_path, capsys):
    with patch.dict('os.environ', {'FLOWISE_LOGFILE_PATH': str(tmp_path / 'test.log')}):
        logger = utils.setup_logging()
        logger.error('test error without protocol contamination')
        captured = capsys.readouterr()
        assert not captured.out
        assert 'test error' in captured.err
    utils.setup_logging()


def test_registration_is_repeatable_and_collisions_preserved():
    flows = [{'id': 'one', 'name': 'Same Name'}, {'id': 'two', 'name': 'Same-Name'}]
    for _ in range(2):
        tools = dynamic.register_tools(flows, {})
        assert len(tools) == 2
        assert len({t.name for t in tools}) == 2
        assert set(dynamic.NAME_TO_ID_MAPPING.values()) == {'one', 'two'}
    assert len(utils.normalize_tool_name('a' * 200)) <= 64


@pytest.mark.parametrize('arguments', [None, {}, {'question': ''}, {'question': 123}])
def test_invalid_dynamic_arguments_are_tool_errors(arguments):
    dynamic.register_tools([{'id': 'one', 'name': 'Test'}], {})
    request = types.CallToolRequest(method='tools/call', params=types.CallToolRequestParams(name='test', arguments=arguments))
    assert asyncio.run(dynamic.dispatcher_handler(request)).root.isError


def test_unknown_tool_is_error():
    request = types.CallToolRequest(method='tools/call', params=types.CallToolRequestParams(name='missing', arguments={}))
    assert asyncio.run(dynamic.dispatcher_handler(request)).root.isError


def test_prediction_cannot_bypass_filters():
    with patch('mcp_flowise.utils.fetch_chatflows', return_value=[]), patch('requests.post') as post:
        with pytest.raises(ValueError, match='excluded'):
            utils.flowise_predict('hidden-id', 'hello')
        post.assert_not_called()


def test_discovery_auth_failure_is_not_empty_success():
    import requests
    response = requests.Response()
    response.status_code = 401
    with patch('requests.get', return_value=response):
        with pytest.raises(utils.FlowiseAPIError, match='HTTP 401'):
            utils.fetch_chatflows()
