"""Real provider check. Costs tokens; run only after setting your own credentials."""
import asyncio
import json
from app.agent import ChatRequest, run_agent
from app.config import Settings
from app.db import seed_database

async def main():
    cfg=Settings.from_env()
    if cfg.mode!='llm' or not cfg.llm_configured:
        raise SystemExit('Set APP_MODE=llm, LLM_MODEL and LLM_API_KEY first.')
    seed_database(cfg.db_path,cfg.seed_loans)
    result=await run_agent(ChatRequest(question='Назови остаток портфеля и долю DPD 90+ на последнем срезе. Сначала узнай доступные даты. Приведи источники.'),cfg)
    print(result['answer'])
    print(json.dumps({'mode':result['mode'],'tools':[t['tool'] for t in result['trace']], 'usage':result['usage'], 'warnings':result['warnings']},ensure_ascii=False,indent=2))
    assert result['mode']=='llm' and result['usage']['llm_requests']>0
    assert any(t['tool']=='get_portfolio_summary' and t['ok'] for t in result['trace'])

if __name__=='__main__':asyncio.run(main())
