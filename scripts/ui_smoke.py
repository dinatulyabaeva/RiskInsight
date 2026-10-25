"""Browser checks. Run a server first. Install requirements-ui.txt + Chromium.

--bridge is a test-only workaround for environments whose browser policy blocks
network navigation: HTML/CSS/JS are loaded locally, fetch is bridged to the REAL
HTTP backend with httpx. It is not a substitute for testing public HTTPS.
"""
import argparse
import json
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
from app.config import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--bridge', action='store_true')
    parser.add_argument('--executable', default=None)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/screenshots')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'transport': 'local fetch bridge to real HTTP' if args.bridge else 'native browser HTTP', 'errors': []}

    def bridge(url, options=None):
        options = options or {}
        if not str(url).startswith('/api/'):
            return {'status': 404, 'headers': {}, 'body': ''}
        res = httpx.request(options.get('method', 'GET'), args.base_url + str(url),
                            headers=options.get('headers', {}), content=options.get('body'), timeout=90)
        return {'status': res.status_code, 'headers': dict(res.headers), 'body': res.text}

    def load(page):
        page.on('pageerror', lambda e: report['errors'].append(str(e)))
        if args.bridge:
            html = (ROOT / 'static/index.html').read_text(encoding='utf-8')
            html = html.replace('<link rel="stylesheet" href="/static/app.css">',
                                '<style>' + (ROOT / 'static/app.css').read_text(encoding='utf-8') + '</style>')
            html = html.replace('<script defer src="/static/app.js"></script>', '')
            page.expose_function('testApiBridge', bridge)
            page.set_content(html, wait_until='domcontentloaded')
            page.evaluate('window.fetch=async(u,o)=>{const r=await window.testApiBridge(u,o);return new Response(r.body,{status:r.status,headers:r.headers});}')
            page.add_script_tag(content=(ROOT / 'static/app.js').read_text(encoding='utf-8'))
        else:
            page.goto(args.base_url, wait_until='networkidle')
        page.wait_for_function("document.getElementById('kpi-count').textContent !== '—'", timeout=20000)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.executable, headless=True)
        page = browser.new_page(viewport={'width': 1512, 'height': 1150})
        load(page)
        page.screenshot(path=str(args.output / 'dashboard.png'), full_page=True)
        page.locator('[data-scenario="drivers"]').click()
        page.wait_for_selector('.result-block', timeout=420000)
        assert page.locator('.source-ref').count() > 0
        page.screenshot(path=str(args.output / 'analysis.png'), full_page=True)
        page.locator('.source-ref').first.click()
        report['evidence_expanded'] = page.locator('details.trace').first.get_attribute('open') is not None
        assert report['evidence_expanded']
        page.select_option('#product-filter', 'Потребительский')
        page.wait_for_function("!document.getElementById('export-dashboard').disabled")
        report['filtered_count'] = page.locator('#kpi-count').inner_text()
        # Test downloads without inspecting OS paths or user files.
        with page.expect_download() as event:
            page.locator('#export-dashboard').click()
        report['dashboard_export'] = event.value.suggested_filename
        with page.expect_download() as event:
            page.locator('.result-actions button').first.click()
        report['report_export'] = event.value.suggested_filename
        with page.expect_download() as event:
            page.locator('.result-actions button').nth(1).click()
        report['evidence_export'] = event.value.suggested_filename
        mobile = browser.new_page(viewport={'width': 390, 'height': 844})
        load(mobile)
        report['mobile_horizontal_overflow'] = mobile.evaluate('document.documentElement.scrollWidth>innerWidth')
        assert not report['mobile_horizontal_overflow']
        mobile.screenshot(path=str(args.output / 'mobile.png'), full_page=True)
        slides = browser.new_page(viewport={'width': 1440, 'height': 1000})
        slides.on('pageerror', lambda e: report['errors'].append(str(e)))
        if args.bridge:
            slides.set_content((ROOT / 'docs/presentation.html').read_text(encoding='utf-8'), wait_until='domcontentloaded')
        else:
            slides.goto(args.base_url + '/presentation', wait_until='domcontentloaded')
        slides.wait_for_timeout(300)
        slides.screenshot(path=str(args.output / 'presentation-cover.png'), animations='disabled', full_page=True)
        slides.keyboard.press('ArrowRight')
        assert slides.locator('#counter').inner_text() == '02 / 10'
        slides.keyboard.press('ArrowRight')
        slides.locator('[data-step="3"]').click()
        assert 'MCP JSON-RPC' in slides.locator('#flow-description').inner_text()
        slides.evaluate('go(5)')
        slides.select_option('#demo-month', '2026-04-30')
        report['presentation_metric'] = slides.locator('#d-npl').inner_text()
        assert not report['errors']
        browser.close()
    (args.output / 'browser-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
