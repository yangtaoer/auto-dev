"""Component coverage contracts. Actual visual QA is performed in the browser."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ThemeComponentContracts(unittest.TestCase):
    def test_each_theme_has_button_card_table_and_icon_dialects(self):
        css = (ROOT/'app/static/theme-components.css').read_text(encoding='utf-8')
        themes = json.loads((ROOT/'app/static/themes/catalog.json').read_text(encoding='utf-8'))['themes']
        for theme in themes:
            rules = [r.split('}',1)[0] for r in css.split('\n') if 'data-theme='+theme['id']+']' in r]
            with self.subTest(theme=theme['id']):
                self.assertTrue(any('.btn-primary' in r for r in rules))
                self.assertTrue(any('.run-card' in r and 'background' in r and 'border-radius' in r for r in rules))
                self.assertTrue(any('.recent-task-table th' in r and 'border-bottom' in r for r in rules))
                self.assertTrue(any('.theme-icon' in r and 'stroke' in r for r in rules))
                self.assertTrue(any('.btn' in r and ':hover' in r for r in rules))

    def test_both_pages_ship_new_components_and_business_icons_keep_their_meaning(self):
        for name in ['index','login']:
            self.assertIn('/static/theme-components.css?v={{ app_version }}',(ROOT/f'app/templates/{name}.html').read_text(encoding='utf-8'))
        page=(ROOT/'app/templates/index.html').read_text(encoding='utf-8')
        self.assertLess(page.index('/static/theme-icons.js'),page.index('/static/app.js'))
        source=(ROOT/'app/static/app.js').read_text(encoding='utf-8')
        self.assertIn('AutoDevThemeIcons?.markup(name)',source)

    def test_presentation_preserves_reduced_motion_and_opaque_login(self):
        css=(ROOT/'app/static/theme-components.css').read_text(encoding='utf-8')
        self.assertIn('@media(prefers-reduced-motion:reduce)',css)
        self.assertIn('html[data-motion=static]',css)
        self.assertNotIn('.login-panel{background:transparent',css)
        self.assertIn('.btn-danger{background:var(--skin-danger-soft)',css)

    def test_header_and_dashboard_dialects_override_legacy_id_rules(self):
        css=(ROOT/'app/static/theme-components.css').read_text(encoding='utf-8')
        self.assertIn(':is(.btn-primary,#open-request,.btn-primary#request-submit-button)',css)
        self.assertIn('#view-dashboard .control-strip{border-radius:var(--dialect-radius)',css)
        self.assertIn("'SimSun',serif!important",css)
