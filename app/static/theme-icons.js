/* Hand-drawn icon dialects. Semantics stay stable; silhouette and penwork do
 * not. Trusted local path data only, with no uploaded HTML or arbitrary SVG. */
(function(global){
  const rounded={
    home:'M3 10 12 3l9 7M5 9v11h5v-6h4v6h5V9',
    clipboard:'M8 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 3h6v4H9ZM8 12h8M8 16h5',
    cube:'m12 3 9 5v9l-9 5-9-5V8Zm-9 5 9 5 9-5M12 13v9',
    chart:'M5 19v-6a2 2 0 0 1 4 0v6M11 19V8a2 2 0 0 1 4 0v11M17 19V4a2 2 0 0 1 4 0v15M3 21h19',
    user:'M8 7a4 4 0 1 0 8 0 4 4 0 0 0-8 0M4 21v-2a8 8 0 0 1 16 0v2',
    book:'M12 6c-3-3-6-3-10-2v15c4-1 7-1 10 2 3-3 6-3 10-2V4c-4-1-7-1-10 2Zm0 0v15',
    gear:'M9 3h6l1 3 3 1 2 5-2 2-1 4-3 1-1 3h-4l-1-3-3-1-1-4-2-2 2-5 3-1Zm0 9a3 3 0 1 0 6 0 3 3 0 0 0-6 0',
    plus:'M12 4v16M4 12h16',search:'M3 10a7 7 0 1 0 14 0 7 7 0 0 0-14 0m12 5 7 7',
    check:'m4 12 5 5L20 5',layers:'m12 3 10 5-10 5L2 8Zm-10 9 10 5 10-5M2 16l10 5 10-5',
    code:'m9 7-5 5 5 5m6-10 5 5-5 5M13 5l-2 14',clock:'M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0m9-6v6l4 2',
  };
  const angular={...rounded,
    home:'m3 10 9-8 9 8M5 9v12h5v-7h4v7h5V9M3 21h18',
    clipboard:'M8 4H4v18h16V4h-4M8 2h8v5H8ZM8 12h8M8 16h5',
    cube:'m12 2 10 6v9l-10 5L2 17V8Zm-10 6 10 5 10-5M12 13v9M7 5l10 5',
    chart:'M4 20V12h3v8M10 20V7h3v13M16 20V3h3v17M2 22h20',
    user:'M8 3h8v8H8ZM5 21v-6l3-2h8l3 2v6Z',
    book:'M2 3h7l3 3 3-3h7v17h-7l-3 2-3-2H2ZM12 6v16',
    gear:'M8 2h8v4l4 2v8l-4 2v4H8v-4l-4-2V8l4-2Zm1 7v6h6V9Z',
  };
  const pixel={...angular,
    home:'M2 10h2V8h2V6h2V4h2V2h4v2h2v2h2v2h2v2h2v2h-4v10h-5v-6h-2v6H6V12H2Z',
    clipboard:'M8 2h8v2h4v18H4V4h4Zm0 6h8V6H8Zm0 4v2h8v-2Zm0 5v2h6v-2Z',
    cube:'M10 2h4v2h4v2h4v12h-4v2h-4v2h-4v-2H6v-2H2V6h4V4h4Zm-6 6v8h2v2h4v-6H8v-2H6V8Zm10 4v6h4v-2h2V8h-2v2h-2v2Z',
    chart:'M2 22V10h4v12Zm7 0V6h4v16Zm7 0V2h4v20Z',
    user:'M8 2h8v2h2v6h-2v2H8v-2H6V4h2ZM6 14h12v2h2v6H4v-6h2Z',
    book:'M2 2h8v2h4V2h8v18h-8v2h-4v-2H2Zm4 4v10h4V6Zm8 0v10h4V6Z',
    gear:'M8 2h8v4h4v4h2v4h-2v4h-4v4H8v-4H4v-4H2v-4h2V6h4Zm0 8v4h2v2h4v-2h2v-4h-2V8h-4v2Z',
    plus:'M10 2h4v8h8v4h-8v8h-4v-8H2v-4h8Z',search:'M6 2h8v2h4v10h-2v2h2v2h2v2h2v2h-4v-2h-2v-2h-2v-2H6v-2H2V6h2V4h2Zm0 4v8h8V6Z',
    check:'M2 10h4v4h4v-4h4V6h4V2h4v6h-4v4h-4v4h-4v4H6v-4H2Z',
  };
  const solid={...rounded,
    home:'m2 10 10-8 10 8v2h-3v10h-5v-7h-4v7H5V12H2Z',
    clipboard:'M7 4V2h10v2h4v18H3V4Zm2 0v3h6V4ZM7 11v2h10v-2Zm0 5v2h7v-2Z',
    cube:'m12 2 10 6v9l-10 5-10-5V8Zm-7 7v6l6 4v-6Zm8 4v6l6-4V9Z',
    chart:'M3 22V12h4v10Zm7 0V7h4v15Zm7 0V2h4v20Z',
    user:'M7 7a5 5 0 1 1 10 0 5 5 0 0 1-10 0M3 22v-2a9 9 0 0 1 18 0v2Z',
    book:'M2 3h8l2 2 2-2h8v17h-8l-2 2-2-2H2Zm4 4v9h4V7Zm8 0v9h4V7Z',
    gear:angular.gear,
  };
  const dialects={
    'mint-garden':{family:'botanical',paths:rounded,stamp:'M18 20q-1-6 5-6-1 5-5 6m0 0 3-3'},
    'moon-courtyard':{family:'astral',paths:rounded,stamp:'M19 2a3 3 0 1 0 4 4 4 4 0 0 1-4-4M2 18h2m-1-1v2'},
    'sky-atelier':{family:'aerodynamic',paths:rounded,stamp:'M17 3h5M19 1h3M17 21q4 0 5-2'},
    'autumn-court':{family:'engraved',paths:rounded,stamp:'M18 21q-4-4 0-7 4-3 6 0-1 4-6 7m0 0 3-5'},
    'paper-workshop':{family:'folded',paths:angular,stamp:'M17 2h5v5Zm0 0v5h5'},
    'deep-ocean':{family:'sonar',paths:rounded,stamp:'M2 20q5 4 10 0t10 0M18 2a4 4 0 0 1 4 4'},
    'orbital-station':{family:'instrument',paths:angular,stamp:'M1 6V1h5M18 23h5v-5M20 2v4m-2-2h4'},
    'porcelain-studio':{family:'blueware',paths:rounded,stamp:'M19 21q5-1 3-4t-4 1q-2 2-3 0M2 5q-1-4 3-3'},
    'pixel-arcade':{family:'pixel',paths:pixel,solid:true},
    'ink-gallery':{family:'swiss',paths:solid,solid:true},
  };
  const known=new Set([...Object.keys(rounded),'help','lock','eye','eye-off','arrow-right','chevron-right','message','network','phone','building','refresh','more']);
  let theme=typeof document!=='undefined'?document.documentElement.dataset.theme:'mint-garden';
  function glyph(name,id=theme){
    const dialect=dialects[id]||dialects['mint-garden'];
    const d=dialect.paths[name];
    if(!d)return `<use href="/static/editorial-icons.svg#${known.has(name)?name:'help'}"></use>`;
    return `<path class="theme-glyph" d="${d}"${dialect.solid?' fill="currentColor" fill-rule="evenodd" stroke="none"':''}></path>${dialect.stamp?`<path class="theme-icon-stamp" d="${dialect.stamp}"/>`:''}`;
  }
  function markup(name){const family=(dialects[theme]||dialects['mint-garden']).family;return `<svg class="ui-icon theme-icon" viewBox="0 0 24 24" data-icon-name="${known.has(name)?name:'help'}" data-icon-family="${family}" aria-hidden="true">${glyph(name)}</svg>`;}
  function apply(id){
    theme=id;
    if(typeof document==='undefined')return;
    const family=(dialects[id]||dialects['mint-garden']).family;
    document.querySelectorAll('svg.nav-symbol,svg.ui-icon').forEach(icon=>{
      const source=icon.dataset.iconName||icon.querySelector('use')?.getAttribute('href')?.split('#')[1];
      if(!source||!dialects[id]?.paths[source])return;
      icon.dataset.iconName=source;icon.dataset.iconFamily=family;icon.setAttribute('viewBox','0 0 24 24');icon.classList.add('theme-icon');icon.innerHTML=glyph(source,id);
    });
  }
  const api={dialects,glyph,markup,apply};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else {global.AutoDevThemeIcons=api;apply(theme);document.addEventListener('autodev:appearance',event=>apply(event.detail.theme.id));}
})(typeof window==='undefined'?globalThis:window);
