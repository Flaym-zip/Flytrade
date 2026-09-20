from __future__ import annotations
import asyncio, json, logging, os, sqlite3, tempfile
from contextlib import asynccontextmanager,suppress
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse,StreamingResponse,Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,StrictBool,ConfigDict,Field
from typing import Literal
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .market import Market,stream
from .orderbook import OrderBook,stream_book
from .run08 import Run08 as Run06
from .economy06 import Rules06
from .academy08 import Academy08 as Academy06, Config06
from .feed07 import KrakenMarket, KrakenBook, stream_kraken, stream_kraken_book, poll_rest
from .wiki07 import wiki_parameters

STATIC=Path(__file__).parent/'static'

@asynccontextmanager
async def lifespan(app):
    root=Path(os.getenv('FLYTRADE_DATA',os.getenv('FLYLAB_DATA','./data')))
    provider=os.getenv('FLYTRADE_FEED','kraken').lower()
    if provider not in ('kraken','coinbase','off'): raise ValueError('FLYTRADE_FEED: kraken, coinbase ou off')
    # Never reuse a Coinbase wallet/model/calibration on Kraken silently.
    data=root/'sources'/'kraken' if provider=='kraken' else root
    data.mkdir(parents=True,exist_ok=True)
    app.state.provider=provider;app.state.data=data
    app.state.market=KrakenMarket() if provider=='kraken' else Market()
    app.state.book=KrakenBook() if provider=='kraken' else OrderBook()
    app.state.run=Run06(data,app.state.market,app.state.book)
    app.state.academy=Academy06(data,expected_source=(provider+'-ETH-USD') if provider!='off' else None)
    app.state.lock=asyncio.Lock();app.state.train_lock=asyncio.Lock()
    app.state.train_snapshot=app.state.academy.snapshot()
    async def clock():
        while True:
            await asyncio.sleep(.1)
            async with app.state.lock:
                try:app.state.run.tick()
                except Exception:logging.exception('Execution clock');app.state.run.policy_enabled=False
    async def train_clock():
        while True:
            await asyncio.sleep(.05)
            if app.state.academy.auto:
                async with app.state.train_lock:
                    try:
                        await asyncio.to_thread(app.state.academy.step_batch)
                        app.state.train_snapshot=app.state.academy.snapshot()
                    except Exception:
                        logging.exception('Training');app.state.academy.auto=False
                        app.state.train_snapshot=app.state.academy.snapshot()
    async def connected():
        async with app.state.lock:
            app.state.run.invalidate('Reconnexion '+provider)
            app.state.market.begin();app.state.book.reset_flow()
    async def receive(event):
        async with app.state.lock:
            m=app.state.market;n=m.total_ticks;reason=m.receive(event)
            if reason:app.state.run.invalidate(reason);app.state.book.reset_flow()
            if m.total_ticks>n:app.state.book.trade(event);app.state.run.observe(m.ticks[-1])
    async def disconnected(reason):
        async with app.state.lock:
            app.state.market.disconnect(reason);app.state.book.reset_flow();app.state.run.invalidate(reason)
    async def book_connected():
        async with app.state.lock:app.state.book.begin()
    async def book_message(message):
        async with app.state.lock:app.state.book.receive(message)
    async def book_error(reason):
        async with app.state.lock:app.state.book.disconnect(reason)
    tasks=[asyncio.create_task(clock()),asyncio.create_task(train_clock())]
    if provider=='kraken':
        tasks.extend([asyncio.create_task(stream_kraken(connected,receive,disconnected)),
                      asyncio.create_task(poll_rest(app.state.market.watch))])
        if os.getenv('FLYTRADE_BOOK','on')!='off':
            tasks.append(asyncio.create_task(stream_kraken_book(book_connected,book_message,book_error)))
    elif provider=='coinbase':
        tasks.append(asyncio.create_task(stream(connected,receive,disconnected)))
        if os.getenv('FLYTRADE_BOOK','on')!='off':
            tasks.append(asyncio.create_task(stream_book(book_connected,book_message,book_error)))
    else:
        app.state.market.status='Flux desactive (test hors ligne)'
        app.state.market.provider_off=True
    try:yield
    finally:
        app.state.academy.auto=False
        # Wait for worker to exit before closing sqlite connection.
        async with app.state.train_lock:pass
        for task in tasks:task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):await task
        app.state.run.close();app.state.academy.close()

app=FastAPI(title='Flytrade alpha08 - guide et atelier',lifespan=lifespan,docs_url=None,redoc_url=None)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','testserver'])

@app.middleware('http')
async def local(request:Request,call_next):
    if request.method in ('POST','PUT','DELETE'):
        origin=request.headers.get('origin')
        if origin and urlsplit(origin).netloc!=request.headers.get('host') or request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'detail':'Origine non autorisee'},status_code=403)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    return response

@app.exception_handler(ValueError)
async def bad_value(request,exc):return JSONResponse({'detail':str(exc)},status_code=409)

@app.get('/health')
async def health(request:Request):
    return {'ok':not request.app.state.run.fatal,'version':'0.8.0-alpha','connected':request.app.state.market.connected}
@app.get('/')
async def index():return FileResponse(STATIC/'index06.html')
@app.get('/training')
async def training_page():return FileResponse(STATIC/'index06.html')
@app.get('/guide')
async def guide_page():return FileResponse(STATIC/'index06.html')
@app.get('/wiki')
async def wiki_page():return FileResponse(STATIC/'wiki08.html')
@app.get('/api/wiki/parameters')
async def parameters():return wiki_parameters()
@app.get('/api/feed/diagnostics')
async def feed_diagnostics(request:Request):
    async with request.app.state.lock:
        m=request.app.state.market.snapshot()
        return {k:v for k,v in m.items() if k!='chart'}
@app.get('/api/state')
async def state(request:Request):
    async with request.app.state.lock:return request.app.state.run.snapshot()
@app.get('/api/training/state')
async def training_state(request:Request):return request.app.state.train_snapshot

class Controls(BaseModel):
    model_config=ConfigDict(extra='forbid')
    collect:StrictBool|None=None
    policy:StrictBool|None=None
class Confirm(BaseModel):
    confirm:StrictBool=False
    reset_brain:StrictBool=False
class TrainingControl(BaseModel):
    action:str
class DatasetVersionRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=120)
    dataset_ids:list[str]=Field(min_length=1)
    allow_exposed_training:StrictBool=False

@app.post('/api/controls')
async def controls(p:Controls,request:Request):
    async with request.app.state.lock:
        request.app.state.run.controls(p.collect,p.policy);return request.app.state.run.snapshot()
@app.post('/api/settings')
async def settings(p:Rules06,request:Request):
    async with request.app.state.lock:request.app.state.run.configure(p);return p.model_dump()
@app.post('/api/reset')
async def reset(p:Confirm,request:Request):
    if not p.confirm:raise ValueError('Confirmation requise')
    async with request.app.state.lock:request.app.state.run.reset_wallet(p.reset_brain);return request.app.state.run.snapshot()

@app.post('/api/training/import')
async def upload(request:Request):
    # Spool outside model memory; parse in dedicated thread. No fixed cumulative corpus cap.
    fd,name=tempfile.mkstemp(prefix='flytrade-upload-',suffix='.jsonl')
    size=0
    try:
        with os.fdopen(fd,'wb') as f:
            async for part in request.stream():
                size+=len(part)
                if size>100*1024*1024:raise HTTPException(413,'100 Mio maximum par fichier. Importer en plusieurs fichiers')
                f.write(part)
        async with request.app.state.train_lock:
            def load():
                with open(name,'rb') as f:return request.app.state.academy.import_lines(f,name=request.query_params.get('name'))
            result=await asyncio.to_thread(load)
            request.app.state.train_snapshot=request.app.state.academy.snapshot()
            return result
    finally:Path(name).unlink(missing_ok=True)
@app.post('/api/training/collect')
async def collect_import(request:Request):
    async with request.app.state.train_lock:
        result=await asyncio.to_thread(request.app.state.academy.import_live,request.app.state.data/'flytrade06.sqlite3',request.query_params.get('name'))
        request.app.state.train_snapshot=request.app.state.academy.snapshot();return result
@app.get('/api/datasets')
async def datasets(request:Request):
    async with request.app.state.train_lock:return request.app.state.academy.datasets()
@app.post('/api/datasets/version')
async def dataset_version(p:DatasetVersionRequest,request:Request):
    async with request.app.state.train_lock:
        return await asyncio.to_thread(request.app.state.academy.create_dataset_version,p.dataset_ids,p.name,p.allow_exposed_training)
@app.post('/api/training/create')
async def create(p:Config06,request:Request):
    async with request.app.state.train_lock:
        result=await asyncio.to_thread(request.app.state.academy.create,p)
        request.app.state.train_snapshot=result;return result
@app.post('/api/training/control')
async def train_control(p:TrainingControl,request:Request):
    async with request.app.state.train_lock:
        a=request.app.state.academy
        if p.action=='start':
            if not a.session or a.position>=len(a.plan):raise ValueError('Creer un protocole ou ouvrir le test')
            a.auto=True
        elif p.action=='pause':a.auto=False
        elif p.action=='step':
            a.auto=False;await asyncio.to_thread(a.step_batch,1)
        elif p.action=='test':a.open_test()
        else:raise ValueError('Commande inconnue')
        request.app.state.train_snapshot=a.snapshot();return request.app.state.train_snapshot
@app.post('/api/training/deploy')
async def deploy(p:Confirm,request:Request):
    if not p.confirm:raise ValueError('Confirmation requise')
    async with request.app.state.train_lock:
        b,c,m=request.app.state.academy.deployable()
        async with request.app.state.lock:request.app.state.run.deploy(b,c,m)
    return {'ok':True,'message':'Copie gelee deployee ; nouveau portefeuille de 20 EUR, politique en pause'}

class LifecycleRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirm:StrictBool=False
    action:Literal['replay','continue','reset_training','reset_live']
    config:Config06|None=None
    passes:int=Field(default=1,ge=1,le=20)

@app.post('/api/training/preview')
async def preview(p:Config06,request:Request):
    async with request.app.state.train_lock:
        return await asyncio.to_thread(request.app.state.academy.preview,p)

@app.post('/api/lifecycle')
async def lifecycle(p:LifecycleRequest,request:Request):
    if not p.confirm:raise ValueError('Confirmation explicite requise. Aucune modification.')
    async with request.app.state.train_lock:
        a=request.app.state.academy
        if p.action=='replay':result=await asyncio.to_thread(a.replay)
        elif p.action=='continue':result=await asyncio.to_thread(a.continue_training,p.passes)
        elif p.action=='reset_training':result=await asyncio.to_thread(a.reset_training,p.config or Config06())
        else:
            async with request.app.state.lock:
                request.app.state.run.reset_brain_only(p.config or Config06())
                result=request.app.state.run.snapshot()
        request.app.state.train_snapshot=a.snapshot()
        return result

@app.get('/api/training/archives')
async def archives(request:Request):
    async with request.app.state.train_lock:return request.app.state.academy.archive_list()

@app.get('/api/training/archives/{key}')
async def archive_download(key:str,request:Request):
    async with request.app.state.train_lock:
        d=request.app.state.academy.archive(key)
    return download_json(d,'flytrade-archive-atelier.json')

def download_json(d,name):
    return Response(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False),media_type='application/json',
                    headers={'Content-Disposition':f'attachment; filename="{name}"'})
@app.get('/api/training/report')
async def report(request:Request):
    async with request.app.state.train_lock:
        header=request.app.state.academy.report_header()
    def stream_report():
        db=sqlite3.connect(f'file:{(request.app.state.data/"flytrade-training06.sqlite3").absolute()}?mode=ro',uri=True)
        try:
            yield json.dumps(header,ensure_ascii=False,allow_nan=False)[:-1]+',"records":['
            first=True
            for row, in db.execute('SELECT payload FROM records WHERE session=? AND step<=? ORDER BY step',(header['session'],header['position'])):
                if not first:yield ','
                first=False;yield row
            yield ']}'
        finally:db.close()
    return StreamingResponse(stream_report(),media_type='application/json',headers={'Content-Disposition':'attachment; filename="flytrade-alpha08-rapport.json"'})
@app.get('/api/training/weights')
async def weights(request:Request):
    async with request.app.state.train_lock:
        a=request.app.state.academy
        return download_json({'version':'0.8.0-alpha','brain':a.brain.to_dict(),'calibrator':a.calibrator.to_dict()},'flytrade-alpha08-poids.json')
@app.get('/api/checkpoint')
async def checkpoint(request:Request):
    async with request.app.state.lock:return download_json(request.app.state.run.checkpoint(),'flytrade-alpha08-live.json')

def rows_stream(dbfile,kind):
    db=sqlite3.connect(f'file:{dbfile.absolute()}?mode=ro',uri=True)
    try:
        if kind=='windows':
            for r in db.execute('SELECT payload FROM opportunities ORDER BY id'):yield r[0]+'\n'
        elif kind=='samples':
            for t,p in db.execute('SELECT t,payload FROM samples ORDER BY id'):yield json.dumps({'type':'snapshot','t':t,**json.loads(p)})+'\n'
        else:
            for tid,t,p,epoch,received in db.execute('SELECT * FROM ticks ORDER BY trade_id'):
                yield json.dumps(dict(type='tick',trade_id=tid,t=t,price=p,epoch=epoch,received=received))+'\n'
    finally:db.close()
@app.get('/api/export/{kind}')
async def export(kind:str,request:Request):
    if kind not in ('windows','samples','ticks'):raise HTTPException(404)
    return StreamingResponse(rows_stream(request.app.state.data/'flytrade06.sqlite3',kind),media_type='application/x-ndjson',
        headers={'Content-Disposition':f'attachment; filename="flytrade-alpha08-{kind}.jsonl"'})
app.mount('/static',StaticFiles(directory=STATIC),name='static')
