from dataclasses import replace
from fastapi.testclient import TestClient
import pytest
from app.main import create_app


def test_web_dashboard_and_assets(settings):
    with TestClient(create_app(settings)) as c:
        assert c.get('/').status_code==200
        assert 'RiskInsight' in c.get('/').text
        assert c.get('/static/app.js').status_code==200
        assert c.get('/api/health').json()['mode']=='demo'
        assert c.get('/api/ready').json()['mcp']=='ok'
        d=c.get('/api/dashboard').json()
        assert d['summary']['metrics']['balance_rub']==9600
        assert d['source']=='MCP / stdio / SQLite'
        assert 'nosniff' in c.get('/').headers['x-content-type-options']


def test_web_to_mcp_to_result(settings):
    with TestClient(create_app(settings)) as c:
        res=c.post('/api/chat',json={'question':'Обзор портфеля','scenario':'overview'})
        assert res.status_code==200
        r=res.json()
        assert r['request_id']
        assert r['trace'][1]['result']['metrics']['balance_rub']==9600
        assert r['protocol']['transport']=='stdio'


def test_api_token_protects_data_and_chat(settings):
    with TestClient(create_app(replace(settings,access_token='secret-token'))) as c:
        assert c.get('/api/health').status_code==200
        assert c.get('/api/context').status_code==401
        assert c.post('/api/chat',json={'question':'Обзор','scenario':'overview'}).status_code==401
        assert c.get('/api/context',headers={'Authorization':'Bearer secret-token'}).status_code==200
        assert 'secret-token' not in c.get('/api/health').text


def test_rate_limit(settings):
    with TestClient(create_app(replace(settings,requests_per_minute=1))) as c:
        body={'question':'Обзор портфеля','scenario':'overview'}
        assert c.post('/api/chat',json=body).status_code==200
        assert c.post('/api/chat',json=body).status_code==429


@pytest.mark.parametrize('params',[{'as_of':'2030-01-31'},{'region':'nonexistent'},{'risk_band':'Z'}])
def test_invalid_filters(settings,params):
    with TestClient(create_app(settings)) as c:
        assert c.get('/api/dashboard',params=params).status_code==422


def test_llm_missing_config_is_visible(settings):
    with TestClient(create_app(replace(settings,mode='llm'))) as c:
        assert c.post('/api/chat',json={'question':'Обзор портфеля'}).status_code==503


@pytest.mark.parametrize('q',['  ','a','x'*2501])
def test_question_validation(settings,q):
    with TestClient(create_app(settings)) as c:
        assert c.post('/api/chat',json={'question':q,'scenario':'overview'}).status_code==422


def test_methodology_is_mcp_resource(settings):
    with TestClient(create_app(settings)) as c:
        assert 'PD12' in c.get('/api/methodology').text
