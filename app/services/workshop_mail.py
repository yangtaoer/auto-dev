"""Server-owned workshop mail using the existing platform SMTP configuration."""
from __future__ import annotations

import hashlib
import html
import json
import smtplib

from app import db
from app.config import settings
from .delivery import Mailer
from .theme_workshop import _claimed, _audit
from .codex_runner import CodexRunner


def mail_error_detail(error):
    # Reuse credential redaction without labeling a mail failure as a model error.
    return CodexRunner._failure_message({'message':str(error)}).rsplit('上游详情：',1)[-1]


def build_mail(job, design, recipient):
    recommendation=job['kind']=='recommendation'
    title='发现一张新壁纸' if recommendation else '壁纸已正式上线'
    name=html.escape(design['name']);creator=html.escape(design['author'])
    greeting=html.escape(recipient.get('name') or '朋友')
    link=settings.public_base_url+('/?workshop-public=' if recommendation else '/?workshop=')+design['id']
    body=f'''<div style="background:#eef5ef;padding:28px;font-family:'Microsoft YaHei',sans-serif;color:#082c30"><div style="max-width:680px;margin:auto;background:#fffefb;border:1px solid #c9d8d2;border-radius:18px;overflow:hidden"><header style="background:#d6e9dc;padding:28px"><small>AutoDev · CREATIVE WORKSHOP</small><h1 style="margin-bottom:0;font-size:26px">{title}</h1></header><div style="padding:28px"><p>{greeting}，你好。</p><h2 style="margin-bottom:8px">{name}</h2><p style="margin-top:0;color:#607d85;font-size:14px">由 <strong style="color:#257e65">{creator}</strong> 创作{' · 公开壁纸' if recommendation else ''}</p><p>{'这张壁纸由制作者公开分享，可在公共壁纸中预览和选择。' if recommendation else '本次制作版本已通过制作者确认，正式加入个人设计。壁纸作为动态数据上线，无需发布新的应用版本。'}</p><p><a style="display:inline-block;background:#ff582c;color:white;padding:12px 22px;border-radius:30px;text-decoration:none" href="{html.escape(link,quote=True)}">{'选择新壁纸' if recommendation else '查看作品与交互记录'} ↗</a></p><p style="font-size:12px;color:#607d85">{'不想收到壁纸推荐？可在创意工坊关闭推荐邮件。' if recommendation else '设计、制作、确认记录与使用统计均可在创意工坊查看。'}</p></div></div></div>'''
    subject_name=' '.join(design['name'].split())
    return {'to':recipient['emails'],'subject':f'【AutoDev 创意工坊】{title} · {subject_name}',
            'html_body':body,'include_admin_copy':False}


def send_claimed(job_id,runner_id,claim_token):
    """Authenticate a real queued job, recheck opt-out, and persist its SMTP receipt."""
    with db.transaction() as conn:
        job=dict(_claimed(conn,job_id,runner_id,claim_token))
        if job['kind'] not in {'notification','recommendation'}:raise ValueError('只有邮件通知任务可调用发信接口')
        design=dict(conn.execute('SELECT d.*,u.display_name author FROM theme_designs d JOIN users u ON u.id=d.owner_id WHERE d.id=?',(job['design_id'],)).fetchone())
        payload=db.json_value(job['input_json'],{})
        revision_id=payload.get('revision_id')
        if not conn.execute("SELECT 1 FROM theme_revisions WHERE id=? AND design_id=? AND stage='production'",(revision_id,design['id'])).fetchone():
            raise ValueError('邮件没有已制作完成的作品版本')
        if job['kind']=='recommendation':
            if not design['is_public'] or revision_id!=design['live_revision_id']:raise ValueError('推荐邮件只能发送当前公开上线的壁纸')
            user=conn.execute('SELECT display_name,email,active FROM users WHERE id=?',(payload.get('recipient_id'),)).fetchone()
            if not user:raise ValueError('邮件收件账号不存在')
            preference=conn.execute('SELECT recommendations FROM theme_mail_preferences WHERE user_id=?',(payload['recipient_id'],)).fetchone()
            extra=[r[0] for r in conn.execute('SELECT email FROM user_emails WHERE user_id=?',(payload['recipient_id'],))]
            allowed=user['active'] and (not preference or preference['recommendations'])
            recipient={'name':user['display_name'],'emails':Mailer._deduplicate_emails(extra+[user['email']]) if allowed else []}
            delivery_key=f'recommend:{revision_id}:{payload["recipient_id"]}'
        else:
            if not design['live_revision_id']:raise ValueError('作品尚未上线，不能发送上线通知')
            recipient={'name':payload.get('recipient_name',''),'emails':Mailer._deduplicate_emails(payload.get('emails',[]))}
            delivery_key=f'live:{revision_id}:'+hashlib.sha256('\n'.join(sorted(e.casefold() for e in recipient['emails'])).encode()).hexdigest()
        prior=conn.execute('SELECT * FROM theme_mail_deliveries WHERE delivery_key=?',(delivery_key,)).fetchone()
        if prior and prior['status'] in {'sent','skipped'}:return {**db.json_value(prior['result_json'],{}),'deduplicated':True}
        if prior and prior['status']=='sending':return {'pending':True}
        if prior and prior['status']=='uncertain':raise RuntimeError('邮件服务器应答中断，投递结果暂不确定；请核实收件结果，避免重复投递。')
        if job['status']!='running':raise RuntimeError('本轮邮件任务已结束，拒绝迟到发信')
        result={'sent':False,'recipients':0,'transport':'server_smtp','author':design['author']}
        if not recipient['emails']:
            result['reason']='recipient_opted_out_or_missing'
            conn.execute('INSERT OR REPLACE INTO theme_mail_deliveries(delivery_key,job_id,status,result_json,updated_at) VALUES (?,?,?,?,?)',
                         (delivery_key,job_id,'skipped',json.dumps(result,ensure_ascii=False),db.utc_now()))
            return result
        if not Mailer().configured():raise RuntimeError('云端默认邮件配置缺失，请恢复平台已有的 SMTP_HOST/SMTP_FROM 配置。')
        conn.execute('INSERT OR REPLACE INTO theme_mail_deliveries(delivery_key,job_id,status,updated_at) VALUES (?,?,?,?)',
                     (delivery_key,job_id,'sending',db.utc_now()))
    try:
        Mailer().send(**build_mail(job,design,recipient))
        result.update(sent=True,recipients=len(recipient['emails']),sender=settings.smtp_from)
    except Exception as exc:
        # SMTP cannot guarantee exactly-once delivery after an ambiguous lost
        # response. Do not automatically send a second copy in that case.
        known_rejected=isinstance(exc,(ValueError,smtplib.SMTPAuthenticationError,smtplib.SMTPRecipientsRefused,smtplib.SMTPDataError,smtplib.SMTPConnectError))
        with db.transaction() as conn:
            conn.execute('UPDATE theme_mail_deliveries SET status=?,error_message=?,updated_at=? WHERE delivery_key=?',
                         ('failed' if known_rejected else 'uncertain',mail_error_detail(exc),db.utc_now(),delivery_key))
        raise RuntimeError('默认邮箱发送失败：'+mail_error_detail(exc)) from None
    with db.transaction() as conn:
        conn.execute("UPDATE theme_mail_deliveries SET status='sent',result_json=?,updated_at=? WHERE delivery_key=?",
                     (json.dumps(result,ensure_ascii=False),db.utc_now(),delivery_key))
        _audit(conn,job['actor_id'],'mail_sent',design['id'],{'job_id':job_id,'kind':job['kind'],'recipients':len(recipient['emails'])})
    return result
