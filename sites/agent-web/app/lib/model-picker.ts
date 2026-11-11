type SearchableModel={provider:string;model:string;display_name:string;family?:string;legacy?:boolean};
export function filterModels<T extends SearchableModel>(models:T[],provider:string,query:string,showLegacy=true):T[]{
 const term=query.trim().toLowerCase();
 return models.filter(m=>(provider==="all"||m.provider===provider)&&(showLegacy||!m.legacy||!!term)&&`${m.display_name} ${m.model} ${m.provider} ${m.family||""}`.toLowerCase().includes(term));
}
