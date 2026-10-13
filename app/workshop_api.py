"""Creative workshop APIs; a runtime data catalog never modifies app source."""
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Request, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .main import current_user, runner_auth, learning_call, runner_is_online
from .db import get_session_user
from .config import settings
from .services import theme_workshop as workshop, ui_preferences

router=APIRouter()

def asset_path(asset):
    from pathlib import Path
    path=Path(asset['local_path']).resolve()
    if (settings.data_dir/'theme-workshop').resolve() not in path.parents or not path.is_file():
        raise HTTPException(404,'素材文件不存在')
    return path

class CreateDesign(BaseModel,extra='forbid'):
    name:str=Field(min_length=1,max_length=60)
    base_theme_id:str=Field(max_length=80)
    idempotency_key:str=Field(min_length=8,max_length=100)

class DesignRound(BaseModel,extra='forbid'):
    stage:Literal['design','production']
    description:str=Field(min_length=1,max_length=8000)
    reference_ids:list[str]=Field(default_factory=list,max_length=5)
    idempotency_key:str=Field(min_length=8,max_length=100)

class DesignAction(BaseModel,extra='forbid'):
    action:Literal['approve_design','publish','share','recommend']
    revision_id:str=Field(max_length=80)
    idempotency_key:str=Field(min_length=8,max_length=100)

class WorkshopClaim(BaseModel,extra='forbid'):
    runner_id:str=Field(max_length=80)

class WorkshopResult(WorkshopClaim):
    claim_token:str=Field(max_length=80)
    status:Literal['running','completed','failed']='running'
    answer:str=Field(default='',max_length=30000)
    progress:str=Field(default='',max_length=1000)
    thread_id:str=Field(default='',max_length=100)
    error_message:str=Field(default='',max_length=3000)
    result:dict|None=None

class WorkshopNotify(WorkshopClaim):
    claim_token:str=Field(max_length=80)

class PreviewEvent(BaseModel,extra='forbid'):
    theme_id:str=Field(max_length=80)

class MailPreference(BaseModel,extra='forbid'):
    enabled:bool

class RetryJob(BaseModel,extra='forbid'):
    idempotency_key:str=Field(min_length=8,max_length=100)

@router.get('/api/ui/themes')
def theme_catalog(request:Request):
    user=get_session_user(request.cookies.get('autodev_session'))
    builtins=[{**t,'usage':workshop.usage_summary(t['id'])} for t in ui_preferences.catalog()['themes']]
    custom=workshop.themes_for_user(user['id'] if user else None,include_trials=bool(user))
    return {'themes':builtins+custom,'revision':max((t.get('revision_id','') for t in custom),default='')}

@router.get('/api/workshop')
def designs(user:Annotated[dict,Depends(current_user)]):
    return {'items':workshop.list_designs(user),'runner_online':runner_is_online(settings.runner_id),
            'recommendations_enabled':workshop.mail_preference(user['id'])}

@router.post('/api/workshop')
def create(payload:CreateDesign,user:Annotated[dict,Depends(current_user)]):
    return {'design':learning_call(workshop.create,payload.name,payload.base_theme_id,user,payload.idempotency_key)}

@router.get('/api/workshop/designs/{identifier}')
def design(identifier:str,user:Annotated[dict,Depends(current_user)]):
    from . import db
    with db.transaction() as conn:workshop._expire(conn)
    return {'design':learning_call(workshop.detail,identifier,user),'runner_online':runner_is_online(settings.runner_id)}

@router.get('/api/workshop/designs/{identifier}/conversation')
def conversation(identifier:str,user:Annotated[dict,Depends(current_user)],offset:Annotated[int,Query(ge=0)]=0):
    return {'items':learning_call(workshop.conversation,identifier,user,offset)}

@router.post('/api/workshop/designs/{identifier}/references')
async def reference(identifier:str,user:Annotated[dict,Depends(current_user)],file:Annotated[UploadFile,File()]):
    raw=await file.read(12*1024*1024+1)
    return {'asset':learning_call(workshop.add_asset,identifier,user,raw,file.filename or '参考图片')}

@router.post('/api/workshop/designs/{identifier}/rounds')
def design_round(identifier:str,payload:DesignRound,user:Annotated[dict,Depends(current_user)]):
    return {'design':learning_call(workshop.submit_round,identifier,user,payload.stage,payload.description,payload.reference_ids,payload.idempotency_key)}

@router.post('/api/workshop/designs/{identifier}/actions')
def design_action(identifier:str,payload:DesignAction,user:Annotated[dict,Depends(current_user)]):
    return {'design':learning_call(workshop.action,identifier,user,payload.action,payload.revision_id,payload.idempotency_key)}

@router.get('/api/workshop/assets/{asset_id}')
def media(asset_id:str,request:Request):
    user=get_session_user(request.cookies.get('autodev_session'))
    asset=learning_call(workshop.accessible_asset,asset_id,user)
    return FileResponse(asset_path(asset),media_type=asset['mime'],headers={
        'Cache-Control':'private, max-age=3600','X-Content-Type-Options':'nosniff'})

@router.post('/api/workshop/jobs/{job_id}/retry')
def retry_job(job_id:str,payload:RetryJob,user:Annotated[dict,Depends(current_user)]):
    return {'design':learning_call(workshop.retry_job,job_id,user,payload.idempotency_key)}

@router.get('/api/workshop/usage')
def usage(user:Annotated[dict,Depends(current_user)]):
    themes=ui_preferences.catalog()['themes']+workshop.themes_for_user(user['id'])
    rankings=[{'id':t['id'],'name':t['name'],'preview':t['preview'],'author':t.get('author','AutoDev'),**workshop.usage_summary(t['id'])} for t in themes]
    return {'rankings':sorted(rankings,key=lambda t:(-t['users'],-t['current'],-t['activations'],t['name'])),
            'history':workshop.usage_history(user['id'])}

@router.post('/api/workshop/usage/preview')
def preview(payload:PreviewEvent,user:Annotated[dict,Depends(current_user)]):
    themes=ui_preferences.catalog()['themes']+workshop.themes_for_user(user['id'],include_trials=True)
    if payload.theme_id not in {t['id'] for t in themes}:raise HTTPException(403,'壁纸未对当前账号开放')
    workshop.record_usage(user['id'],payload.theme_id,'preview');return {'ok':True}

@router.put('/api/workshop/mail-preference')
def preference(payload:MailPreference,user:Annotated[dict,Depends(current_user)]):
    return {'enabled':workshop.mail_preference(user['id'],payload.enabled)}

@router.post('/api/runner/workshop/claim',dependencies=[Depends(runner_auth)])
def claim(payload:WorkshopClaim):
    job=workshop.claim(payload.runner_id)
    if job and job['kind'] in {'notification','recommendation'}:
        # Mail belongs to the control plane, where SMTP is configured. Older
        # runners also benefit without interrupting their in-flight code/image jobs.
        from .services.workshop_mail import send_claimed,mail_error_detail
        try:
            result=send_claimed(job['id'],payload.runner_id,job['claim_token'])
            if result.get('pending'):raise RuntimeError('云端已有邮件投递正在处理，回执保留，未重复发送。')
            answer='邮件已通过平台默认邮箱发送。' if result.get('sent') else '收件人关闭推荐或没有可用邮箱，已跳过。'
            workshop.update(job['id'],payload.runner_id,job['claim_token'],status='completed',answer=answer,result=result)
        except Exception as exc:
            detail=mail_error_detail(exc)
            workshop.update(job['id'],payload.runner_id,job['claim_token'],status='failed',error_message=detail,
                result={'failure':{'category':'mail','title':'邮件发送未完成，壁纸与设计记录不受影响。','details':detail}})
        return {'job':None}
    return {'job':job}

@router.patch('/api/runner/workshop/jobs/{job_id}',dependencies=[Depends(runner_auth)])
def update(job_id:str,payload:WorkshopResult):return {'job':learning_call(workshop.update,job_id,**payload.model_dump())}

@router.post('/api/runner/workshop/jobs/{job_id}/notify',dependencies=[Depends(runner_auth)])
def notify(job_id:str,payload:WorkshopNotify):
    from .services.workshop_mail import send_claimed
    return {'result':learning_call(send_claimed,job_id,payload.runner_id,payload.claim_token)}

@router.get('/api/runner/workshop/jobs/{job_id}/assets/{asset_id}',dependencies=[Depends(runner_auth)])
def runner_asset(job_id:str,asset_id:str,runner_id:str,claim_token:str):
    from . import db
    with db.transaction() as conn:job=learning_call(workshop._claimed,conn,job_id,runner_id,claim_token)
    asset=db.row('SELECT * FROM theme_assets WHERE id=? AND design_id=?',(asset_id,job['design_id']))
    if not asset:raise HTTPException(404,'素材不存在')
    return FileResponse(asset_path(asset),media_type=asset['mime'])

@router.post('/api/runner/workshop/jobs/{job_id}/assets',dependencies=[Depends(runner_auth)])
async def generated_asset(job_id:str,file:Annotated[UploadFile,File()],runner_id:Annotated[str,Form()],
                          claim_token:Annotated[str,Form()],kind:Annotated[str,Form()]):
    from . import db
    job=db.row('SELECT * FROM theme_jobs WHERE id=?',(job_id,))
    if not job:raise HTTPException(404,'工坊任务不存在')
    raw=await file.read(12*1024*1024+1)
    asset=learning_call(workshop.add_asset,job['design_id'],None,raw,file.filename or '生成图片',job_id=job_id,
                        runner_id=runner_id,claim_token=claim_token,kind=kind)
    return {'asset':asset}
