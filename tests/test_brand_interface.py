from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Elements(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict]] = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class BrandInterfaceTests(unittest.TestCase):
    def run_isolated_media_check(self, code: str) -> None:
        # Importing the application in the discovery process would load its
        # settings before workflow tests select their own disposable database.
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, AUTODEV_DATA_DIR=directory, AUTODEV_WORKER_ENABLED='0',
                       BOOTSTRAP_ADMIN_PASSWORD='brand-test-only', AUTODEV_ENV='development',
                       AUTODEV_ALLOWED_HOSTS='127.0.0.1,localhost,testserver', AUTODEV_SEED_DEMO='0',
                       PYTHONIOENCODING='utf-8')
            result = subprocess.run([sys.executable, '-c', textwrap.dedent(code)],
                                    cwd=ROOT, env=env, capture_output=True, text=True,
                                    encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_login_reuses_delivery_film_and_sidebar_stays_reserved(self):
        config = json.loads((ROOT / 'app/static/brand-media.json').read_text('utf-8'))
        for template, slot in [('login.html', 'login'), ('index.html', 'sidebar')]:
            html = (ROOT / 'app/templates' / template).read_text('utf-8')
            videos = [attrs for tag, attrs in Elements(html).elements if tag == 'video']
            self.assertEqual(len(videos), 1)
            self.assertEqual(videos[0]['data-brand-video'], slot)
            if slot == 'login':
                self.assertIn('login_media.src', videos[0]['src'])
                self.assertIn('login_media.poster', videos[0]['poster'])
                self.assertEqual(videos[0]['preload'], 'auto')
            else:
                self.assertFalse(videos[0].get('src'))
                self.assertFalse(videos[0].get('poster'))
            self.assertIn('muted', videos[0])
            self.assertIn('playsinline', videos[0])
        self.assertEqual(config['sidebar'], {'src': '', 'poster': ''})
        self.assertEqual(config['login']['layout'], 'delivery-line')
        self.assertEqual(config['login']['src'], '/static/media/login-delivery-line.mp4')
        for key in ['src', 'poster']:
            self.assertTrue((ROOT / 'app' / config['login'][key].lstrip('/')).is_file())
        script = (ROOT / 'app/static/brand-media.js').read_text('utf-8')
        self.assertIn("shell.dataset.loginLayout === 'delivery-line'", script)
        self.assertIn("!!video.getAttribute('src') && !stage?.classList.contains('film-failed') && !motion.matches", script)
        self.assertIn("video.getAttribute('src') !== media.src", script)
        self.assertLess(script.index('\n  sync();'), script.index("fetch('/static/brand-media.json'"))
        self.assertIn("video.addEventListener('error'", script)
        login = (ROOT / 'app/templates/login.html').read_text('utf-8')
        self.assertIn('data-film-fallback', login)
        self.assertIn('login-panel-tagline', login)

    def test_every_status_email_uses_mint_theme_and_preserves_escaped_business_content(self):
        self.run_isolated_media_check('''
            from app.services.delivery import Mailer
            from app.domain import TaskType
            detail = dict(id='mint-theme-test', work_item_id=1707804, title='<script>bad()</script>',
                          delivery_mode='local_package', project_name='APP & 项目', requester_name='测试',
                          created_at='2026-10-10T09:00:00+08:00', policy_snapshot={}, artifacts=[],
                          error_message='环境 <dependency> 缺失', requirement_summary='需求说明', result_summary='结果')
            variants = [({}, {}), ({'task_type': TaskType.ANALYSIS.value}, {}),
                        ({'status': 'waiting_input'}, {'action_required': True}),
                        ({'status': 'waiting_approval'}, {'action_required': True}),
                        ({'status': 'waiting_merge'}, {'action_required': True})]
            variants.extend(({}, {'terminal_status': status}) for status in ['failed', 'cancelled', 'rejected'])
            for extra, kwargs in variants:
                body = Mailer().delivery_html({**detail, **extra}, **kwargs)
                assert 'data-mail-theme="mint-editorial"' in body
                assert 'background:#d6e9dc' in body and 'color:#082c30' in body
                assert '#171813' not in body and '#e8e3d8' not in body
                assert 'max-width:720px' in body and 'max-width:600px' in body
                assert 'cid:autodev-brand-mark' in body and 'TFS #1707804' in body
                assert '<script>bad()</script>' not in body
                assert '&lt;script&gt;bad()&lt;/script&gt;' in body
                assert 'APP &amp; 项目' in body
                assert '<link' not in body and '<script' not in body
                if kwargs.get('terminal_status') == 'failed':
                    assert '#bf3e34' in body and '环境 &lt;dependency&gt; 缺失' in body
        ''')

    def test_initial_login_response_includes_valid_media_without_waiting_for_javascript(self):
        self.run_isolated_media_check('''
            from html.parser import HTMLParser
            from fastapi.testclient import TestClient
            import app.main as main
            class Elements(HTMLParser):
                def __init__(self, text):
                    super().__init__(); self.elements = []; self.feed(text)
                def handle_starttag(self, tag, attrs):
                    self.elements.append((tag, dict(attrs)))
            expected = main.login_brand_media()
            assert expected['src'] == '/static/media/login-delivery-line.mp4', expected
            assert expected['poster'] == '/static/media/login-delivery-line-poster.jpg', expected
            assert expected['layout'] == 'delivery-line', expected
            with TestClient(main.app) as client:
                response = client.get('/login')
            assert response.status_code == 200, response.text
            elements = Elements(response.text).elements
            video = next(attrs for tag, attrs in elements if tag == 'video')
            shell = next(attrs for tag, attrs in elements if attrs.get('class') == 'login-shell')
            assert video['src'] == expected['src'], video
            assert video['poster'] == expected['poster'], video
            assert video['preload'] == 'auto', video
            assert 'hidden' not in video, video
            assert shell['data-login-layout'] == expected['layout'], shell
        ''')

    def test_login_media_helper_rejects_missing_invalid_and_nonlocal_media(self):
        self.run_isolated_media_check('''
            import json
            from pathlib import Path
            import tempfile
            from unittest.mock import patch
            import app.main as main
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                media_dir = root / 'app/static/media'
                media_dir.mkdir(parents=True)
                config_file = root / 'app/static/brand-media.json'
                (media_dir / 'film.mp4').write_bytes(b'test film')
                (media_dir / 'poster.jpg').write_bytes(b'test poster')
                valid = {'src': '/static/media/film.mp4', 'poster': '/static/media/poster.jpg',
                         'layout': 'delivery-line'}
                with patch('app.main.ROOT', root):
                    assert not main.login_brand_media()['src']
                    for raw in ('{', '[]', 'null', '{"login": []}'):
                        config_file.write_text(raw, encoding='utf-8')
                        assert not main.login_brand_media()['src'], raw
                    config_file.write_text(json.dumps({'login': valid}), encoding='utf-8')
                    assert main.login_brand_media() == valid
                    for field in ('src', 'poster'):
                        for unsafe in ('https://example.com/film.mp4', '/static/media/../film.mp4',
                                       '/static/media/%2e%2e/film.mp4', '/static/brand/favicon.ico',
                                       '/static/media/missing.mp4', '/static/media/', 123, None):
                            config_file.write_text(json.dumps({'login': {**valid, field: unsafe}}), encoding='utf-8')
                            result = main.login_brand_media()
                            assert not result[field], (field, unsafe, result)
        ''')

    def test_theme_precedes_page_refinements_and_character_engine_precedes_scene(self):
        for name in ['login.html', 'index.html']:
            text = (ROOT / 'app/templates' / name).read_text('utf-8')
            tags = Elements(text).elements
            styles = [attrs['href'] for tag, attrs in tags if tag == 'link' and attrs.get('rel') == 'stylesheet']
            theme = next(index for index, href in enumerate(styles) if href.startswith('/static/mint-ui.css'))
            polish = next(index for index, href in enumerate(styles) if href.startswith('/static/orb-polish.css'))
            refinements = next(index for index, href in enumerate(styles) if href.startswith('/static/ui-refinements.css'))
            self.assertLess(theme, polish)
            self.assertLess(polish, refinements)
            if name == 'index.html':
                form = next(index for index, href in enumerate(styles) if href.startswith('/static/task-form-ui.css'))
                self.assertLess(polish, form)
                self.assertLess(form, refinements)
                loading = next(index for index, href in enumerate(styles) if href.startswith('/static/loading-ui.css'))
                self.assertLess(refinements, loading)
                self.assertLess(loading, next(i for i,href in enumerate(styles) if href.startswith('/static/themes.css')))
                self.assertLess(text.index('loading-ui.js'), text.index('src="/static/app.js'))
            else:
                self.assertLess(next(i for i,href in enumerate(styles) if href.startswith('/static/login-polish.css')),len(styles)-1)
            self.assertTrue(styles[-2].startswith('/static/themes.css'))
            self.assertTrue(styles[-1].startswith('/static/theme-components.css'))
            self.assertLess(text.index('orb-character.js'), text.index('orb-scene.js'))
            self.assertLess(text.index('orb-motion.js'), text.index('orb-character.js'))
            self.assertIn('type="module" src="/static/orb-scene.js', text)

    def test_character_keeps_depth_motion_accessibility_and_cleanup(self):
        scene = (ROOT / 'app/static/orb-scene.js').read_text('utf-8')
        garden = (ROOT / 'app/static/orb-garden.js').read_text('utf-8')
        scene_compact = ''.join(scene.split())
        garden_compact = ''.join(garden.split())
        self.assertIn('THREE.SphereGeometry', scene)
        self.assertIn('geometry.computeVertexNormals()', garden)
        self.assertIn('driver._currentPolys', scene)
        self.assertIn('Math.sqrt(Math.max(.09,1-x*x-y*y))', scene_compact)
        self.assertIn('normals.push(x,y,z)', scene_compact)
        self.assertIn('this.buildContactShadow()', scene)
        self.assertIn('scene:this.scene,ball:this.ball', scene_compact)
        self.assertIn('new AutoDevGarden(host)', scene)
        self.assertIn('this.leaves.push', garden)
        self.assertIn('mesh.castShadow=true;mesh.receiveShadow=true', garden_compact)
        self.assertRegex(scene, r'THREE\.PCF(?:Soft)?ShadowMap')
        self.assertIn('THREE.ShadowMaterial', scene)
        self.assertIn('this.contactShadow.material.opacity', scene)
        self.assertIn('this.resources.add(texture)', scene)
        self.assertIn('IntersectionObserver', scene)
        self.assertIn('resource.dispose()', scene)
        self.assertIn('this.resizeObserver?.disconnect()', scene)
        self.assertIn('this.intersection?.disconnect()', scene)
        self.assertIn('this.garden?.destroy?.()', scene)
        self.assertIn('this.orbitLights.dispose()', garden)
        self.assertIn('this.signalLights.dispose()', garden)
        self.assertIn("this.canvas.setAttribute('aria-hidden', 'true')", scene)
        self.assertIn("this.root.classList.contains('is-running')", scene)
        self.assertIn('this.orbit.visible = working', garden)
        self.assertIn('this.signals.visible = working', garden)
        self.assertIn('reduced: Boolean(this.orb.reducedMotion)', scene)
        self.assertIn('document.hidden', scene)
        css = (ROOT / 'app/static/mint-ui.css').read_text('utf-8')
        self.assertIn('prefers-reduced-motion:reduce', css)
        self.assertIn('backdrop-filter:blur(12px)', css)
        self.assertIn('.login-shell.is-authenticating .login-panel{opacity:1!important', css)
        self.assertIn('.mint-ui [hidden]{display:none!important}', css)

    def test_three_is_pinned_locally_with_license_and_relative_core(self):
        folder = ROOT / 'app/static/vendor/three'
        manifest = json.loads((folder / 'SOURCE.json').read_text('utf-8'))
        self.assertEqual(manifest['version'], '0.186.1')
        self.assertIn('MIT', (folder / 'LICENSE').read_text('utf-8'))
        self.assertIn('./three.core.js', (folder / 'three.module.js').read_text('utf-8'))
        self.assertTrue((folder / 'three.core.js').is_file())

    def test_login_password_visibility_does_not_hijack_submit(self):
        html = (ROOT / 'app/templates/login.html').read_text('utf-8')
        buttons = [attrs for tag, attrs in Elements(html).elements if tag == 'button']
        toggle = next(attrs for attrs in buttons if attrs.get('aria-controls') == 'login-password')
        self.assertEqual(toggle['type'], 'button')
        self.assertIn('aria-controls', toggle)
        self.assertIn("form.querySelector('button[type=submit]')", html)

    def test_reference_layout_keeps_data_entries_and_ordered_tabs(self):
        html = (ROOT / 'app/templates/index.html').read_text('utf-8')
        for identifier in ['record-filters', 'recent-table', 'all-table', 'project-detail-content',
                           'experience-metrics', 'request-form', 'model-settings-form', 'users-table']:
            self.assertEqual(html.count(f'id="{identifier}"'), 1)
        app = (ROOT / 'app/static/app.js').read_text('utf-8')
        panels = app[app.index("TaskDialog.render(d.id,head,["):]
        order = [panels.index(f"['{tab}',") for tab in ['overview', 'requirement', 'development', 'delivery', 'acceptance']]
        self.assertEqual(order, sorted(order))
        self.assertIn('展开技术详情', app)
        self.assertIn('project.repository_base_branches?.[name]', app)

    def test_geometric_icon_references_resolve_to_local_symbols(self):
        import re
        sprite = (ROOT / 'app/static/editorial-icons.svg').read_text('utf-8')
        symbols = set(re.findall(r'<symbol id="([^"]+)"', sprite))
        for name in ['index.html', 'login.html']:
            html = (ROOT / 'app/templates' / name).read_text('utf-8')
            for symbol in re.findall(r'/static/editorial-icons\.svg#([\w-]+)', html):
                self.assertIn(symbol, symbols)
        self.assertGreaterEqual(len(symbols), 15)

    def test_workspace_starts_with_business_content_not_hero(self):
        html = (ROOT / 'app/templates/index.html').read_text('utf-8')
        self.assertNotIn('class="page-heading"', html)
        self.assertNotIn('id="view-description"', html)
        heading = next(attrs for tag, attrs in Elements(html).elements if attrs.get('id') == 'view-title')
        self.assertEqual(heading['class'], 'sr-only')
        app = (ROOT / 'app/static/app.js').read_text('utf-8')
        self.assertNotIn("querySelector('#view-number')", app)
        self.assertIn("querySelector('#project-breadcrumb b').textContent=project.name", app)

    def test_sidebar_task_picker_is_accessible_and_reuses_detail_flows(self):
        html = (ROOT / 'app/templates/index.html').read_text('utf-8')
        elements = Elements(html).elements
        trigger = next((tag, attrs) for tag, attrs in elements if attrs.get('id') == 'orb-activity-console')
        self.assertEqual(trigger[0], 'button')
        self.assertEqual(trigger[1]['aria-controls'], 'sidebar-task-popover')
        self.assertEqual(trigger[1]['aria-expanded'], 'false')
        panel = next(attrs for tag, attrs in elements if attrs.get('id') == 'sidebar-task-popover')
        self.assertEqual(panel['role'], 'dialog')
        self.assertIn('hidden', panel)
        app = (ROOT / 'app/static/app.js').read_text('utf-8')
        picker = app[app.index('function sidebarTaskRuns()'):app.index('function syncSidebarOrbState')]
        self.assertIn('openRoutingDetail(', picker)
        self.assertIn('openDetail(run.id)', picker)
        self.assertIn('existing.get(key)', picker)
        self.assertIn("event.key==='Escape'", app)
        self.assertIn('runs.length>1&&!expanded', app)

    def test_random_animation_obeys_visibility_and_keeps_shared_depth(self):
        character = (ROOT / 'app/static/orb-character.js').read_text('utf-8')
        self.assertIn('motion.pick(this.state, this.lastGesture)', character)
        self.assertIn('this.whisperTimer, this.ambientTimer', character)
        self.assertIn('this.reducedMotion || document.hidden || !this.visible', character)
        scene = (ROOT / 'app/static/orb-scene.js').read_text('utf-8')
        garden = (ROOT / 'app/static/orb-garden.js').read_text('utf-8')
        motion = (ROOT / 'app/static/garden-motion.js').read_text('utf-8')
        # The garden director replaces unrelated random clips with a shared
        # interaction timeline. The old playlist remains only as the fallback.
        self.assertIn('this.scene?.garden || !global.AutoDevOrbMotion', character)
        self.assertIn('this.director.update(delta', scene)
        self.assertIn('paused: this.paused()', scene)
        self.assertIn('this.sceneLastAt = this.paused() ? null : now', scene)
        self.assertIn('this.garden.update(environmentFrame)', scene)
        self.assertIn('focus?.x ?? pose.gazeX', scene)
        self.assertIn('this.orb.pointer.targetX * pointerWeight', scene)
        self.assertIn('this.ball.matrixWorld.decompose(this.worldPosition, this.worldQuaternion, this.worldScale)', garden)
        self.assertIn('this.orbit.position.copy(this.worldPosition)', garden)
        self.assertRegex(garden, r'(?:applyMatrix4|multiplyMatrices)\(this\.ball\.matrixWorld[,)]')
        self.assertIn('this.crown.quaternion.copy(this.worldQuaternion)', garden)
        self.assertIn('this.crown.scale.copy(this.worldScale)', garden)
        self.assertLess(motion.index('if (reduced) {', motion.index('  update(')),
                        motion.index('if (paused) return this.frame', motion.index('  update(')))
        self.assertRegex(motion, r"return sampleGarden\(\{(?:mode: 'quiet', )?reduced: true\}\)")


if __name__ == '__main__':
    unittest.main()
