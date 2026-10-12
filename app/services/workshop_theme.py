"""A safe component contract for custom themes, not executable uploaded CSS."""
from __future__ import annotations

COMPONENT_COLORS = {
    'skin-button-ink','skin-button-bg','skin-button-border','skin-primary-ink',
    'skin-field-bg','skin-field-ink','skin-field-border','skin-field-focus',
    'skin-card-bg','skin-card-ink','skin-card-border','skin-nav-ink','skin-nav-active-bg','skin-nav-active-ink',
    'skin-icon','skin-icon-accent',
}
COMPONENT_CHOICES = {
    'button_shape':('square','soft','pill'), 'field_shape':('square','soft','round'),
    'card_shape':('square','soft','round'), 'surface':('flat','soft','raised'),
    'icon_family':('rounded','angular','solid'), 'icon_decoration':('none','star','leaf','flower','orbit'),
    'font':('sans','serif','mono'),
}
DEFAULT_COMPONENTS = {'button_shape':'soft','field_shape':'soft','card_shape':'soft','surface':'soft',
                      'icon_family':'rounded','icon_decoration':'none','font':'sans'}
COVERAGE = ['sidebar_wallpaper','menu_and_icons','buttons','forms','cards','tables_and_dialogs']


def validate_components(value):
    if not isinstance(value,dict) or any(k not in COMPONENT_CHOICES or v not in COMPONENT_CHOICES[k] for k,v in value.items()):
        raise ValueError('组件主题只能使用已支持的形状、材质、字体和图标风格，不能上传 CSS 或脚本')
    return {**DEFAULT_COMPONENTS,**value}


def luminance(color):
    channels=[int(color[i:i+2],16)/255 for i in (1,3,5)]
    linear=[c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in channels]
    return sum(c*w for c,w in zip(linear,(.2126,.7152,.0722)))


def contrast(a,b):
    high,low=sorted((luminance(a),luminance(b)),reverse=True)
    return (high+.05)/(low+.05)


def component_tokens(palette,components):
    components=validate_components(components)
    defaults={'skin-button-ink':palette['skin-ink'],'skin-button-bg':palette['skin-surface'],
        'skin-button-border':palette['skin-line'],'skin-primary-ink':'#FFFFFF',
        'skin-field-bg':palette['skin-surface'],'skin-field-ink':palette['skin-ink'],
        'skin-field-border':palette['skin-line'],'skin-field-focus':palette['skin-accent'],
        'skin-card-bg':palette['skin-surface'],'skin-card-ink':palette['skin-ink'],'skin-card-border':palette['skin-line'],
        'skin-nav-ink':palette['skin-ink'],'skin-nav-active-bg':palette['skin-surface'],
        'skin-nav-active-ink':palette['skin-accent'],'skin-icon':palette['skin-ink'],'skin-icon-accent':palette['skin-accent']}
    tokens={k:palette.get(k,v) for k,v in defaults.items()};adjusted=[]
    for ink,bg in [('skin-primary-ink','skin-accent'),('skin-button-ink','skin-button-bg'),
                   ('skin-field-ink','skin-field-bg'),('skin-card-ink','skin-card-bg'),('skin-nav-active-ink','skin-nav-active-bg'),
                   ('skin-nav-ink','skin-sidebar')]:
        background=tokens.get(bg,palette.get(bg))
        if contrast(tokens[ink],background)<4.5:
            tokens[ink]=max(('#FFFFFF','#171717'),key=lambda value:contrast(value,background));adjusted.append(ink)
    radii={'square':'2px','soft':'9px','round':'16px','pill':'999px'}
    for component in ('button','field','card'):tokens[f'skin-{component}-radius']=radii[components[component+'_shape']]
    tokens['skin-radius']=tokens['skin-card-radius']
    tokens['skin-display']={'sans':"'Microsoft YaHei UI',system-ui,sans-serif",'serif':"Georgia,'Noto Serif SC',serif",'mono':"Consolas,'Microsoft YaHei UI',monospace"}[components['font']]
    tokens['skin-component-font']=tokens['skin-display']
    tokens['skin-shadow']={'flat':'none','soft':'0 4px 18px color-mix(in srgb,var(--skin-ink) 9%,transparent)',
        'raised':'0 8px 24px color-mix(in srgb,var(--skin-ink) 16%,transparent)'}[components['surface']]
    tokens['skin-button-shadow']='none' if components['surface']=='flat' else 'inset 0 1px 0 color-mix(in srgb,var(--skin-button-ink) 10%,transparent),0 2px 5px color-mix(in srgb,var(--skin-ink) 10%,transparent)'
    return tokens,adjusted
