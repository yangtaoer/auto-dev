/* Small, testable appearance state. The account server is the source of truth. */
export const DEFAULT_APPEARANCE = Object.freeze({theme_id:'mint-garden',font_size:'normal',density:'compact',motion:'full'});
const choices={font_size:['normal','large'],density:['compact','comfortable'],motion:['full','reduced','static']};
export function normalizeAppearance(value={},themes=[]){
  if(!value||typeof value!=='object'||Array.isArray(value))value={};
  const result={...DEFAULT_APPEARANCE};
  result.theme_id=themes.some(t=>t.id===value.theme_id)?value.theme_id:(themes[0]?.id||DEFAULT_APPEARANCE.theme_id);
  for(const key of Object.keys(choices))if(choices[key].includes(value[key]))result[key]=value[key];
  return result;
}
export function selectedTheme(id,themes){return themes.find(t=>t.id===id)||themes[0];}
export class AppearanceSession{
  constructor({themes,saved,persist,render}){this.themes=themes;this.saved=normalizeAppearance(saved,themes);this.draft={...this.saved};this.persist=persist;this.render=render;this.busy=false;this.open=false;}
  begin(){if(this.busy)return false;this.open=true;this.draft={...this.saved};return true;}
  change(patch){if(this.busy||!this.open)return false;this.draft=normalizeAppearance({...this.draft,...patch},this.themes);this.render(this.draft,selectedTheme(this.draft.theme_id,this.themes));return true;}
  cancel(){if(this.busy)return false;this.draft={...this.saved};this.open=false;this.render(this.saved,selectedTheme(this.saved.theme_id,this.themes));return true;}
  async save(){
    if(this.busy||!this.open)return false;
    this.busy=true;
    const submitted={...this.draft};
    try{const result=await this.persist(submitted);this.saved=normalizeAppearance(result||submitted,this.themes);this.draft={...this.saved};this.open=false;this.render(this.saved,selectedTheme(this.saved.theme_id,this.themes));return true;}
    finally{this.busy=false;}
  }
}
