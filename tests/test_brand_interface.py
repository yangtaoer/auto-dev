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
    def test_media_slots_are_empty_and_do_not_autoplay_old_assets(self):
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
            self.assertEqual(config[slot], {'src': '', 'poster': ''})

    def test_theme_is_last_and_character_engine_precedes_scene(self):
        for name in ['login.html', 'index.html']:
            text = (ROOT / 'app/templates' / name).read_text('utf-8')
            tags = Elements(text).elements
            styles = [attrs['href'] for tag, attrs in tags if tag == 'link' and attrs.get('rel') == 'stylesheet']
            self.assertTrue(styles[-1].startswith('/static/mint-ui.css'))
            self.assertLess(text.index('orb-character.js'), text.index('orb-scene.js'))
            self.assertIn('type="module" src="/static/orb-scene.js', text)

    def test_character_keeps_depth_motion_accessibility_and_cleanup(self):
        scene = (ROOT / 'app/static/orb-scene.js').read_text('utf-8')
        self.assertIn('THREE.SphereGeometry', scene)
        self.assertIn('geometry.computeVertexNormals()', scene)
        self.assertIn('driver._currentPolys', scene)
        self.assertIn('IntersectionObserver', scene)
        self.assertIn('resource.dispose()', scene)
        self.assertIn("this.root.classList.contains('is-running')&&!reduced", scene)
        self.assertIn('document.hidden', scene)
        css = (ROOT / 'app/static/mint-ui.css').read_text('utf-8')
        self.assertIn('prefers-reduced-motion:reduce', css)
        self.assertIn('backdrop-filter:blur(12px)', css)
        self.assertIn('.login-shell.is-authenticating .login-panel{opacity:1!important', css)

    def test_three_is_pinned_locally_with_license_and_relative_core(self):
        folder = ROOT / 'app/static/vendor/three'
        manifest = json.loads((folder / 'SOURCE.json').read_text('utf-8'))
        self.assertEqual(manifest['version'], '0.186.1')
        self.assertIn('MIT', (folder / 'LICENSE').read_text('utf-8'))
        self.assertIn('./three.core.js', (folder / 'three.module.js').read_text('utf-8'))
        self.assertTrue((folder / 'three.core.js').is_file())


if __name__ == '__main__':
    unittest.main()
