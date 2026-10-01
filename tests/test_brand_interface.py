from __future__ import annotations

import json
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
    def test_login_reuses_delivery_film_and_sidebar_stays_reserved(self):
        config = json.loads((ROOT / 'app/static/brand-media.json').read_text('utf-8'))
        for template, slot in [('login.html', 'login'), ('index.html', 'sidebar')]:
            html = (ROOT / 'app/templates' / template).read_text('utf-8')
            videos = [attrs for tag, attrs in Elements(html).elements if tag == 'video']
            self.assertEqual(len(videos), 1)
            self.assertEqual(videos[0]['data-brand-video'], slot)
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
        self.assertIn("stage?.classList.contains('film-ready') && !motion.matches", script)
        self.assertIn("video.addEventListener('error'", script)
        login = (ROOT / 'app/templates/login.html').read_text('utf-8')
        self.assertIn('data-film-fallback', login)
        self.assertIn('login-panel-tagline', login)

    def test_theme_is_last_and_character_engine_precedes_scene(self):
        for name in ['login.html', 'index.html']:
            text = (ROOT / 'app/templates' / name).read_text('utf-8')
            tags = Elements(text).elements
            styles = [attrs['href'] for tag, attrs in tags if tag == 'link' and attrs.get('rel') == 'stylesheet']
            self.assertTrue(styles[-1].startswith('/static/mint-ui.css'))
            self.assertLess(text.index('orb-character.js'), text.index('orb-scene.js'))
            self.assertLess(text.index('orb-motion.js'), text.index('orb-character.js'))
            self.assertIn('type="module" src="/static/orb-scene.js', text)

    def test_character_keeps_depth_motion_accessibility_and_cleanup(self):
        scene = (ROOT / 'app/static/orb-scene.js').read_text('utf-8')
        self.assertIn('THREE.SphereGeometry', scene)
        self.assertIn('geometry.computeVertexNormals()', scene)
        self.assertIn('driver._currentPolys', scene)
        self.assertIn('normals.push(x/length,y/length,z/length)', scene)
        self.assertIn('buildEditorialDesk(paperMaterial)', scene)
        self.assertIn('new THREE.ExtrudeGeometry(shape', scene)
        self.assertIn('this.resources.add(texture)', scene)
        self.assertIn('IntersectionObserver', scene)
        self.assertIn('resource.dispose()', scene)
        self.assertIn("this.root.classList.contains('is-running')&&!reduced", scene)
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
        self.assertIn('this.orbit.position.copy(this.ball.position)', scene)
        self.assertIn('driver?.gazeX?.x', scene)
        self.assertIn('this.orb.sampleMotion(now)', scene)


if __name__ == '__main__':
    unittest.main()
