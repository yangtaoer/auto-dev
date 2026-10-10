from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AppearanceTests(unittest.TestCase):
    def check(self, code: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, AUTODEV_DATA_DIR=directory, AUTODEV_WORKER_ENABLED="0",
                       BOOTSTRAP_ADMIN_PASSWORD="appearance-test-only", BOOTSTRAP_PM_PASSWORD="appearance-pm-only",
                       AUTODEV_ENV="development", AUTODEV_ALLOWED_HOSTS="localhost,127.0.0.1,testserver",
                       AUTODEV_SEED_DEMO="1", PYTHONIOENCODING="utf-8")
            script = '''from app.main import app
from app import db
from app.services import ui_preferences
from fastapi.testclient import TestClient
db.init_db()
def sign_in(client,name='admin',password='appearance-test-only'):
    result=client.post('/api/auth/login',json={'username':name,'password':password})
    assert result.status_code==200,result.text
def appearance(theme_id='moon-courtyard',**values):
    return {**ui_preferences.DEFAULTS,'theme_id':theme_id,**values}
'''+textwrap.dedent(code)
            result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                                    capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_account_preferences_survive_session_browser_and_database_reopen(self):
        self.check('''
        with TestClient(app) as first:
            sign_in(first)
            assert first.get('/api/me/appearance').json()['appearance']['theme_id']=='mint-garden'
            saved=first.put('/api/me/appearance',json=appearance(font_size='large',motion='reduced'))
            assert saved.status_code==200,saved.text
            first.post('/api/auth/logout')
        db.init_db()
        with TestClient(app) as second:
            assert not second.cookies
            sign_in(second)
            result=second.get('/api/me/appearance').json()['appearance']
            assert result['theme_id']=='moon-courtyard' and result['font_size']=='large' and result['motion']=='reduced'
            page=second.get('/').text
            assert 'data-theme="moon-courtyard"' in page and 'data-motion="reduced"' in page
            assert '--skin-bg:#0b252e' in page
        ''')

    def test_preferences_are_per_user_and_non_admin_can_choose(self):
        self.check('''
        with TestClient(app) as admin,TestClient(app) as pm:
            sign_in(admin)
            sign_in(pm,'pm','appearance-pm-only')
            assert admin.put('/api/me/appearance',json=appearance()).status_code==200
            assert pm.get('/api/me/appearance').json()['appearance']['theme_id']=='mint-garden'
            assert pm.put('/api/me/appearance',json=appearance('paper-workshop')).status_code==200
            assert admin.get('/api/me/appearance').json()['appearance']['theme_id']=='moon-courtyard'
            assert pm.get('/api/me/appearance').json()['appearance']['theme_id']=='paper-workshop'
        ''')

    def test_write_auth_whitelist_and_no_cross_account_fields(self):
        self.check('''
        with TestClient(app) as client:
            assert client.get('/api/me/appearance').status_code==401
            assert client.put('/api/me/appearance',json=appearance()).status_code==401
            sign_in(client)
            for value in ['unknown','../../style','<script>','javascript:alert(1)']:
                assert client.put('/api/me/appearance',json=appearance(value)).status_code==400
            assert client.put('/api/me/appearance',json={**appearance(),'user_id':2}).status_code==422
            assert client.put('/api/me/appearance',json=appearance(motion='javascript')).status_code==422
            assert client.get('/api/me/appearance').json()['appearance']['theme_id']=='mint-garden'
        ''')

    def test_login_hint_is_not_an_account_setting_and_invalid_hint_falls_back(self):
        self.check('''
        with TestClient(app) as client:
            client.cookies.set('autodev_theme','paper-workshop')
            assert 'data-theme="paper-workshop"' in client.get('/login').text
            sign_in(client)
            assert 'data-theme="mint-garden"' in client.get('/').text
            assert client.put('/api/me/appearance',json=appearance()).status_code==200
            client.post('/api/auth/logout')
            assert 'data-theme="moon-courtyard"' in client.get('/login').text
            client.cookies.clear()
            client.cookies.set('autodev_theme','<script>')
            assert 'data-theme="mint-garden"' in client.get('/login').text
        ''')

    def test_repeated_initialization_preserves_appearance_and_model_configuration(self):
        self.check('''
        from app.services import model_settings
        with TestClient(app) as client:
            sign_in(client)
            before=model_settings.current()
            response=client.put('/api/me/appearance',json=appearance('autumn-court',density='comfortable',motion='static'))
            assert response.status_code==200,response.text
            for _ in range(3):db.init_db()
            assert client.get('/api/me/appearance').json()['appearance']['theme_id']=='autumn-court'
            assert model_settings.current()==before
            assert db.row('SELECT count(*) n FROM user_ui_preferences')['n']==1
        ''')

    def test_catalog_assets_and_template_integration_exist(self):
        self.check('''
        from pathlib import Path
        from app.config import ROOT
        items=ui_preferences.catalog()['themes']
        assert len(items)==10 and len({i['id'] for i in items})==len(items)
        assert {i['scene'] for i in items}=={'garden','moon','sky','autumn','paper','ocean','space','porcelain','arcade','gallery'}
        for item in items:
            for key in ['preview','background']:
                assert item[key].startswith('/static/themes/')
                assert (ROOT/'app'/item[key].lstrip('/')).is_file(),item[key]
            assert {'skin-bg','skin-ink','skin-surface','skin-accent'}<=item['tokens'].keys()
        with TestClient(app) as client:
            sign_in(client)
            page=client.get('/').text
            assert 'id="open-appearance"' in page and '/static/themes.css' in page and '/static/appearance-ui.js' in page
        ''')
