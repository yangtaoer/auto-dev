"""Versioned, account-owned creative work, approvals, live catalog and usage audit."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import db
from app.config import settings
from . import model_settings
from .workshop_theme import COMPONENT_COLORS,validate_components,component_tokens

COLOR_KEYS = {'skin-bg','skin-surface','skin-surface-alt','skin-sidebar','skin-ink','skin-muted','skin-line',
              'skin-accent','skin-accent-hover','skin-success','skin-success-soft','skin-warning','skin-warning-soft',
              'skin-danger','skin-danger-soft','skin-info','skin-info-soft','skin-hover','skin-meta'} | COMPONENT_COLORS
SCENE_KEYS = {'leaf','petal','ground','ambient','key'}
PHASE_STATUS = {'design':'designing','production':'producing','publish':'publishing','share':'sharing'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _audit(conn, actor_id, action, identifier, detail=None):
    conn.execute("INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES (?,?,'theme_design',?,?,?)",
                 (actor_id, 'theme.'+action, identifier, _json(detail or {}), db.utc_now()))


def owned(identifier: str, actor: dict, *, mutate: bool = False) -> dict:
    item = db.row('SELECT * FROM theme_designs WHERE id=?', (identifier,))
    if not item:
        raise LookupError('作品不存在')
    if item['owner_id'] != actor['id'] and (mutate or actor['role'] != 'admin'):
        raise PermissionError('只有制作者可以修改或确认这个作品')
    return item


def asset_url(item: dict) -> str:
    expires = item.get('url_expires_at')
    if item.get('external_url') and (not expires or datetime.fromisoformat(expires) > datetime.now(UTC)+timedelta(minutes=5)):
        return item['external_url']
    return '/api/workshop/assets/'+item['id']


def _public_asset(item: dict) -> dict:
    return {k:item[k] for k in ['id','kind','name','mime','size_bytes','width','height','created_at']} | {'url':asset_url(item)}


def _public_job(item: dict) -> dict:
    result = {k:item[k] for k in ['id','kind','question','answer','status','progress','error_message','created_at',
                                'started_at','completed_at','thread_id']}
    result['elapsed_seconds'] = max(0,(datetime.fromisoformat(item['completed_at'] or db.utc_now())-
                                      datetime.fromisoformat(item['started_at'])).total_seconds()) if item['started_at'] else 0
    result['output'] = db.json_value(item['output_json'], {})
    return result


def detail(identifier: str, actor: dict) -> dict:
    item = owned(identifier,actor)
    item['can_edit'] = item['owner_id'] == actor['id']
    item['author'] = db.row('SELECT display_name FROM users WHERE id=?',(item['owner_id'],))['display_name']
    item['job_count']=db.row('SELECT count(*) n FROM theme_jobs WHERE design_id=?',(identifier,))['n']
    item['jobs'] = list(reversed([_public_job(j) for j in db.rows('SELECT * FROM theme_jobs WHERE design_id=? ORDER BY created_at DESC,id DESC LIMIT 80',(identifier,))]))
    item['assets'] = [_public_asset(a) for a in db.rows('SELECT * FROM theme_assets WHERE design_id=? ORDER BY created_at,id',(identifier,))]
    item['revisions'] = db.rows('SELECT id,number,stage,preview_asset_id,background_asset_id,created_at FROM theme_revisions WHERE design_id=? ORDER BY number',(identifier,))
    item['usage'] = usage_summary('workshop-'+identifier)
    item.pop('create_key',None)
    return item


def conversation(identifier: str,actor:dict,offset:int=0) -> list[dict]:
    owned(identifier,actor)
    return [_public_job(j) for j in db.rows('SELECT * FROM theme_jobs WHERE design_id=? ORDER BY created_at DESC,id DESC LIMIT 40 OFFSET ?',(identifier,offset))]


def list_designs(actor: dict) -> list[dict]:
    with db.transaction() as conn:_expire(conn)
    clause,params = ('',()) if actor['role']=='admin' else ('WHERE owner_id=?',(actor['id'],))
    # List only small summaries. Complete conversations are loaded on demand,
    # not multiplied by every design whenever the studio is opened.
    result = db.rows(f'''SELECT d.id,d.owner_id,d.name,d.status,d.is_public,d.updated_at,
        d.approved_design_id,u.display_name author FROM theme_designs d JOIN users u ON u.id=d.owner_id
        {clause} ORDER BY d.updated_at DESC LIMIT 300''',params)
    return result


def create(name: str, base_theme_id: str, actor: dict, key: str) -> dict:
    from .ui_preferences import catalog
    if base_theme_id not in {t['id'] for t in catalog()['themes']}:
        raise ValueError('请选择一个系统自带主题作为基础')
    name=name.strip()
    if not name or len(name)>60:
        raise ValueError('作品名称须为 1–60 个字')
    with db.transaction() as conn:
        existing=conn.execute('SELECT * FROM theme_designs WHERE owner_id=? AND create_key=?',(actor['id'],key)).fetchone()
        if existing:
            if existing['name']!=name or existing['base_theme_id']!=base_theme_id:
                raise RuntimeError('重复提交标识与已有作品不一致')
            identifier=existing['id']
        else:
            identifier,now=str(uuid.uuid4()),db.utc_now()
            conn.execute('INSERT INTO theme_designs(id,owner_id,name,base_theme_id,create_key,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                         (identifier,actor['id'],name,base_theme_id,key,now,now))
            _audit(conn,actor['id'],'created',identifier)
    return detail(identifier,actor)


def _expire(conn):
    cutoff=(datetime.now(UTC)-timedelta(minutes=5)).isoformat()
    for job in conn.execute("SELECT * FROM theme_jobs WHERE status='running' AND updated_at<?",(cutoff,)).fetchall():
        conn.execute("UPDATE theme_jobs SET status='failed',error_message=?,completed_at=?,updated_at=? WHERE id=?",
                     ('执行器连接中断，记录和上一轮作品保留，可重新提交。',db.utc_now(),db.utc_now(),job['id']))
        if job['kind'] in PHASE_STATUS:
            conn.execute('UPDATE theme_designs SET status=?,updated_at=? WHERE id=?',(job['kind']+'_failed',db.utc_now(),job['design_id']))


def _queue(conn, design: dict, kind: str, question: str, actor_id: int, key: str, payload: dict) -> dict:
    fingerprint=hashlib.sha256(_json({'kind':kind,'question':question,'payload':payload}).encode()).hexdigest()
    prior=conn.execute('SELECT * FROM theme_jobs WHERE design_id=? AND idempotency_key=?',(design['id'],key)).fetchone()
    if prior:
        if prior['payload_hash']!=fingerprint:
            raise RuntimeError('重复提交标识与原内容不一致')
        return dict(prior)
    if kind in PHASE_STATUS and conn.execute("SELECT 1 FROM theme_jobs WHERE design_id=? AND status IN ('queued','running') AND kind IN ('design','production','publish','share')",(design['id'],)).fetchone():
        raise RuntimeError('这一轮正在处理，完成后可以继续沟通；输入内容会保留')
    identifier,now=str(uuid.uuid4()),db.utc_now()
    conn.execute('''INSERT INTO theme_jobs(id,design_id,actor_id,kind,question,input_json,runner_id,model_config,
        idempotency_key,payload_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
        (identifier,design['id'],actor_id,kind,question,_json(payload),settings.runner_id,_json(model_settings.current()),key,fingerprint,now,now))
    if kind in PHASE_STATUS:
        conn.execute('UPDATE theme_designs SET status=?,updated_at=? WHERE id=?',(kind+'_queued',now,design['id']))
    _audit(conn,actor_id,'queued',design['id'],{'job_id':identifier,'kind':kind})
    return dict(conn.execute('SELECT * FROM theme_jobs WHERE id=?',(identifier,)).fetchone())


def submit_round(identifier: str, actor: dict, stage: str, description: str, reference_ids: list[str], key: str) -> dict:
    design=owned(identifier,actor,mutate=True)
    if stage not in {'design','production'} or not description.strip():
        raise ValueError('请输入设计或制作的修改意见')
    if stage=='production' and not design['approved_design_id']:
        raise ValueError('先确认设计图，才能进入制作')
    with db.transaction() as conn:
        _expire(conn)
        for asset_id in reference_ids:
            if not conn.execute("SELECT 1 FROM theme_assets WHERE id=? AND design_id=?",(asset_id,identifier)).fetchone():
                raise ValueError('参考图不属于当前作品')
        _queue(conn,design,stage,description.strip(),actor['id'],key,{'reference_ids':reference_ids,
               'approved_design_id':design['approved_design_id'] if stage=='production' else None})
    return detail(identifier,actor)


def action(identifier: str, actor: dict, action: str, revision_id: str, key: str) -> dict:
    design=owned(identifier,actor,mutate=True)
    with db.transaction() as conn:
        _expire(conn)
        # Lost HTTP response retries must not create another approval or email.
        prior=conn.execute('SELECT * FROM theme_jobs WHERE design_id=? AND idempotency_key=?',(identifier,key)).fetchone()
        if prior:
            payload=db.json_value(prior['input_json'],{})
            expected_kind='production' if action=='approve_design' else action
            if prior['kind']!=expected_kind or (payload.get('approved_design_id') if action=='approve_design' else payload.get('revision_id'))!=revision_id:
                raise RuntimeError('重复提交标识与原确认内容不一致')
            return detail(identifier,actor)
        revision=conn.execute('SELECT * FROM theme_revisions WHERE id=? AND design_id=?',(revision_id,identifier)).fetchone()
        if not revision:
            raise ValueError('请选择当前作品的版本')
        if action=='approve_design':
            if revision['stage']!='design' or design['current_revision_id']!=revision_id:
                raise ValueError('只能确认最新设计图')
            _queue(conn,design,'production','确认设计 R'+str(revision['number'])+'，进入制作',actor['id'],key,{'approved_design_id':revision_id})
            conn.execute('UPDATE theme_designs SET approved_design_id=? WHERE id=?',(revision_id,identifier))
        elif action=='publish':
            if revision['stage']!='production' or design['current_revision_id']!=revision_id or not revision['background_asset_id']:
                raise ValueError('只能确认最新制作完成的版本上线')
            _queue(conn,design,'publish','确认制作效果，正式上线 R'+str(revision['number']),actor['id'],key,{'revision_id':revision_id})
        elif action=='share':
            if design['live_revision_id']!=revision_id or design['is_public']:
                raise ValueError('请先上线个人作品；已公开的作品无需重复公开')
            _queue(conn,design,'share','确认将这个已上线壁纸公开给所有用户',actor['id'],key,{'revision_id':revision_id})
        elif action=='recommend':
            if not design['is_public'] or design['live_revision_id']!=revision_id:
                raise ValueError('只有已公开上线的版本可以发送推荐邮件')
            for user in conn.execute('''SELECT u.id,u.email FROM users u LEFT JOIN theme_mail_preferences p ON p.user_id=u.id
                WHERE u.active=1 AND COALESCE(p.recommendations,1)=1''').fetchall():
                mail_key=f'recommend:{revision_id}:{user["id"]}'
                if not conn.execute('SELECT 1 FROM theme_jobs WHERE design_id=? AND idempotency_key=?',(identifier,mail_key)).fetchone():
                    _queue(conn,design,'recommendation','推荐公开壁纸',actor['id'],mail_key,{'revision_id':revision_id,'recipient_id':user['id']})
            _audit(conn,actor['id'],'recommend_requested',identifier,{'revision_id':revision_id})
        else:
            raise ValueError('未知作品操作')
    return detail(identifier,actor)


def validate_palette(value: dict) -> dict:
    if not isinstance(value,dict):
        raise ValueError('主题配色不是对象')
    allowed=COLOR_KEYS|SCENE_KEYS
    if any(key not in allowed or not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color) for key,color in value.items()):
        raise ValueError('主题只能使用合法颜色，不能包含脚本、CSS 或外部资源')
    return value


def add_asset(identifier: str, actor: dict | None, raw: bytes, name: str, *, job_id: str | None=None,
              runner_id: str='', claim_token: str='', kind: str='reference') -> dict:
    if not actor and not job_id:raise PermissionError('素材上传必须由制作者或已领取的执行器发起')
    if len(raw)>12*1024*1024:
        raise ValueError('单张图片不能超过 12 MB')
    if kind not in {'reference','design_preview','production_preview','production_background'}:
        raise ValueError('不支持的素材类型')
    if actor:
        owned(identifier,actor,mutate=True)
        if kind!='reference':raise PermissionError('用户只能上传参考图片')
    from .workshop_images import normalize_image
    payload,mime,width,height=normalize_image(raw,reference=kind=='reference')
    digest=hashlib.sha256(payload).hexdigest()
    asset_id=str(uuid.uuid4());directory=settings.data_dir/'theme-workshop'/identifier
    path=directory/(asset_id+'.webp')
    with db.transaction() as conn:
        if job_id:
            _claimed(conn,job_id,runner_id,claim_token)
            job=conn.execute('SELECT * FROM theme_jobs WHERE id=?',(job_id,)).fetchone()
            if job['status']!='running':raise RuntimeError('本轮已结束，拒绝迟到素材')
            if job['design_id']!=identifier or (kind.split('_')[0]!=job['kind']):
                raise PermissionError('素材与执行器领取的阶段不一致')
        elif conn.execute("SELECT count(*) FROM theme_assets WHERE design_id=? AND kind='reference'",(identifier,)).fetchone()[0]>=30:
            raise ValueError('每个作品最多保留 30 张参考图')
        existing=conn.execute('SELECT * FROM theme_assets WHERE design_id=? AND kind=? AND sha256=? AND job_id IS ?',
                              (identifier,kind,digest,job_id)).fetchone()
        if existing:return _public_asset(dict(existing))
        directory.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
        now=db.utc_now()
        conn.execute('''INSERT INTO theme_assets(id,design_id,job_id,kind,name,local_path,mime,sha256,size_bytes,width,height,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(asset_id,identifier,job_id,kind,name[:120],str(path),mime,hashlib.sha256(payload).hexdigest(),len(payload),width,height,now))
        _audit(conn,actor['id'] if actor else None,'asset_uploaded',identifier,{'asset_id':asset_id,'kind':kind})
    return _public_asset(db.row('SELECT * FROM theme_assets WHERE id=?',(asset_id,)))


def accessible_asset(asset_id: str, actor: dict | None) -> dict:
    item=db.row('SELECT a.*,d.owner_id,d.is_public,d.live_revision_id FROM theme_assets a JOIN theme_designs d ON d.id=a.design_id WHERE a.id=?',(asset_id,))
    if not item:raise LookupError('素材不存在')
    if actor and (actor['role']=='admin' or actor['id']==item['owner_id']):return item
    revision=db.row('SELECT * FROM theme_revisions WHERE id=?',(item['live_revision_id'],)) if item['is_public'] else None
    if revision and asset_id in {revision['preview_asset_id'],revision['background_asset_id']}:
        return item
    raise PermissionError('素材未公开，只有制作者和管理员可查看')


def _claimed(conn, job_id, runner_id, token):
    job=conn.execute('SELECT * FROM theme_jobs WHERE id=?',(job_id,)).fetchone()
    if not job:raise LookupError('工坊任务不存在')
    if not token or job['runner_id']!=runner_id or job['claim_token']!=token:
        raise PermissionError('工坊任务不属于当前执行器')
    return job


def claim(runner_id: str) -> dict | None:
    with db.transaction() as conn:
        conn.execute('BEGIN IMMEDIATE');_expire(conn)
        job=conn.execute("SELECT * FROM theme_jobs WHERE runner_id=? AND status='queued' ORDER BY CASE WHEN kind IN ('notification','recommendation') THEN 1 ELSE 0 END,created_at,id LIMIT 1",(runner_id,)).fetchone()
        if not job:return None
        job=dict(job);now,token=db.utc_now(),str(uuid.uuid4())
        conn.execute("UPDATE theme_jobs SET status='running',claim_token=?,started_at=?,updated_at=? WHERE id=?",(token,now,now,job['id']))
        if job['kind'] in PHASE_STATUS:
            conn.execute('UPDATE theme_designs SET status=?,updated_at=? WHERE id=?',(PHASE_STATUS[job['kind']],now,job['design_id']))
        design=dict(conn.execute('SELECT * FROM theme_designs WHERE id=?',(job['design_id'],)).fetchone())
        job.update(claim_token=token,design=design,model_config=db.json_value(job['model_config'],{}),input=db.json_value(job['input_json'],{}))
        job['history']=list(reversed([dict(i) for i in conn.execute("SELECT kind,question,answer,thread_id FROM theme_jobs WHERE design_id=? AND status='completed' AND kind IN ('design','production') ORDER BY created_at DESC,id DESC LIMIT 12",(job['design_id'],))]))
        job['assets']=[dict(i) for i in conn.execute('SELECT id,kind,name,job_id FROM theme_assets WHERE design_id=?',(job['design_id'],))]
        job['revisions']=[dict(i) for i in conn.execute('SELECT * FROM theme_revisions WHERE design_id=? ORDER BY number',(job['design_id'],))]
        approved=next((r for r in job['revisions'] if r['id']==job['input'].get('approved_design_id')),None)
        if approved:
            approved_job=conn.execute('SELECT output_json FROM theme_jobs WHERE id=?',(approved['job_id'],)).fetchone()
            job['approved_theme']={'palette':db.json_value(approved['palette'],{}),
                'components':db.json_value(approved_job['output_json'],{}).get('components',{})}
        from .ui_preferences import theme
        job['base_theme']=theme(design['base_theme_id'])
        recipient_id=job['input'].get('recipient_id')
        if recipient_id:
            user=conn.execute('SELECT display_name,email,active FROM users WHERE id=?',(recipient_id,)).fetchone()
            extra=[r[0] for r in conn.execute('SELECT email FROM user_emails WHERE user_id=?',(recipient_id,))]
            enabled=conn.execute('SELECT recommendations FROM theme_mail_preferences WHERE user_id=?',(recipient_id,)).fetchone()
            opted_out=job['kind']=='recommendation' and enabled and not enabled['recommendations']
            job['recipient']={'name':user['display_name'],'emails':list(dict.fromkeys(extra+[user['email']])) if user['active'] and not opted_out else []}
        elif job['kind']=='notification':
            job['recipient']={'name':job['input'].get('recipient_name',''),'emails':job['input'].get('emails',[])}
        return job


def update(job_id: str, runner_id: str, claim_token: str, *, status: str='running', answer: str='',
           progress: str='', thread_id: str='', error_message: str='', result: dict | None=None) -> dict:
    if status not in {'running','completed','failed'}:raise ValueError('无效工坊任务状态')
    with db.transaction() as conn:
        job=_claimed(conn,job_id,runner_id,claim_token)
        # Heartbeats and terminal errors must not erase the native conversation
        # or recovery evidence recorded by an earlier update.
        result=db.json_value(job['output_json'],{}) if result is None else dict(result)
        thread_id=thread_id or job['thread_id']
        progress=progress or job['progress']
        if job['status']!='running':
            saved=db.json_value(job['output_json'],{})
            if job['status']==status and all(saved.get(k)==v for k,v in result.items()) and job['answer']==answer:
                return _public_job(dict(job))
            raise RuntimeError('本轮已结束，拒绝迟到结果')
        now=db.utc_now();design=dict(conn.execute('SELECT * FROM theme_designs WHERE id=?',(job['design_id'],)).fetchone())
        if status=='completed' and job['kind'] in {'design','production'}:
            palette=validate_palette(result.get('palette',{}));preview_id=result.get('preview_asset_id')
            result['components']=validate_components(result.get('components',{}))
            bg_id=result.get('background_asset_id')
            for asset_id,kind in [(preview_id,job['kind']+'_preview')]+([(bg_id,'production_background')] if job['kind']=='production' else []):
                if not conn.execute('SELECT 1 FROM theme_assets WHERE id=? AND job_id=? AND kind=?',(asset_id,job_id,kind)).fetchone():
                    raise ValueError('生成素材尚未校验保存，不能完成此轮')
            if not result.get('imagegen_verified'):raise ValueError('未收到原生 imagegen 成功事件，不能以占位图完成')
            revision_id=str(uuid.uuid4());number=conn.execute('SELECT COALESCE(MAX(number),0)+1 FROM theme_revisions WHERE design_id=?',(design['id'],)).fetchone()[0]
            conn.execute('INSERT INTO theme_revisions(id,design_id,job_id,number,stage,preview_asset_id,background_asset_id,palette,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                         (revision_id,design['id'],job_id,number,job['kind'],preview_id,bg_id,_json(palette),now))
            result.update(revision_id=revision_id,number=number)
            conn.execute('UPDATE theme_designs SET current_revision_id=?,status=?,updated_at=? WHERE id=?',
                         (revision_id,job['kind']+'_review',now,design['id']))
        elif status=='completed' and job['kind'] in {'publish','share'}:
            payload=db.json_value(job['input_json'],{});revision=conn.execute('SELECT * FROM theme_revisions WHERE id=? AND design_id=?',(payload['revision_id'],design['id'])).fetchone()
            if not revision or revision['stage']!='production':raise ValueError('没有可上线的制作版本')
            published=result.get('assets',[])
            expected={revision['preview_asset_id'],revision['background_asset_id']}
            public=bool(design['is_public'] or job['kind']=='share')
            if not result.get('oss_verified') or result.get('visibility')!=('public' if public else 'private'):
                raise ValueError('OSS 校验或可见范围不一致，不能上线')
            if len(published)!=2 or {a.get('id') for a in published}!=expected:raise ValueError('上线资产不完整')
            for asset in published:
                link=asset.get('url','');key=asset.get('oss_key','')
                if not re.fullmatch(r'https://[a-zA-Z0-9.-]+\.aliyuncs\.com/autodev-static/workshop/[a-zA-Z0-9_./-]+(?:\?[^<>"\s]*)?',link):
                    raise ValueError('不是已校验的 OSS 素材地址')
                if not key.startswith(f'autodev-static/workshop/{design["id"]}/{revision["id"]}/') or '..' in key:raise ValueError('OSS 素材目录不属于此作品')
                from urllib.parse import urlsplit,unquote
                if unquote(urlsplit(link).path).lstrip('/')!=key:raise ValueError('OSS 素材地址与目录不一致')
                expires=asset.get('expires_at')
                if public and (expires or urlsplit(link).query):raise ValueError('公开素材必须使用永久地址')
                if not public:
                    try:expiry=datetime.fromisoformat(expires)
                    except (TypeError,ValueError):raise ValueError('私人素材须有有效签名期限')
                    if not urlsplit(link).query or expiry.tzinfo is None or expiry<=datetime.now(UTC):raise ValueError('私人素材签名已过期')
                conn.execute('UPDATE theme_assets SET external_url=?,url_expires_at=?,oss_key=? WHERE id=?',
                             (link,asset.get('expires_at'),key,asset['id']))
            conn.execute("UPDATE theme_designs SET status='live',is_public=?,live_revision_id=?,updated_at=? WHERE id=?",(public,revision['id'],now,design['id']))
            if job['kind']=='publish':
                recipients={design['owner_id']}|{r[0] for r in conn.execute("SELECT id FROM users WHERE role='admin' AND active=1")}
                contacts={}
                for uid in recipients:
                    user=conn.execute('SELECT display_name,email FROM users WHERE id=?',(uid,)).fetchone()
                    addresses=[r[0] for r in conn.execute('SELECT email FROM user_emails WHERE user_id=?',(uid,))]+[user['email']]
                    contacts.update({address.casefold():(address,user['display_name']) for address in addresses if address})
                for address in re.split(r'[,;]',settings.task_admin_email):
                    if address.strip():contacts.setdefault(address.strip().casefold(),(address.strip(),'管理员'))
                for email,person in contacts.values():
                    mail_key=f'live:{revision["id"]}:'+hashlib.sha256(email.casefold().encode()).hexdigest()[:20]
                    _queue(conn,design,'notification','壁纸已正式上线',job['actor_id'],mail_key,
                           {'revision_id':revision['id'],'emails':[email],'recipient_name':person})
            _audit(conn,job['actor_id'],job['kind']+'_activated',design['id'],{'revision_id':revision['id']})
        elif status=='failed' and job['kind'] in PHASE_STATUS:
            conn.execute('UPDATE theme_designs SET status=?,updated_at=? WHERE id=?',(job['kind']+'_failed',now,design['id']))
        conn.execute('''UPDATE theme_jobs SET status=?,answer=?,progress=?,thread_id=?,error_message=?,output_json=?,updated_at=?,completed_at=? WHERE id=?''',
                     (status,answer[:30000],progress[:1000],thread_id[:100],error_message[:3000],_json(result),now,now if status!='running' else None,job_id))
        return _public_job(dict(conn.execute('SELECT * FROM theme_jobs WHERE id=?',(job_id,)).fetchone()))


def custom_theme(design: dict, revision: dict) -> dict:
    from .ui_preferences import catalog
    base=next(t for t in catalog()['themes'] if t['id']==design['base_theme_id'])
    palette=db.json_value(revision['palette'],{});tokens=dict(base['tokens']);scene_palette=dict(base['scenePalette'])
    tokens.update({k:v for k,v in palette.items() if k in COLOR_KEYS});scene_palette.update({k:v for k,v in palette.items() if k in SCENE_KEYS})
    job=db.row('SELECT output_json FROM theme_jobs WHERE id=?',(revision['job_id'],))
    components=validate_components(db.json_value(job['output_json'],{}).get('components',{}))
    component_values,adjusted=component_tokens(tokens,components);tokens.update(component_values)
    preview=db.row('SELECT * FROM theme_assets WHERE id=?',(revision['preview_asset_id'],))
    background=db.row('SELECT * FROM theme_assets WHERE id=?',(revision['background_asset_id'],))
    return {**base,'id':'workshop-'+design['id'],'base_theme_id':base['id'],'number':'W','name':design['name'],
            'subtitle':'公开作品' if design['is_public'] else '个人设计','preview':asset_url(preview),
            'background':asset_url(background),'tokens':tokens,'scenePalette':scene_palette,'is_custom':True,
            'owner_id':design['owner_id'],'revision_id':revision['id'],'is_public':bool(design['is_public']),
            'components':components,'component_contrast_adjustments':adjusted,
            'preview_fallback':'/api/workshop/assets/'+preview['id'],
            'background_fallback':'/api/workshop/assets/'+background['id']}


def themes_for_user(user_id: int | None, *, include_trials=False) -> list[dict]:
    result=[]
    for design in db.rows('SELECT * FROM theme_designs WHERE live_revision_id IS NOT NULL OR owner_id=?',(user_id or -1,)):
        revisions=[]
        if design['live_revision_id'] and (design['is_public'] or design['owner_id']==user_id):
            revisions.append(db.row('SELECT * FROM theme_revisions WHERE id=?',(design['live_revision_id'],)))
        if include_trials and design['owner_id']==user_id and design['current_revision_id']!=design['live_revision_id']:
            candidate=db.row('SELECT * FROM theme_revisions WHERE id=?',(design['current_revision_id'],))
            if candidate and candidate['stage']=='production':revisions.append(candidate)
        for revision in revisions:
            item=custom_theme(design,revision);item['trial']=revision['id']!=design['live_revision_id'];item['design_id']=design['id']
            if item['trial']:item['id']='workshop-trial-'+revision['id'];item['is_public']=False
            item['author']=db.row('SELECT display_name FROM users WHERE id=?',(design['owner_id'],))['display_name']
            item['usage']=usage_summary('workshop-'+design['id']);result.append(item)
    return result


def retry_job(job_id: str, actor: dict, key: str) -> dict:
    job=db.row('SELECT * FROM theme_jobs WHERE id=?',(job_id,))
    if not job:raise LookupError('工坊任务不存在')
    design=owned(job['design_id'],actor,mutate=True)
    if job['status']!='failed':raise ValueError('只有未完成的轮次支持重试')
    payload=db.json_value(job['input_json'],{})
    if job['kind'] in {'publish','share'} and payload.get('revision_id')!=design['current_revision_id']:
        raise ValueError('制作版本已变化，请确认当前版本而不是重试旧版本')
    with db.transaction() as conn:
        _queue(conn,design,job['kind'],job['question'],actor['id'],key,{**payload,'retry_of':job_id})
    return detail(design['id'],actor)


def resolve_theme(theme_id: str, user_id: int | None) -> dict | None:
    if not theme_id.startswith('workshop-'):return None
    return next((t for t in themes_for_user(user_id) if t['id']==theme_id),None)


def record_usage(user_id: int, theme_id: str, kind: str, *, key: str | None=None):
    if kind not in {'activate','open','preview'}:raise ValueError('无效壁纸使用事件')
    if not key:key=str(uuid.uuid4()) if kind=='activate' else db.utc_now()[:10]
    with db.transaction() as conn:
        conn.execute('INSERT OR IGNORE INTO theme_usage_events(user_id,theme_id,kind,dedupe_key,created_at) VALUES (?,?,?,?,?)',
                     (user_id,theme_id,kind,key,db.utc_now()))


def usage_summary(theme_id: str) -> dict:
    summary=db.row("SELECT count(DISTINCT CASE WHEN kind IN ('activate','open') THEN user_id END) users,count(CASE WHEN kind='activate' THEN 1 END) activations,count(CASE WHEN kind='open' THEN 1 END) visits FROM theme_usage_events WHERE theme_id=?",(theme_id,))
    summary['current']=db.row('SELECT count(*) n FROM user_ui_preferences WHERE theme_id=?',(theme_id,))['n']
    return summary


def usage_history(user_id: int) -> list[dict]:
    return db.rows('SELECT theme_id,kind,created_at FROM theme_usage_events WHERE user_id=? ORDER BY id DESC LIMIT 100',(user_id,))


def mail_preference(user_id: int, enabled: bool | None=None) -> bool:
    if enabled is not None:
        with db.transaction() as conn:
            conn.execute('INSERT INTO theme_mail_preferences(user_id,recommendations,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET recommendations=excluded.recommendations,updated_at=excluded.updated_at',
                         (user_id,int(enabled),db.utc_now()))
    result=db.row('SELECT recommendations FROM theme_mail_preferences WHERE user_id=?',(user_id,))
    return bool(result['recommendations']) if result else True
