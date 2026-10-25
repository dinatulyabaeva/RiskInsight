"""Optional independent MCP v1 SDK client against the custom stdio server.
Install requirements-sdk.txt first. This check is separate from bundled tests.
"""
import asyncio
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from app.config import ROOT, Settings
from app.db import seed_database

async def main():
    cfg=Settings.from_env();seed_database(cfg.db_path,cfg.seed_loans)
    server=StdioServerParameters(command=sys.executable,args=['-m','app.mcp_server','--database',str(cfg.db_path)],cwd=str(ROOT))
    async with stdio_client(server) as (read,write):
        async with ClientSession(read,write) as session:
            init=await session.initialize()
            tools=await session.list_tools()
            result=await session.call_tool('get_portfolio_summary',{})
            assert len(tools.tools)==9
            assert not result.isError
            print('MCP SDK handshake:',init.protocolVersion)
            print(result.content[0].text)

if __name__=='__main__':asyncio.run(main())
