import asyncio
import json
import sys
import pytest
from app.mcp_client import MCPClient, MCPError, EvidenceSession
from app.mcp_server import ProtocolServer, PROTOCOL_VERSION


def init_message(version=PROTOCOL_VERSION):
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}}}


def test_requires_initialization(tiny_db):
    s=ProtocolServer(tiny_db)
    assert s.handle({"jsonrpc":"2.0","id":1,"method":"tools/list"})["error"]["code"] == -32000


def test_version_negotiation_and_notification(tiny_db):
    s=ProtocolServer(tiny_db)
    assert s.handle(init_message('1900-01-01'))["result"]["protocolVersion"]==PROTOCOL_VERSION
    assert s.handle({"jsonrpc":"2.0","method":"notifications/initialized"}) is None
    assert s.ready
    assert len(s.handle({"jsonrpc":"2.0","id":2,"method":"tools/list"})["result"]["tools"])==9


@pytest.mark.parametrize('msg',[[],None,{}, {"jsonrpc":"1.0","id":1,"method":"ping"}, {"jsonrpc":"2.0","id":None,"method":"ping"}])
def test_invalid_messages(tiny_db,msg):
    assert 'error' in ProtocolServer(tiny_db).handle(msg)


@pytest.mark.asyncio
async def test_real_subprocess_and_structured_result(tiny_db):
    async with MCPClient(tiny_db) as c:
        pid=c.process.pid
        assert pid
        tools=await c.list_tools()
        assert tools[0]["inputSchema"]["additionalProperties"] is False
        r=await c.call_tool('get_portfolio_summary',{})
        assert r['metrics']['balance_rub']==9600
        resource=await c.request('resources/read',{'uri':'riskinsight://methodology'})
        assert 'IFRS' in resource['contents'][0]['text']
        assert c.events[0]['method']=='initialize'
        assert c.events[2]['method']=='notifications/initialized'
    assert c.process is None


@pytest.mark.asyncio
async def test_tool_validation_is_error_not_crash(tiny_db):
    async with MCPClient(tiny_db) as c:
        with pytest.raises(MCPError):
            await c.call_tool('get_portfolio_summary', {'as_of':'2030-12-31'})
        assert await c.request('ping') == {}
        assert (await c.call_tool('check_data_quality',{}))['passed']


@pytest.mark.asyncio
async def test_unknown_tool_protocol_error(tiny_db):
    async with MCPClient(tiny_db) as c:
        with pytest.raises(MCPError):
            await c.call_tool('execute_sql',{'sql':'select 1'})


@pytest.mark.asyncio
async def test_evidence_keeps_failed_calls(tiny_db):
    async with MCPClient(tiny_db) as c:
        s=EvidenceSession(c)
        a=await s.call('get_portfolio_summary',{'as_of':'1900-01-01'})
        b=await s.call('get_portfolio_summary')
        assert a['source_ref']=='T1' and 'error' in a
        assert b['source_ref']=='T2'
        assert [r['ok'] for r in s.trace]==[False,True]


@pytest.mark.asyncio
async def test_parse_error_over_actual_wire(tiny_db):
    p=await asyncio.create_subprocess_exec(sys.executable,'-m','app.mcp_server','--database',str(tiny_db),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE)
    try:
        p.stdin.write(b'not-json\n');await p.stdin.drain()
        msg=json.loads(await asyncio.wait_for(p.stdout.readline(),3))
        assert msg['error']['code']==-32700
    finally:
        p.stdin.close();await asyncio.wait_for(p.wait(),3)
