'use strict';
/* Navigation and explicit lifecycle actions. The tour never executes an action. */
const modes08={initial:'Cerveau vierge',legacy:'Ancienne s\u00e9ance conserv\u00e9e',fresh:'Nouvelle exp\u00e9rience',replay:'Relecture depuis z\u00e9ro',continue:'Apprentissage prolong\u00e9'};
let loadedSession08,successTimer08,tourStep08=-1,lastFocus08=null;
const originalRender08=render;
function node08(tag,text,cls){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;}
function success08(text){$('success08').textContent=text;$('success08').hidden=false;clearTimeout(successTimer08);successTimer08=setTimeout(()=>$('success08').hidden=true,6500);}
function check08(title,rows,warning,yes='Confirmer'){
  const d=$('confirm-dialog');if(d.open)return Promise.resolve(false);
  lastFocus08=document.activeElement;$('confirm-title').textContent=title;$('confirm-body').replaceChildren();
  rows.forEach(([head,body])=>{const p=node08('p','');p.append(node08('b',head+' : '),document.createTextNode(body));$('confirm-body').append(p);});
  if(warning)$('confirm-body').append(node08('p',warning,'warning08'));
  $('confirm-yes').textContent=yes;d.returnValue='cancel';d.showModal();
  return new Promise(resolve=>d.addEventListener('close',()=>{lastFocus08?.focus();resolve(d.returnValue==='confirm');},{once:true}));
}
selectTab=function(x,push=true){
  if(!['live','train','guide'].includes(x))x='live';tab=x;
  for(const [key,id] of [['live','live-panel'],['train','train-panel'],['guide','guide-panel']]){
    $(id).hidden=x!==key;const b=$('tab-'+key);b.classList.toggle('selected',x===key);if(x===key)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');
  }
  $('workspace').classList.toggle('workspace-guide',x==='guide');
  $('brain-location').textContent=x==='live'?'march\u00e9 : copie gel\u00e9e':'atelier : dernier exemple';
  if(push)history.replaceState(null,'',x==='guide'?'/guide':x==='train'?'/training':'/');
  render();
};
$('tab-live').onclick=()=>{closeTour08();selectTab('live');};$('tab-train').onclick=()=>{closeTour08();selectTab('train');};$('tab-guide').onclick=$('open-guide').onclick=()=>{closeTour08();selectTab('guide');window.scrollTo({top:0,behavior:'smooth'});};
window.addEventListener('popstate',()=>selectTab(location.pathname==='/guide'?'guide':location.pathname==='/training'?'train':'live',false));
function jump08(id){selectTab('train');const target=$(id);if(target instanceof HTMLDetailsElement)target.open=true;target.scrollIntoView({behavior:'smooth',block:'start'});}
document.querySelectorAll('[data-jump]').forEach(b=>b.onclick=()=>jump08(b.dataset.jump));
function formConfig08(){
  const form=$('protocol');if(!form.reportValidity())throw Error('V\u00e9rifie les champs du formulaire.');const d=new FormData(form),p={};
  for(const k of ['n_kc','seed','sparsity','epochs','epsilon','learning_rate','max_windows','batch'])p[k]=Number(d.get(k));
  for(const k of ['mode','signal'])p[k]=d.get(k);
  for(const k of ['use_liquidity','shuffle_train'])p[k]=form.elements[k].checked;
  p.economic_head=false;
  return p;
}
const economicHead08=$('protocol').elements.economic_head;
economicHead08.checked=false;economicHead08.disabled=true;
economicHead08.closest('label').lastElementChild.textContent='Désactivée pour le protocole scientifique H / S / B.';
function syncConfig08(){if(!T||loadedSession08===T.session)return;loadedSession08=T.session;for(const[k,v]of Object.entries(T.config)){const e=$('protocol').elements[k];if(!e)continue;if(e.type==='checkbox')e.checked=v;else {e.value=String(v);if(e.tagName==='SELECT'&&e.selectedIndex<0){const option=[...e.options].find(o=>typeof v==='number'&&Number(o.value)===v);if(option)e.value=option.value;}}}}
function readyHint08(){
  if(!T||!S)return 'Lecture de l\u2019\u00e9tat du serveur...';
  if(!T.corpus.windows)return 'Prochaine action : collecte ou importe des observations, puis copie-les dans le corpus.';
  if(!T.session)return 'Le corpus existe. V\u00e9rifie les donn\u00e9es, puis pr\u00e9pare une nouvelle exp\u00e9rience. Le cerveau partira de z\u00e9ro.';
  if(T.auto)return 'La s\u00e9ance avance. Mettre en pause conservera exactement la position et les poids.';
  if(!T.done)return `S\u00e9ance en pause : ${T.position} / ${T.total}. Reprendre utilise les poids actuels, sans recommencer.`;
  if(!T.test_opened)return 'Apprentissage et calibration termin\u00e9s. Tu peux maintenant consulter le test gel\u00e9 ou exporter les r\u00e9sultats.';
  return 'Test termin\u00e9. Compare au choix toujours stable, puis exporte. D\u00e9ployer reste une d\u00e9cision explicite, pas une validation de performance.';
}
function renderClarity08(){
  syncConfig08();
  if(T){
    const done=T.done,paused=!T.auto,has=Boolean(T.session),n=T.weights_updates||0,quality=T.corpus.quality;
    $('identity-training').textContent=(modes08[T.lineage?.mode]||'Atelier')+' \u00b7 '+f(n,0)+' corrections apprises';
    $('identity-training-note').textContent=`${f(T.config.n_kc,0)} KC \u00b7 graine ${T.config.seed} \u00b7 poids ${T.brain_id} \u00b7 ${has?(T.auto?'en cours':done?'termin\u00e9':'en pause'):'aucune s\u00e9ance'}`;
    const start=has&&!done&&paused;for(const id of ['resume-training','train-start','train-step'])$(id).disabled=!start;
    $('train-pause').disabled=!T.auto;$('replay-training').disabled=!has||!paused;$('continue-training').disabled=!done||!paused;
    $('reset-training').disabled=!paused;$('preview-data').disabled=T.auto;$('protocol').querySelector('[type=submit]').disabled=T.auto;
    $('test').disabled=!done||T.test_opened||!paused;
    $('lifecycle-reason').textContent=readyHint08();$('next-action').textContent=readyHint08();$('guide-next').textContent=readyHint08();
    $('progress-label').textContent=has?`${T.position} / ${T.total} pr\u00e9sentations \u2014 ${T.auto?'en cours':done?'s\u00e9ance termin\u00e9e':'en pause'}`:'Aucune s\u00e9ance pr\u00e9par\u00e9e';
    $('phase').textContent=T.auto?'Phase : '+(T.next_phase||'termin\u00e9e'):!done&&has?'Prochain exemple : '+T.next_phase:'';
    const c=T.calibration,cn=c?.n||0,min=c?.min_total||60;
    $('calibration-help').textContent=cn===0?'Calibration : pas encore construite. Elle se calcule apr\u00e8s l\u2019apprentissage ; les scores neuronaux seuls ne suffisent pas pour miser.':
      `Calibration : ${cn} fen\u00eatres, minimum technique ${min}. `+(cn<min?'Trop peu pour autoriser une mise ; collecte davantage.':'Minimum global atteint ; certains groupes peuvent encore manquer d\u2019exemples. Aucune garantie de gain.');
    $('test-exposure').hidden=!(T.test_reused||T.train_previously_tested);
    $('test-exposure').textContent=`Tra\u00e7abilit\u00e9 : ${T.test_reused||0} fen\u00eatres de ce TEST ont d\u00e9j\u00e0 \u00e9t\u00e9 ouvertes, et ${T.train_previously_tested||0} fen\u00eatres TRAIN appartenaient \u00e0 un ancien test. Rejouer ou r\u00e9initialiser n\u2019en fait pas des donn\u00e9es inconnues. Pour une \u00e9valuation finale, utilise de nouvelles dates.`;
    if(quality)$('corpus').textContent=`${f(T.corpus.windows,0)} exercices uniques \u00b7 ${f(quality.liquid,0)} avec carnet utilisable`;
    const steps=[['Donn\u00e9es',Boolean(T.corpus.windows)],['S\u00e9ance',has],['Apprendre',has&&(done||['Calibration gelee','Test gele'].includes(T.next_phase))],['Calibrer',cn>0],['Tester',T.test_opened&&done],['Utiliser',Boolean(S?.deployment?.session===T.session&&has)]];
    let active=steps.findIndex(x=>!x[1]);if(active<0)active=5;const journey=$('journey-steps');journey.replaceChildren();
    steps.forEach(([name,ok],i)=>journey.append(node08('div',`${String(i+1).padStart(2,'0')} ${name}`,`journey-step ${ok?'complete':''} ${i===active?'active':''}`)));
    const current=$('guide-current');current.replaceChildren();metric(current,'exercices dans le corpus',f(T.corpus.windows,0));metric(current,'corrections du cerveau atelier',f(n,0));metric(current,'position de la s\u00e9ance',`${T.position} / ${T.total}`);metric(current,'calibration',`${cn} / ${min} min.`);
    if(tab==='train')$('updates').textContent=`${f(n,0)} touche / ${f(T.value_updates||0,0)} valeur`;
  }
  if(S){
    const n=S.stats.updates||0,config=S.brain_config||{};
    $('identity-live').textContent=(n?'Cerveau appris':'Cerveau vierge')+' \u00b7 copie gel\u00e9e \u00b7 '+f(n,0)+' corrections';
    $('identity-live-note').textContent=`${f(config.n_kc||2048,0)} KC \u00b7 poids ${S.brain_id} \u00b7 ${S.deployment?'copi\u00e9 depuis l\u2019atelier':'pas de copie d\u00e9ploy\u00e9e'} \u00b7 portefeuille ${euro(S.stats.capital)}`;
    const stopped=!S.collect&&!S.policy_enabled&&!S.pending;
    $('reset-live-brain').disabled=!stopped;$('reset').disabled=!stopped;
    if(T)$('deploy').disabled=!T.done||T.auto||!stopped;
    $('deploy').title=stopped?'Copie les poids et la calibration ; nouveau portefeuille fictif.':'Arr\u00eate collecte et politique, puis attends la fin des fen\u00eatres.';
  }
  if(tab==='train'){
    $('last-signal').textContent='Les barres financi\u00e8res ci-dessous appartiennent au march\u00e9, pas \u00e0 la s\u00e9ance.';
    $('gainloss').hidden=true;$('last-signal').textContent='R\u00e8glements du march\u00e9 masqu\u00e9s ici pour ne pas les confondre avec l\u2019apprentissage.';
  }else $('gainloss').hidden=false;
}
render=function(){originalRender08();renderClarity08();};
// Direction colors are distinct from financial gain/loss colors. Zero is centered.
bars=function(id,values,labels=names,signed=true){
  const parent=$(id);parent.replaceChildren();values.forEach((v,i)=>{
    const label=node08('div','','bar-label');label.append(node08('span',labels[i]),node08('b',f(v,3)));const track=node08('div','','track'+(signed?' signed':''));
    const fill=document.createElement('i'),m=Math.min(1,Math.abs(v||0));fill.style.width=m*(signed?50:100)+'%';
    if(signed)fill.style.left=(v<0?50-m*50:50)+'%';
    fill.style.background=signed?['var(--up)','var(--flat)','var(--down)'][i%3]:i===0?'var(--gain)':'var(--red)';
    track.append(fill);parent.append(label,track);
  });
};
function showPreflight08(d){
  const p=$('preflight');p.hidden=false;p.replaceChildren(node08('b','V\u00e9rification : tes donn\u00e9es, sans changer le cerveau'));
  p.append(node08('p',`${d.eligible} exercices admissibles sur ${d.considered} lignes r\u00e9centes examin\u00e9es (${d.filtered} filtr\u00e9es). ${d.presentations} pr\u00e9sentations pr\u00e9vues, dont ${d.train_presentations} avec apprentissage.`));
  p.append(node08('p',`TRAIN ${d.split.splits.train.n} \u00b7 CALIBRATION ${d.calibration_n} \u00b7 TEST ${d.split.splits.test.n} (r\u00e9sultats cach\u00e9s). ${d.split.purged} fen\u00eatres retir\u00e9es aux fronti\u00e8res.`));
  if(d.calibration_n<d.calibration_min)p.append(node08('p',`Tu peux essayer le m\u00e9canisme, mais ${d.calibration_n} < ${d.calibration_min} : le transfert ne permettra pas de miser. Ajoute des donn\u00e9es pour une calibration suffisante.`, 'warning08'));
  if(d.reused_test||d.reused_in_train)p.append(node08('p',`${d.reused_test} fen\u00eatres TEST d\u00e9j\u00e0 consult\u00e9es ; ${d.reused_in_train} anciennes fen\u00eatres de test dans TRAIN. Ce nouveau d\u00e9coupage ne cr\u00e9e pas un test inconnu.`, 'warning08'));
}
$('preview-data').onclick=()=>action(async()=>{showPreflight08(await api('/api/training/preview',formConfig08()));});
$('protocol').onsubmit=e=>{e.preventDefault();action(async()=>{
  const p=formConfig08(),pre=await api('/api/training/preview',p);showPreflight08(pre);
  if(!await check08('Pr\u00e9parer avec un cerveau neuf ?',[
    ['Remplac\u00e9s','Les poids, la calibration et le plan de la s\u00e9ance atelier active. Son \u00e9tat est archiv\u00e9 avant remplacement.'],
    ['Conserv\u00e9s','Ton corpus, la collecte, le cerveau du march\u00e9 et le portefeuille.'],
    ['Nouveau d\u00e9part',`${p.n_kc} KC, graine ${p.seed}, ${pre.presentations} pr\u00e9sentations. Rien ne d\u00e9marre tout seul.`]],
    pre.calibration_n<60?'Calibration trop petite pour miser. Cette exp\u00e9rience reste utile pour observer le fonctionnement.':'Un minimum technique de donn\u00e9es ne garantit pas des r\u00e9sultats fiables.','Pr\u00e9parer depuis z\u00e9ro'))return;
  await api('/api/training/create',p);loadedSession08=undefined;success08('Cerveau neuf pr\u00e9par\u00e9. Clique D\u00e9marrer / reprendre pour apprendre.');refreshArchives08();
});};
$('resume-training').onclick=$('train-start').onclick=()=>action(()=>api('/api/training/control',{action:'start'}));
$('replay-training').onclick=()=>action(async()=>{
  if(!await check08('Rejouer la m\u00eame exp\u00e9rience depuis z\u00e9ro ?',[
    ['Conserv\u00e9s','Les exemples et leur ordre, la graine et les r\u00e9glages du protocole sauvegard\u00e9. Les nouveaux imports ne sont pas ajout\u00e9s.'],
    ['Recr\u00e9\u00e9s','Un cerveau initial et une calibration vide, dans une nouvelle s\u00e9ance. L\u2019ancienne est archiv\u00e9e.'],
    ['Inchang\u00e9s','Le march\u00e9, le capital et le corpus.']],
    'Les champs modifi\u00e9s dans le formulaire ne sont pas utilis\u00e9s ici. Pour changer les r\u00e9glages, pr\u00e9pare une nouvelle exp\u00e9rience.','Rejouer depuis z\u00e9ro'))return;
  await api('/api/lifecycle',{confirm:true,action:'replay'});success08('S\u00e9ance recr\u00e9\u00e9e depuis z\u00e9ro, en pause.');refreshArchives08();
});
$('continue-training').onclick=()=>action(async()=>{
  if(!await check08('Garder ce cerveau et lui faire apprendre encore ?',[
    ['Conserv\u00e9s','Les poids d\u00e9j\u00e0 appris et les r\u00e9glages du cerveau.'],
    ['Ajout\u00e9','Un passage sur le TRAIN de cette s\u00e9ance uniquement, puis une nouvelle calibration sur sa partition conserv\u00e9e.'],
    ['Exclus','Aucun exemple CALIBRATION ou TEST ne sert aux corrections. Les nouvelles donn\u00e9es du corpus ne sont pas incluses.']],
    'R\u00e9p\u00e9ter des exercices peut renforcer leur m\u00e9morisation sans am\u00e9liorer les pr\u00e9visions sur de nouvelles donn\u00e9es.','Continuer avec les poids appris'))return;
  await api('/api/lifecycle',{confirm:true,action:'continue',passes:1});success08('Poids conserv\u00e9s. Un passage suppl\u00e9mentaire est pr\u00eat, en pause.');refreshArchives08();
});
$('reset-training').onclick=()=>action(async()=>{
  const c=formConfig08();if(!await check08('Cerveau initial dans l\u2019atelier ?',[
    ['Remplac\u00e9s','L\u2019apprentissage, la calibration et la s\u00e9ance active de l\u2019atelier. Une archive est cr\u00e9\u00e9e.'],
    ['Conserv\u00e9s','Tout le corpus, la collecte et le cerveau utilis\u00e9 sur le march\u00e9. Le capital ne change pas.'],
    ['Construction',`${c.n_kc} KC, graine ${c.seed}, ${pct(c.sparsity)} d\u2019activit\u00e9 cibl\u00e9e ; capteurs de liquidit\u00e9 ${c.use_liquidity?'activ\u00e9s':'d\u00e9sactiv\u00e9s'}.`]],
    'Il restera ensuite \u00e0 pr\u00e9parer une nouvelle exp\u00e9rience. Rien n\u2019est entra\u00een\u00e9 automatiquement.','Recr\u00e9er l\u2019atelier vierge'))return;
  await api('/api/lifecycle',{confirm:true,action:'reset_training',config:c});loadedSession08=undefined;success08('Atelier vierge. Tes donn\u00e9es et ton cerveau du march\u00e9 sont conserv\u00e9s.');refreshArchives08();
});
$('reset-live-brain').onclick=()=>action(async()=>{
  const c=S?.brain_config||{seed:42,n_kc:2048,sparsity:.05,use_liquidity:true};
  if(!await check08('Remplacer seulement le cerveau du march\u00e9 ?',[
    ['Remplac\u00e9s','Les poids et la calibration de la copie live. Son ancien \u00e9tat est archiv\u00e9 dans la base.'],
    ['Conserv\u00e9s','Le portefeuille et ses r\u00e9sultats, les observations, le corpus et la s\u00e9ance atelier.'],
    ['Construction',`M\u00eames param\u00e8tres que le cerveau live actuel : ${c.n_kc} KC et graine ${c.seed}.`]],
    'Ce cerveau non calibr\u00e9 ne pourra pas miser. Pour utiliser un cerveau appris, choisis le bouton de d\u00e9ploiement.','Recr\u00e9er le cerveau live'))return;
  await api('/api/lifecycle',{confirm:true,action:'reset_live',config:c});success08('Cerveau live vierge. Aucun changement de capital ni suppression de donn\u00e9es.');
});
$('reset').onclick=()=>action(async()=>{
  if(await check08('Remettre seulement le portefeuille \u00e0 20 \u20ac ?',[
    ['Remis \u00e0 z\u00e9ro','Le r\u00e9sultat et les compteurs de la session financi\u00e8re. Ancien \u00e9tat archiv\u00e9.'],
    ['Conserv\u00e9s','Les poids du cerveau live, sa calibration, le corpus et l\u2019atelier.']],
    'Ce bouton ne recr\u00e9e PAS un cerveau initial.','Nouveau portefeuille fictif')){await api('/api/reset',{confirm:true});success08('Portefeuille \u00e0 20 \u20ac fictifs. Cerveau inchang\u00e9.');}
});
$('test').onclick=()=>action(async()=>{
  if(await check08('Consulter le test sans apprendre ?',[
    ['Conserv\u00e9s','Les poids et la calibration. Aucune correction neuronale sur ces exemples.'],
    ['Mesur\u00e9s','Les choix du cerveau sur la derni\u00e8re partition chronologique.']],
    'Apr\u00e8s consultation, ce test est connu. Le logiciel garde cette trace m\u00eame apr\u00e8s un reset ou une relecture.','Ouvrir le test')){await api('/api/training/control',{action:'test'});success08('Test ouvert, en pause. D\u00e9marre ou observe un exemple.');}
});
$('deploy').onclick=()=>action(async()=>{
  if(await check08('Utiliser ce cerveau sur le march\u00e9 ?',[
    ['Copi\u00e9s','Les poids appris ET leur calibration vers le cerveau live. L\u2019atelier conserve sa copie.'],
    ['R\u00e9initialis\u00e9','Le portefeuille fictif revient \u00e0 20 \u20ac ; l\u2019ancienne session du march\u00e9 est archiv\u00e9e.'],
    ['Ensuite','Les poids du march\u00e9 restent gel\u00e9s. La politique n\u2019est pas d\u00e9marr\u00e9e automatiquement.']],
    'Un transfert r\u00e9ussi n\u2019est ni un certificat de performance ni une garantie de gain.','Copier vers le march\u00e9')){await api('/api/training/deploy',{confirm:true});success08('Cerveau et calibration copi\u00e9s. Portefeuille fictif 20 \u20ac, politique en pause.');}
});
$('policy-on').onclick=()=>action(async()=>{if(await check08('Autoriser des mises fictives ?',[
  ['Source',S?.market?.source||'Flux courant'],['Capital',euro(S?.stats?.capital)],['R\u00e8gles','Les r\u00e8gles \u00e9conomiques affich\u00e9es d\u00e9terminent les mises. Les poids ne changent pas.']],
  'Simulation locale uniquement : cotations hypoth\u00e9tiques, aucun ordre Euphoria.','Activer en simulation'))await api('/api/controls',{policy:true});});
$('import-live').onclick=()=>action(async()=>{const d=await api('/api/training/collect',{});success08(`${d.added||0} exercices ajout\u00e9s au corpus. La s\u00e9ance et les poids sont inchang\u00e9s.`);});
$('import').onclick=()=>action(async()=>{const file=$('file').files[0];if(!file)throw Error('Choisis un export d\u2019observations .jsonl, pas un fichier de poids .json.');const r=await fetch('/api/training/import',{method:'POST',headers:{'Content-Type':'application/x-ndjson'},body:file});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));success08(`${d.added||0} exercices ajout\u00e9s. Aucun cerveau n\u2019a \u00e9t\u00e9 r\u00e9initialis\u00e9.`);});
async function refreshArchives08(){try{const rows=await api('/api/training/archives'),area=$('archive-list');area.replaceChildren();if(!rows.length){area.append(node08('p','Pas encore de s\u00e9ance remplac\u00e9e.'));return;}for(const a of rows){const line=node08('div','','archive-item'),text=node08('div',`${a.session||'Cerveau initial'} \u00b7 ${f(a.updates,0)} corrections`);text.append(node08('small',`${modes08[a.mode]||'Ancienne version'} \u00b7 ${a.position} / ${a.total} \u00b7 graine ${a.config.seed}`));const link=node08('a','T\u00e9l\u00e9charger l\u2019archive','button subtle');link.href='/api/training/archives/'+encodeURIComponent(a.id);line.append(text,link);area.append(line);}}catch(e){$('archive-list').textContent=e.message;}}
$('refresh-archives').onclick=refreshArchives08;$('archive-card').addEventListener('toggle',()=>{if($('archive-card').open)refreshArchives08();});
const tour08=[
 ['collect-card','Enregistrer le monde','La collecte remplit un cahier de donn\u00e9es. Elle n\u2019entra\u00eene aucun cerveau. Tu peux l\u2019arr\u00eater sans perdre les observations.'],
 ['corpus-card','Choisir les exercices','Copier la collecte ou importer un JSONL ajoute les exercices au corpus. Un fichier de poids .json n\u2019est pas un corpus.'],
 ['protocol-card','Pr\u00e9parer, puis apprendre','V\u00e9rifier les donn\u00e9es ne change rien. Pr\u00e9parer cr\u00e9e un cerveau neuf. D\u00e9marrer lance ensuite sa s\u00e9ance.'],
 ['lifecycle-card','Trois fa\u00e7ons de r\u00e9entra\u00eener','Reprendre conserve tout. Rejouer garde le plan mais remet les poids \u00e0 z\u00e9ro. Continuer garde les poids et ajoute un passage sur le TRAIN sauvegard\u00e9.'],
 ['run-card','V\u00e9rifier et transf\u00e9rer','La calibration n\u2019est pas un nouvel apprentissage des poids. Le test v\u00e9rifie. Utiliser ce cerveau copie explicitement le r\u00e9sultat vers le march\u00e9.'],
 ['reset-card','Choisir ce qui repart de z\u00e9ro','Atelier, cerveau live et portefeuille ont des remises \u00e0 z\u00e9ro distinctes. Chaque confirmation indique ce qui reste. Aucun de ces boutons ne supprime le dataset.']
];
function closeTour08(){document.querySelectorAll('.tour-highlight').forEach(n=>n.classList.remove('tour-highlight'));$('tour-box').hidden=true;tourStep08=-1;}
function showTour08(step){document.querySelectorAll('.tour-highlight').forEach(n=>n.classList.remove('tour-highlight'));tourStep08=step;const [id,title,text]=tour08[step];jump08(id);$(id).classList.add('tour-highlight');$('tour-box').hidden=false;$('tour-index').textContent=`VISITE ${step+1} / ${tour08.length}`;$('tour-title').textContent=title;$('tour-text').textContent=text;$('tour-prev').disabled=step===0;$('tour-next').textContent=step===tour08.length-1?'Terminer la visite':'Suivant';}
$('tour-start').onclick=$('guide-tour').onclick=()=>showTour08(0);$('tour-prev').onclick=()=>showTour08(Math.max(0,tourStep08-1));$('tour-next').onclick=()=>tourStep08===tour08.length-1?closeTour08():showTour08(tourStep08+1);$('tour-close').onclick=closeTour08;document.addEventListener('keydown',e=>{if(e.key==='Escape')closeTour08();});
selectTab(location.pathname==='/guide'?'guide':location.pathname==='/training'?'train':'live',false);

if(location.pathname==='/guide'&&location.hash){
  try{const target=document.getElementById(decodeURIComponent(location.hash.slice(1)));if(target?.closest('#guide-panel'))requestAnimationFrame(()=>target.scrollIntoView({block:'start'}));}catch(_e){}
}
