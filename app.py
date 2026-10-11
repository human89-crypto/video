import os, uuid, json, base64, secrets, subprocess, shutil, threading, html
from pathlib import Path
from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from pydantic import BaseModel, Field

ROOT=Path(os.environ.get("WORKSPACE_DIR","/data/workspaces"))
ROOT.mkdir(parents=True,exist_ok=True)
app=FastAPI(docs_url=None,redoc_url=None)
jobs={}
lock=threading.Lock()
FORMATS={"16:9":(1280,720),"9:16":(720,1280),"1:1":(720,720)}

@app.middleware("http")
async def auth(req:Request,next_call):
    if req.url.path=="/health": return await next_call(req)
    if os.environ.get("PUBLIC_MODE","1")=="1":
        name=os.environ.get("APP_ACCESS_USER","")
        pw=os.environ.get("APP_ACCESS_PASSWORD","")
        if not name or len(pw)<16: return JSONResponse({"detail":"Missing administrator credentials"},status_code=503)
        try:
            tok=req.headers.get("authorization","")
            if not tok.lower().startswith("basic "): raise ValueError()
            u,p=base64.b64decode(tok.split(" ",1)[1],validate=True).decode().split(":",1)
            valid=secrets.compare_digest(u,name) and secrets.compare_digest(p,pw)
        except Exception: valid=False
        if not valid: return Response("需要登入",status_code=401,headers={"WWW-Authenticate":'Basic realm="Campus Cut Studio"'})
    resp=await next_call(req)
    resp.headers.update({"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY"})
    return resp

@app.get("/health")
def health(): return {"ok":True}

def run(cmd,cwd=None,timeout=900):
    p=subprocess.run(cmd,cwd=cwd,text=True,capture_output=True,timeout=timeout)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout)[-900:])
    return p.stdout

def getdir(i):
    if len(i)!=32 or any(c not in "0123456789abcdef" for c in i): raise HTTPException(404)
    d=ROOT/i
    if not d.exists(): raise HTTPException(404)
    return d

@app.get("/api/status")
def status():
    return {"ffmpeg":bool(shutil.which("ffmpeg")),"hyperframes":bool(shutil.which("hyperframes")),"maxUploadBytes":300000000}

@app.post("/api/upload")
async def upload(file:UploadFile=File(...)):
    suffix=Path(file.filename or "").suffix.lower()
    if suffix not in {".mp4",".mov",".mkv",".webm",".m4v"}: raise HTTPException(400,"不支援的檔案格式")
    ident=uuid.uuid4().hex; d=ROOT/ident; d.mkdir()
    path=d/("source"+suffix); size=0
    try:
        with path.open("wb") as out:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>300000000: raise ValueError("檔案超過300MB")
                out.write(chunk)
        data=json.loads(run(["ffprobe","-v","error","-show_format","-show_streams","-of","json",str(path)],timeout=60))
        v=next((x for x in data["streams"] if x["codec_type"]=="video"),None)
        dur=float(data["format"].get("duration",0))
        if not v or not (0<dur<=14400): raise ValueError("影片格式或長度不正確")
        meta={"id":ident,"duration":round(dur,2),"name":Path(file.filename or "").name,"filename":path.name,"width":v["width"],"height":v["height"]}
        (d/"meta.json").write_text(json.dumps(meta,ensure_ascii=False),encoding="utf-8")
        return meta
    except Exception as e:
        shutil.rmtree(d,ignore_errors=True)
        raise HTTPException(400,str(e))
    finally: await file.close()

@app.get("/api/source/{ident}")
def source(ident:str):
    d=getdir(ident); m=json.loads((d/"meta.json").read_text())
    return FileResponse(d/m["filename"])

class Clip(BaseModel):
    start:float=Field(ge=0)
    end:float=Field(gt=0)
class Render(BaseModel):
    clips:list[Clip]=Field(min_length=1,max_length=30)
    aspect:str="9:16"
    engine:str="ffmpeg"
    title:str=Field(default="",max_length=80)
    school:str=Field(default="高雄市立左營高中",max_length=60)
    srt:str=Field(default="",max_length=90000)

def worker(ident,req:Render):
    d=ROOT/ident
    try:
        jobs[ident]={"status":"processing","progress":5}
        m=json.loads((d/"meta.json").read_text())
        src=d/m["filename"]
        w,h=FORMATS[req.aspect]
        parts=[]
        for i,c in enumerate(req.clips):
            f=d/("part%02d.mp4"%i)
            dur=c.end-c.start
            vf=f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
            run(["ffmpeg","-hide_banner","-loglevel","error","-y","-ss",str(c.start),"-i",str(src),"-t",str(dur),"-vf",vf,"-r","25","-c:v","libx264","-preset","veryfast","-crf","24","-pix_fmt","yuv420p","-an",str(f)],timeout=500)
            parts.append(f)
            jobs[ident]["progress"]=int(10+65*(i+1)/len(req.clips))
        manifest=d/"concat.txt"
        manifest.write_text("".join("file '"+f.name+"'\n" for f in parts))
        montage=d/"montage.mp4"
        run(["ffmpeg","-hide_banner","-loglevel","error","-y","-f","concat","-safe","0","-i",str(manifest),"-c","copy",str(montage)],cwd=d)
        out=d/"finished.mp4"
        # FFmpeg mode renders a clean MP4; HyperFrames adds the branded HTML layout.
        if req.engine=="hyperframes":
            hdir=d/"hf"; hdir.mkdir()
            shutil.copyfile(montage,hdir/"montage.mp4")
            total=sum(c.end-c.start for c in req.clips)
            title=html.escape(req.title)
            school=html.escape(req.school)
            markup=f"""<!doctype html><html><head><meta charset="utf-8"><style>
            html,body{{margin:0;background:#0A1E3F}} #root{{width:{w}px;height:{h}px;position:relative;overflow:hidden;font-family:sans-serif;color:white}}
            video{{width:100%;height:100%;object-fit:contain}}.clip{{position:absolute}}
            .banner{{top:7%;left:5%;right:5%;padding:22px;background:#0A1E3Fdd;border-left:7px solid #E6C97A;font-size:36px}}
            .school{{display:block;font-size:20px;color:#E6C97A;margin-top:10px}}
            </style></head><body><div id="root" data-composition-id="main" data-no-timeline data-start="0" data-duration="{total:.3f}" data-width="{w}" data-height="{h}">
            <video src="montage.mp4" data-track-index="0" data-start="0" data-duration="{total:.3f}" data-media-start="0" data-has-audio="false"></video>
            <div class="clip banner" data-track-index="2" data-start="0" data-duration="{min(total,4):.3f}">{title}<span class="school">{school}</span></div>
            </div></body></html>"""
            (hdir/"index.html").write_text(markup)
            jobs[ident]["progress"]=85
            run(["hyperframes","lint"],cwd=hdir,timeout=90)
            run(["hyperframes","render","--output",str(out)],cwd=hdir,timeout=1200)
        else:
            shutil.copyfile(montage,out)
        jobs[ident]={"status":"completed","progress":100,"url":f"/api/output/{ident}"}
    except Exception as e:
        jobs[ident]={"status":"failed","error":str(e)[-1000:],"progress":0}

@app.post("/api/render/{ident}")
def render(ident:str, req:Render):
    d=getdir(ident)
    m=json.loads((d/"meta.json").read_text())
    if req.aspect not in FORMATS or req.engine not in {"ffmpeg","hyperframes"}: raise HTTPException(400,"格式或引擎不正確")
    if sum(c.end-c.start for c in req.clips)>600 or any(c.end<=c.start or c.end>m["duration"]+.02 for c in req.clips): raise HTTPException(400,"剪輯時間不正確或超過10分鐘")
    with lock:
        if any(j.get("status")=="processing" for j in jobs.values()): raise HTTPException(429,"正在處理其他影片")
        jobs[ident]={"status":"queued","progress":0}
    threading.Thread(target=worker,args=(ident,req),daemon=True).start()
    return {"id":ident,"status":"queued"}

@app.get("/api/jobs/{ident}")
def progress(ident:str):
    getdir(ident)
    return jobs.get(ident,{"status":"not_started"})

@app.get("/api/output/{ident}")
def output(ident:str):
    d=getdir(ident); f=d/"finished.mp4"
    if not f.exists(): raise HTTPException(404)
    return FileResponse(f,media_type="video/mp4",filename="campus-cut.mp4")

@app.delete("/api/project/{ident}")
def delete(ident:str):
    d=getdir(ident)
    if jobs.get(ident,{}).get("status") in ("queued","processing"): raise HTTPException(409,"影片仍在處理")
    shutil.rmtree(d); jobs.pop(ident,None)
    return {"deleted":True}

PAGE=r"""<!DOCTYPE html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Campus Cut Studio</title><style>
:root{font-family:system-ui,'Noto Sans TC',sans-serif;background:#08152d;color:#eff5ff}*{box-sizing:border-box}
body{margin:0}.wrap{max-width:1000px;margin:auto;padding:30px 24px}header{padding:28px 0;border-bottom:1px solid #294366}h1{font-size:32px;margin:8px 0}h2{font-size:20px}
small,.muted{color:#99adcc}.hero{padding:20px 0 12px}section{background:#102340;padding:24px;border:1px solid #294366;border-radius:18px;margin:24px 0}
label{display:block;color:#b3c7e7;margin:14px 0 6px}input,select,textarea,button{font:inherit;border-radius:9px;padding:12px}
input,select,textarea{width:100%;background:#071630;border:1px solid #39577c;color:white}input[type=file]{padding:18px}
button{background:#e6c97a;border:0;cursor:pointer;color:#071630;font-weight:700}button:disabled{opacity:.5}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
video{display:block;max-width:100%;max-height:360px;margin:20px auto;border-radius:10px}a{color:#f8d98a}
code{background:#071630;padding:3px 6px;border-radius:6px}@media(max-width:640px){.grid{grid-template-columns:1fr}}
</style><div class="wrap"><header><div style="color:#e6c97a;font-weight:800;letter-spacing:3px">CAMPUS CUT STUDIO</div><h1>校園影片智慧剪輯台</h1><div class="muted">FFmpeg 精確剪輯 · HyperFrames 校園標題合成</div></header>
<section><h2>01　上傳影片</h2><input type="file" id="file" accept="video/*"><p><button id="upload">開始上傳</button> <span id="fileinfo" class="muted"></span></p></section>
<section><h2>02　設定剪輯</h2><div class="grid"><div><label>開始時間（秒）</label><input id="start" type="number" min="0" step=".1" value="0"></div><div><label>結束時間（秒）</label><input id="end" type="number" min=".1" step=".1" value="10"></div>
<div><label>畫面比例</label><select id="aspect"><option>9:16</option><option>16:9</option><option>1:1</option></select></div><div><label>輸出引擎</label><select id="engine"><option value="ffmpeg">FFmpeg 快速剪輯</option><option value="hyperframes">HyperFrames 動態標題</option></select></div></div>
<label>影片標題（HyperFrames 模式）</label><input id="title" value="精彩校園時刻"><label>學校名稱</label><input id="school" value="高雄市立左營高中">
<p><button id="make">製作 MP4</button> <button id="remove" style="background:#8da3c6">刪除本次專案</button></p><div id="msg" class="muted">請先上傳影片。</div><div id="view"></div></section>
<footer class="muted">影片儲存於本站受密碼保護的雲端空間。使用前請確認學生影像及音訊的必要授權。</footer></div>
<script>
let id=null,duration=0,timer=null;
const el=x=>document.getElementById(x), msg=t=>el('msg').textContent=t;
el('upload').onclick=async()=>{
const f=el('file').files[0];if(!f)return alert('請選擇影片');
let data=new FormData();data.append('file',f);msg('正在上傳...');
try{let r=await fetch('/api/upload',{method:'POST',body:data}),a=await r.json();if(!r.ok)throw Error(a.detail);
id=a.id;duration=a.duration;el('end').value=Math.min(duration,30);el('fileinfo').textContent=a.name+' · '+duration+' 秒';msg('上傳成功，請設定剪輯範圍。');el('view').innerHTML='<video controls src="/api/source/'+id+'"></video>'}
catch(e){msg('上傳失敗：'+e.message)}
};
el('make').onclick=async()=>{
if(!id)return alert('請先上傳影片');
try{let body={clips:[{start:+el('start').value,end:+el('end').value}],aspect:el('aspect').value,engine:el('engine').value,title:el('title').value,school:el('school').value};
let r=await fetch('/api/render/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),a=await r.json();if(!r.ok)throw Error(a.detail);msg('已開始製作...');
clearInterval(timer);timer=setInterval(async()=>{try{const j=await(await fetch('/api/jobs/'+id)).json();msg(j.status+' · '+(j.progress||0)+'%'+(j.error?' · '+j.error:''));
if(j.status==='completed'){clearInterval(timer);el('view').innerHTML='<video controls src="'+j.url+'"></video><p><a download="campus-cut.mp4" href="'+j.url+'">下載 MP4</a></p>'}
if(j.status==='failed')clearInterval(timer)}catch(e){clearInterval(timer);msg(e.message)}},1600)
}catch(e){msg('無法開始：'+e.message)}
};
el('remove').onclick=async()=>{if(!id)return;if(!confirm('確定刪除本次上傳的影片及成品？'))return;
const r=await fetch('/api/project/'+id,{method:'DELETE'});if(!r.ok)return msg('刪除失敗');id=null;el('view').innerHTML='';el('fileinfo').textContent='';msg('已刪除專案。')};
</script></html>"""
@app.get("/")
def home(): return HTMLResponse(PAGE)
