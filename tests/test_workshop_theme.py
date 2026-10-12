import unittest
from app.services.workshop_theme import component_tokens,validate_components,contrast

PALETTE={'skin-ink':'#302321','skin-surface':'#FFFCF7','skin-line':'#E3C9B9','skin-accent':'#C62828','skin-sidebar':'#F5E2D6'}


class WorkshopThemeTests(unittest.TestCase):
    def test_legacy_theme_defaults_are_neutral_not_inherited_paper_or_mint(self):
        tokens,_=component_tokens(PALETTE,{})
        self.assertEqual(tokens['skin-field-focus'],'#C62828')
        self.assertEqual(tokens['skin-button-radius'],'9px')
        self.assertIn('sans-serif',tokens['skin-component-font'])
        self.assertGreaterEqual(contrast(tokens['skin-primary-ink'],PALETTE['skin-accent']),4.5)

    def test_material_shape_font_and_roles_are_not_just_a_wallpaper_change(self):
        palette={**PALETTE,'skin-field-bg':'#251010','skin-field-ink':'#221111','skin-nav-active-bg':'#AA1111','skin-nav-active-ink':'#111111'}
        tokens,adjusted=component_tokens(palette,{'button_shape':'pill','field_shape':'square','card_shape':'round','surface':'raised','font':'serif'})
        self.assertEqual(tokens['skin-button-radius'],'999px');self.assertEqual(tokens['skin-field-radius'],'2px')
        self.assertEqual(tokens['skin-card-radius'],'16px');self.assertIn('Georgia',tokens['skin-component-font'])
        for ink,bg in [('skin-field-ink','skin-field-bg'),('skin-nav-active-ink','skin-nav-active-bg')]:
            self.assertIn(ink,adjusted);self.assertGreaterEqual(contrast(tokens[ink],tokens[bg]),4.5)

    def test_only_supported_data_not_code_or_external_icons(self):
        for value in [{'button_shape':'url(https://evil)'},{'css':'body{}'},{'icon_family':'<svg onload>'},{'font':[]}]:
            with self.subTest(value=value),self.assertRaises(ValueError):validate_components(value)


if __name__=='__main__':unittest.main()
