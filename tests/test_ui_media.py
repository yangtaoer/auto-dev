from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UiMediaTests(unittest.TestCase):
    def check(self, script: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, AUTODEV_ENV_FILE=str(Path(directory)/'unused.env'),
                       AUTODEV_DATA_DIR=directory, AUTODEV_ENV='development', AUTODEV_WORKER_ENABLED='0',
                       AUTODEV_ALLOWED_HOSTS='127.0.0.1,localhost,testserver', AUTODEV_SEED_DEMO='1',
                       BOOTSTRAP_ADMIN_PASSWORD='media-test-only', AUTODEV_UI_MEDIA_ENABLED='1',
                       PYTHONIOENCODING='utf-8')
            prelude = '''import json, hashlib, tempfile
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
from app.services import ui_media
'''
            result = subprocess.run([sys.executable, '-c', prelude + textwrap.dedent(script)],
                                    cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shipped_manifest_matches_sources_and_uses_permanent_hashed_unsigned_urls(self):
        self.check('''
        manifest=json.loads(ui_media.MANIFEST.read_text('utf-8'))
        bundle=ui_media.bundle()
        assert len(bundle['assets'])==len(manifest['assets'])==31
        for source,entry in manifest['assets'].items():
            assert ui_media.is_media_path(source)
            assert entry['source_sha256']==hashlib.sha256((ui_media.ROOT/'app'/source.lstrip('/')).read_bytes()).hexdigest()
            assert entry['sha256'][:20] in entry['key']
            assert entry['key'].startswith('autodev-static/v1/') and not entry['key'].startswith('autodev/')
            assert ui_media.url(source)==manifest['base_url']+'/'+entry['key']
            assert '?' not in ui_media.url(source)
        assert sum(e['bytes'] for e in manifest['assets'].values())<sum(e['source_bytes'] for e in manifest['assets'].values())*.3
        assert all(e['key'].endswith('.webp') for s,e in manifest['assets'].items() if '/themes/' in s)
        assert ui_media.url('/static/editorial-icons.svg')=='/static/editorial-icons.svg'
        assert ui_media.url('/static/app.js')=='/static/app.js'
        ''')

    def test_changed_missing_and_malformed_sources_fall_back_without_hiding_valid_assets(self):
        self.check('''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'app/static/media';folder.mkdir(parents=True)
            (folder/'clip.mp4').write_bytes(b'video');(folder/'changed.mp4').write_bytes(b'changed')
            manifest=root/'manifest.json';digest=hashlib.sha256(b'video').hexdigest()
            good={'key':'autodev-static/v1/media/clip.'+digest[:20]+'.mp4','source_sha256':digest}
            content={'version':1,'base_url':'https://example.oss-cn-chengdu.aliyuncs.com','assets':{
              '/static/media/clip.mp4':good,'/static/media/changed.mp4':good,'/static/media/missing.mp4':good,
              '/static/media/bad.mp4':{'key':good['key'],'source_sha256':42},'/static/media/no.mp4':[],
              '/static/media/../secrets.png':good,'/static/app.js':good}}
            manifest.write_text(json.dumps(content))
            with patch.object(ui_media,'ROOT',root),patch.object(ui_media,'MANIFEST',manifest):
                ui_media.bundle.cache_clear();assets=ui_media.bundle()['assets']
                assert set(assets)=={'/static/media/clip.mp4'},assets
                assert ui_media.url('/static/media/changed.mp4')=='/static/media/changed.mp4'
                for base in ['http://example.com','https://user:password@example.com','https://example.com/?key=secret',
                             'https://example.com/#fragment','https://example.com/unsafe','javascript:alert(1)']:
                    manifest.write_text(json.dumps({**content,'base_url':base}));ui_media.bundle.cache_clear()
                    assert not ui_media.bundle()['assets'],base
                for raw in ['{','null','[]']:
                    manifest.write_text(raw);ui_media.bundle.cache_clear();assert not ui_media.bundle()['assets']
        ''')

    def test_disable_switch_requires_no_credentials_or_network(self):
        self.check('''
        with patch.object(ui_media,'settings',replace(ui_media.settings,ui_media_enabled=False)):
            ui_media.bundle.cache_clear()
            assert ui_media.bundle()=={'origin':'','assets':{}}
            assert ui_media.url('/static/media/login-delivery-line.mp4')=='/static/media/login-delivery-line.mp4'
        ''')

    def test_login_and_home_first_response_use_oss_without_client_local_reset(self):
        self.check('''
        from app.main import app,login_brand_media
        from app.services import ui_preferences
        from fastapi.testclient import TestClient
        from html.parser import HTMLParser
        class Elements(HTMLParser):
            def __init__(self,text):super().__init__();self.elements=[];self.feed(text)
            def handle_starttag(self,tag,attrs):self.elements.append((tag,dict(attrs)))
        media=login_brand_media();assert media['src'].startswith(ui_media.bundle()['origin'])
        with TestClient(app) as client:
            page=client.get('/login');assert page.status_code==200
            video=next(attrs for tag,attrs in Elements(page.text).elements if tag=='video')
            assert video['src']==media['src'] and video['poster']==media['poster']
            assert 'rel="preconnect"' in page.text and 'AutoDevMedia' not in page.text
            assert 'window.__MEDIA_ASSETS__=' in page.text and '/static/media-assets.js' in page.text
            assert 'AccessKeyId' not in page.text and 'Signature=' not in page.text
            assert client.post('/api/auth/login',json={'username':'admin','password':'media-test-only'}).status_code==200
            home=client.get('/');assert home.status_code==200
            assert ui_media.url('/static/themes/backgrounds/mint-garden.png') in home.text
            assert ui_media.url('/static/brand/autodev-sidebar-mark.png') in home.text
            assert ui_preferences.catalog()['themes'][0]['preview'].startswith('/static/')
        ''')

    def test_publisher_is_scoped_optimizes_previews_and_never_changes_bucket_permissions(self):
        self.check('''
        import importlib.util
        from types import SimpleNamespace
        from PIL import Image
        spec=importlib.util.spec_from_file_location('media_publisher',ui_media.ROOT/'scripts/publish-ui-media.py')
        publisher=importlib.util.module_from_spec(spec);spec.loader.exec_module(publisher)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'app/static/themes/previews';folder.mkdir(parents=True)
            Image.new('RGB',(1672,940),'green').save(folder/'01.png')
            (folder/'SOURCE.json').write_text('not media');(folder/'secret.txt').write_text('not media')
            for name in ['brand','media','themes/backgrounds']:(root/'app/static'/name).mkdir(parents=True,exist_ok=True)
            fake_settings=replace(publisher.settings,oss_endpoint='https://oss-cn-chengdu.aliyuncs.com',
              oss_bucket='example',oss_prefix='autodev',aliyun_access_key_id='test-only',aliyun_access_key_secret='test-only')
            calls=[]
            storage=SimpleNamespace(bucket='example',oss=SimpleNamespace(PutObjectRequest=lambda **k:k),
              client=SimpleNamespace(put_object_from_file=lambda request,file:calls.append(request)))
            with patch.object(publisher,'ROOT',root),patch.object(publisher,'settings',fake_settings),\
                 patch.object(publisher,'OssArtifactStorage',return_value=storage) as create,\
                 patch.object(publisher,'verify_asset') as verify:
                plan=publisher.publish(dry_run=True);create.assert_not_called();verify.assert_not_called()
                assert len(plan['assets'])==1
                entry=next(iter(plan['assets'].values()));assert entry['bytes']<entry['source_bytes']
                result=publisher.publish();assert len(calls)==1 and verify.call_count==1
                assert calls[0]['object_acl']=='public-read' and calls[0]['content_disposition']=='inline'
                assert calls[0]['cache_control']==publisher.CACHE_CONTROL
                assert calls[0]['key'].startswith('autodev-static/v1/')
                assert not (root/'app/static/media-manifest.json').exists(),'publish() must complete before replacing manifest'
                with patch.object(publisher,'settings',replace(fake_settings,oss_prefix='autodev-static')):
                    try:publisher.publish();raise AssertionError('overlapping cleanup prefix accepted')
                    except RuntimeError as exc:assert 'overlap' in str(exc)
        ''')
