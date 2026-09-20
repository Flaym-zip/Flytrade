/* Shared geometry: one 5-second column has the SAME pixel size as $0.50 row.
   Chart interpolation is visual only. Settlement remains tick-based server-side. */
'use strict';
window.FlyGrid = {
 draw(canvas, data, options={}) {
  const r=canvas.getBoundingClientRect(),w=Math.max(1,r.width),h=Math.max(1,r.height),dpr=Math.min(2,devicePixelRatio||1);
  if(canvas.width!==Math.round(w*dpr)||canvas.height!==Math.round(h*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);}
  const c=canvas.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,w,h);
  c.fillStyle='#0c141d';c.fillRect(0,0,w,h);
  const left=12,right=82,top=31,bottom=29,pw=w-left-right,ph=h-top-bottom;
  // Fit enough future columns even on narrow displays. No skewed cells.
  const cell=Math.max(18,Math.min(56,pw/12,ph/6));
  canvas.dataset.cellWidth=cell.toFixed(6);canvas.dataset.cellHeight=cell.toFixed(6);
  const now=Number(data?.now||0),price=data?.price??null;
  const center=options.center??price??0,cx=left+pw/2,cy=top+ph/2;
  const x=t=>cx+(t-now)*cell/5,y=p=>cy-(p-center)*cell/.5;
  const tmin=now-pw/2*5/cell,tmax=now+pw/2*5/cell;
  const pmin=center-ph/2*.5/cell,pmax=center+ph/2*.5/cell;
  const firstT=Math.floor(tmin/5)*5,firstP=Math.floor(pmin/.5)*.5;
  const fmt=(p)=>Number(p).toLocaleString('fr-FR',{minimumFractionDigits:2,maximumFractionDigits:2});
  const clock=t=>new Date(t*1000).toLocaleTimeString('fr-FR',{hour12:false}).slice(-5);
  c.save();c.beginPath();c.rect(left,top,pw,ph);c.clip();
  c.fillStyle='#111e3077';c.fillRect(cx,top,x(now+10)-cx,ph);
  c.lineWidth=1;c.strokeStyle='#273647';
  for(let t=firstT;t<=tmax;t+=5){c.beginPath();c.moveTo(x(t),top);c.lineTo(x(t),top+ph);c.stroke();}
  for(let p=firstP;p<=pmax;p+=.5){c.beginPath();c.moveTo(left,y(p));c.lineTo(left+pw,y(p));c.stroke();}
  const trade=data?.trade,contract=data?.preview;
  function cellDraw(contract,a,locked=false){
    const name=['Hausse','Stable','Baisse'][a],row=contract.base_row+[1,0,-1][a];
    const xx=x(contract.start),yy=y((row+1)*.5);
    const submitted=locked&&contract.order_status==='submitted';
    const quotes=locked?(contract.quote_lock||contract.quote_click):contract.quotes;
    const mult=quotes?.displayed?.[a];
    c.fillStyle=locked?'#25414dcc':'#142a38aa';c.fillRect(xx+1,yy+1,cell-2,cell-2);
    c.strokeStyle=locked?(submitted?'#e7bd75':'#bdeeff'):'#71b99e';
    c.lineWidth=locked?2.5:1;c.setLineDash(locked?[]:[3,3]);
    c.strokeRect(xx+1,yy+1,cell-2,cell-2);c.setLineDash([]);
    c.textAlign='center';c.fillStyle=locked?'#ffffff':'#c3d6df';c.font='10px system-ui';
    c.fillText(cell>32?name:name[0],xx+cell/2,yy+cell/2-8,cell-4);
    c.fillText(mult==null?'---':'\u00d7'+Number(mult).toFixed(2),xx+cell/2,yy+cell/2+6,cell-4);
    if(locked){c.font='11px system-ui';c.fillText(submitted?'...':'\u2713',xx+cell/2,yy+cell/2+20,cell-4);}
  }
  if(contract){[0,1,2].forEach(a=>cellDraw(contract,a));canvas.dataset.previewRow=String(contract.base_row);}
  if(trade&&trade.chosen!=null){cellDraw(trade,trade.chosen,true);canvas.dataset.lockedRow=String(trade.base_row);}
  else delete canvas.dataset.lockedRow;
  if(data?.old){
   const o=data.old;c.strokeStyle=o.status==='won'?'#63ddb6':o.status==='lost'?'#ed8da1':'#b9a37a';
   c.lineWidth=1.5;c.strokeRect(x(o.start)+1,y(o.upper)+1,cell-2,cell-2);
  }
  c.strokeStyle='#63ddb6';c.lineWidth=1.8;c.lineJoin='round';c.beginPath();let prior=null;
  for(const p of data?.points||[]){
   if(!prior||p[2]!==prior[2])c.moveTo(x(p[0]),y(p[1]));else c.lineTo(x(p[0]),y(p[1]));prior=p;
  }
  c.stroke();
  if(prior){c.fillStyle='#b1ffe3';c.beginPath();c.arc(x(prior[0]),y(prior[1]),3,0,Math.PI*2);c.fill();}
  c.setLineDash([4,5]);c.strokeStyle='#8497af';c.beginPath();c.moveTo(cx,top);c.lineTo(cx,top+ph);c.stroke();
  c.strokeStyle='#b9a37a';c.beginPath();c.moveTo(x(now+10),top);c.lineTo(x(now+10),top+ph);c.stroke();c.setLineDash([]);
  if(price!=null){c.strokeStyle='#63ddb644';c.beginPath();c.moveTo(left,y(price));c.lineTo(left+pw,y(price));c.stroke();}
  c.restore();c.font='10px ui-monospace,monospace';c.fillStyle='#9dafbf';
  c.textAlign='left';for(let p=firstP;p<=pmax;p+=.5){if(y(p)<top+8||y(p)>top+ph-5)continue;c.fillText(fmt(p),w-right+9,y(p)+4);}
  c.textAlign='center';for(let t=firstT;t<=tmax;t+=5){if(x(t)<left+14||x(t)>w-right-14)continue;if(cell<45&&t%10)continue;c.fillText(now?clock(t):String(t),x(t),h-9);}
  c.fillStyle='#d7e6f2';c.fillText(options.replay?'D\u00c9CISION':'PR\u00c9SENT',cx,18);
  c.fillStyle='#d7bb86';c.fillText('+10 s',x(now+10),18);
  if(price!=null){c.fillStyle='#15392e';c.fillRect(w-right+1,Math.max(top,Math.min(top+ph-24,y(price)-12)),right-3,24);c.fillStyle='#b1ffe3';c.textAlign='center';c.fillText(fmt(price),w-right/2,Math.max(top,Math.min(top+ph-24,y(price)-12))+16);}
  if(contract&&(y((contract.base_row+2)*.5)<top||y((contract.base_row-1)*.5)>top+ph)){
    c.fillStyle='#e2c793';c.textAlign='left';c.font='11px system-ui';c.fillText('Bornes de certaines cases hors champ : contrat conserv\u00e9.',left+6,top+16);
  }
 }
};
