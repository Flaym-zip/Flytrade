'use strict';
(() => {
 const $ = id => document.getElementById(id);
 const chapters = [...document.querySelectorAll('.wiki-article')];
 const normalize = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
 const index = chapters.map(el => ({el, id:el.id, title:el.dataset.title, text:el.textContent.replace(/\s+/g,' ').trim()}));
 const toast = text => { $('wiki-toast').textContent=text; $('wiki-toast').hidden=false; setTimeout(()=>{$('wiki-toast').hidden=true;},2500); };
 function navigate() {
  let key='debut';try{key=decodeURIComponent(location.hash.slice(1))||key;}catch(_e){}
  let target=document.getElementById(key), article=target?.closest('.wiki-article');
  if(!article){article=$('debut');target=article;}
  chapters.forEach(c=>c.hidden=c!==article);
  document.querySelectorAll('[data-chapter]').forEach(a=>{const current=a.dataset.chapter===article.id;a.classList.toggle('active',current);if(current)a.setAttribute('aria-current','location');else a.removeAttribute('aria-current');});
  $('breadcrumb').textContent='Wiki / '+article.dataset.title;
  $('reading-time').textContent=Math.max(1,Math.ceil(article.textContent.trim().split(/\s+/).length/200))+' min de lecture';
  document.title='Flytrade | '+article.dataset.title;
  requestAnimationFrame(()=>{if(target!==article)target.scrollIntoView({block:'start'});else if(window.innerWidth>760)window.scrollTo({top:0});else document.querySelector('.wiki-reading').scrollIntoView({block:'start'});});
 }
 window.addEventListener('hashchange',navigate);
 // Same-anchor links still scroll; ordinary navigation keeps browser history.
 document.addEventListener('click',e=>{const a=e.target.closest('a[href^="#"]');if(a && a.getAttribute('href')===location.hash){e.preventDefault();navigate();}});
 $('wiki-search').addEventListener('input',()=>{
  const value=normalize($('wiki-search').value.trim()), terms=value.split(/\s+/).filter(Boolean), results=$('wiki-results');
  results.replaceChildren();results.hidden=!terms.length;document.querySelector('.wiki-toc').hidden=!!terms.length;
  if(!terms.length){$('search-count').textContent=chapters.length+' chapitres';return;}
  const matches=index.filter(row=>terms.every(t=>normalize(row.title+' '+row.text).includes(t)));
  $('search-count').textContent=matches.length+' chapitre'+(matches.length!==1?'s':'')+' correspondant'+(matches.length!==1?'s':'');
  matches.forEach(row=>{
   const a=document.createElement('a');a.href='#'+row.id;a.className='search-result';a.append(document.createTextNode(row.title));
   const lower=normalize(row.text), first=lower.indexOf(terms[0]), start=Math.max(0,first-40);
   const small=document.createElement('small');small.textContent=(start?'... ':'')+row.text.slice(start,start+160)+'...';a.append(small);
   a.addEventListener('click',()=>{$('wiki-search').value='';$('wiki-search').dispatchEvent(new Event('input'));});results.append(a);
  });
 });
 document.querySelectorAll('[data-copy]').forEach(button=>button.addEventListener('click',async()=>{
  const url=location.origin+location.pathname+'#'+button.dataset.copy;
  try{await navigator.clipboard.writeText(url);toast('Lien du chapitre copie.');}catch(_e){toast('Adresse du chapitre : '+url);}
 }));
 $('wiki-print').addEventListener('click',()=>window.print());
 const numeric=id=>{const el=$(id);if(el.value.trim()===''||!el.checkValidity())throw Error('Verifier les valeurs et les bornes des champs.');const n=Number(el.value);if(!Number.isFinite(n))throw Error('Valeur non finie.');return n;};
 const fr=(x,n=2)=>x.toLocaleString('fr-FR',{minimumFractionDigits:n,maximumFractionDigits:n});
 function ev(){try{const p=numeric('calc-p')/100,m=numeric('calc-m'),s=numeric('calc-s'),h=numeric('calc-h')/100,c=numeric('calc-c')/100,g=m*(1-h),gain=s*(g-1-c),loss=-s*(1+c),e=p*gain+(1-p)*loss,threshold=(1+c)/g;
  $('calc-ev').textContent='Succes : '+fr(gain)+' EUR | Echec : '+fr(loss)+' EUR\nEsperance supposee : '+fr(e)+' EUR par occasion\nSeuil de touche : '+fr(threshold*100)+' %'+(threshold>1?' (impossible a atteindre)':'');
 }catch(e){$('calc-ev').textContent=e.message;}}
 function kc(){try{const n=numeric('calc-n'),s=numeric('calc-sp');$('calc-kc').textContent=fr(n*s/100,1)+' KC actives environ par candidat / '+n+' disponibles';}catch(e){$('calc-kc').textContent=e.message;}}
 function wilson(){try{const wins=numeric('calc-win'),n=numeric('calc-total');if(!Number.isInteger(wins)||!Number.isInteger(n)||wins>n)throw Error('Utiliser des nombres entiers, avec succes <= essais.');const p=wins/n,z=1.96,d=1+z*z/n,center=(p+z*z/(2*n))/d,half=z*Math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d;
  $('calc-wilson').textContent='Taux observe : '+fr(100*p)+' %\nIntervalle descriptif a 95 % : '+fr((center-half)*100)+' a '+fr((center+half)*100)+' %';
 }catch(e){$('calc-wilson').textContent=e.message;}}
 ['calc-p','calc-m','calc-s','calc-h','calc-c'].forEach(id=>$(id).addEventListener('input',ev));['calc-n','calc-sp'].forEach(id=>$(id).addEventListener('input',kc));['calc-win','calc-total'].forEach(id=>$(id).addEventListener('input',wilson));
 ev();kc();wilson();navigate();
 const hashElement=document.querySelector('[data-code-hash]')||$('code-hash');
 fetch('/api/wiki/parameters').then(r=>{if(!r.ok)throw Error();return r.json();}).then(data=>{
  const badge=$('registry-status');if(!badge)return;
  const text=document.querySelector('.wiki-document')?.textContent||'';
  const matches=text.includes(data.code_sha256);
  badge.textContent=matches?'Registre et code concordants':'Le code a change : regenerer le Wiki';badge.classList.toggle('negative',!matches); if($('wiki-sync'))$('wiki-sync').textContent=badge.textContent;
 }).catch(()=>{const badge=$('registry-status');if(badge)badge.textContent='Registre serveur indisponible; contenu local conserve';});
})();
