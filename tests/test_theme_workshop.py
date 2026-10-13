from __future__ import annotations
import os,subprocess,sys,tempfile,textwrap,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PRELUDE=r'''
import json,uuid
from datetime import datetime,UTC,timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import db
from app.config import settings
from app.services import theme_workshop as w,ui_preferences
db.init_db()
owner=db.row("SELECT * FROM users WHERE username='admin'")
other=db.row("SELECT * FROM users WHERE username='pm'")
def key():return str(uuid.uuid4())
def image(color='green'):
    out=BytesIO();Image.new('RGB',(256,160),color).save(out,'PNG');return out.getvalue()
def create():return w.create('雨后书房','mint-garden',owner,key())
def finish(job):
    kind=job['kind'];args=dict(job_id=job['id'],runner_id=job['runner_id'],claim_token=job['claim_token'])
    preview=w.add_asset(job['design_id'],None,image(),'预览',kind=kind+'_preview',**args)
    result=dict(preview_asset_id=preview['id'],palette={'skin-bg':'#F5F8F4'},imagegen_verified=True)
    if kind=='production':result['background_asset_id']=w.add_asset(job['design_id'],None,image('blue'),'背景',kind='production_background',**args)['id']
    w.update(job['id'],job['runner_id'],job['claim_token'],status='completed',answer='## 完成\n\n保留布局。',result=result)
    return w.detail(job['design_id'],owner)
def production():
    d=create();w.submit_round(d['id'],owner,'design','我的想法',[],key());d=finish(w.claim(settings.runner_id))
    w.action(d['id'],owner,'approve_design',d['current_revision_id'],key());return finish(w.claim(settings.runner_id))
def publish(d,public=False):
    w.action(d['id'],owner,'share' if public else 'publish',d['live_revision_id'] if public else d['current_revision_id'],key())
    # Notification outbox jobs may precede a share, complete those without sending mail.
    job=w.claim(settings.runner_id)
    while job['kind'] in {'notification','recommendation'}:
        w.update(job['id'],job['runner_id'],job['claim_token'],status='completed',answer='mock mail',result={'sent':True});job=w.claim(settings.runner_id)
    rev=next(r for r in job['revisions'] if r['id']==job['input']['revision_id'])
    assets=[]
    for aid in [rev['preview_asset_id'],rev['background_asset_id']]:
        path=f"autodev-static/workshop/{d['id']}/{rev['id']}/{aid}.webp"
        assets.append({'id':aid,'oss_key':path,'url':'https://auto-dev-oss.oss-cn-chengdu.aliyuncs.com/'+path+('' if public else '?signed=test'),
            'expires_at':None if public else (datetime.now(UTC)+timedelta(hours=1)).isoformat()})
    w.update(job['id'],job['runner_id'],job['claim_token'],status='completed',answer='上线',result={'assets':assets,'oss_verified':True,'visibility':'public' if public else 'private'})
    return w.detail(d['id'],owner)
def rejects(fn,errors=(ValueError,RuntimeError,PermissionError)):
    try:fn()
    except errors:return
    raise AssertionError('Expected rejection')
'''

class ThemeWorkshopTests(unittest.TestCase):
    def check(self,code):
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,AUTODEV_ENV_FILE=str(Path(folder)/'unused.env'),AUTODEV_DATA_DIR=folder,
                AUTODEV_WORKER_ENABLED='0',AUTODEV_SEED_DEMO='1',AUTODEV_ENV='development',
                BOOTSTRAP_ADMIN_PASSWORD='workshop-test-only',PYTHONIOENCODING='utf-8')
            result=subprocess.run([sys.executable,'-c',PRELUDE+textwrap.dedent(code)],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_drafts_rounds_and_approvals_are_owned_and_idempotent(self):
        self.check('''
        k=key();d=w.create('书房','mint-garden',owner,k);assert w.create('书房','mint-garden',owner,k)['id']==d['id']
        rejects(lambda:w.create('不同','mint-garden',owner,k))
        rejects(lambda:w.submit_round(d['id'],other,'design','冒用',[],key()))
        rejects(lambda:w.submit_round(d['id'],owner,'production','未确认',[],key()))
        k=key();w.submit_round(d['id'],owner,'design','第一轮',[],k);w.submit_round(d['id'],owner,'design','第一轮',[],k)
        rejects(lambda:w.submit_round(d['id'],owner,'design','改内容',[],k))
        rejects(lambda:w.submit_round(d['id'],owner,'design','并发',[],key()))
        d=finish(w.claim(settings.runner_id));k=key();w.action(d['id'],owner,'approve_design',d['current_revision_id'],k)
        w.action(d['id'],owner,'approve_design',d['current_revision_id'],k)
        rejects(lambda:w.action(d['id'],owner,'share',d['current_revision_id'],k))
        assert db.row('SELECT count(*) n FROM theme_jobs')['n']==2
        ''')

    def test_generation_requires_native_proof_complete_assets_and_lease(self):
        self.check('''
        d=create();w.submit_round(d['id'],owner,'design','test',[],key());job=w.claim(settings.runner_id)
        rejects(lambda:w.add_asset(d['id'],None,image(),'无身份'))
        rejects(lambda:w.update(job['id'],'wrong',job['claim_token'],status='completed'))
        args=dict(job_id=job['id'],runner_id=job['runner_id'],claim_token=job['claim_token'])
        rejects(lambda:w.add_asset(d['id'],None,image(),'wrong',kind='production_preview',**args))
        a=w.add_asset(d['id'],None,image(),'one',kind='design_preview',**args)
        assert w.add_asset(d['id'],None,image(),'retry',kind='design_preview',**args)['id']==a['id']
        rejects(lambda:w.update(job['id'],job['runner_id'],job['claim_token'],status='completed',result={'preview_asset_id':a['id']}))
        d=finish(job);rev=d['current_revision_id']
        rejects(lambda:w.add_asset(d['id'],None,image('red'),'late',kind='design_preview',**args))
        rejects(lambda:w.update(job['id'],job['runner_id'],job['claim_token'],status='running'))
        assert w.detail(d['id'],owner)['current_revision_id']==rev
        ''')

    def test_private_and_trial_artwork_do_not_leak_or_overwrite_live(self):
        self.check('''
        d=production();assert not w.themes_for_user(other['id'],include_trials=True)
        trial=w.themes_for_user(owner['id'],include_trials=True)[0];assert trial['trial'] and trial['id'].startswith('workshop-trial-')
        rejects(lambda:ui_preferences.save(owner['id'],{'theme_id':trial['id']}))
        rejects(lambda:w.accessible_asset(d['assets'][0]['id'],other))
        rejects(lambda:w.accessible_asset(d['assets'][0]['id'],None))
        d=publish(d);old=d['live_revision_id'];assert w.resolve_theme('workshop-'+d['id'],other['id']) is None
        w.submit_round(d['id'],owner,'production','第二版',[],key());d=finish(w.claim(settings.runner_id))
        assert d['live_revision_id']==old and d['current_revision_id']!=old
        own=w.themes_for_user(owner['id'],include_trials=True);assert len(own)==2 and len({t['id'] for t in own})==2
        live=next(t for t in own if not t['trial']);assert live['revision_id']==old
        ''')

    def test_public_catalog_hot_updates_and_reference_privacy(self):
        self.check('''
        d=production();ref=w.add_asset(d['id'],owner,image('orange'),'private-ref')
        before=Path('VERSION').read_bytes();d=publish(d);d=publish(d,True)
        theme=w.resolve_theme('workshop-'+d['id'],other['id']);assert theme and theme['is_public']
        assert Path('VERSION').read_bytes()==before
        assert w.accessible_asset(d['revisions'][-1]['preview_asset_id'],None)
        rejects(lambda:w.accessible_asset(ref['id'],None));rejects(lambda:w.detail(d['id'],other))
        assert 'jobs' not in theme and 'assets' not in theme and 'local_path' not in json.dumps(theme)
        assert 'jobs' not in w.list_designs(owner)[0]
        ''')

    def test_oss_requires_complete_verified_visibility_and_scoped_keys(self):
        self.check('''
        d=production();w.action(d['id'],owner,'publish',d['current_revision_id'],key());job=w.claim(settings.runner_id)
        args=(job['id'],job['runner_id'],job['claim_token'])
        rejects(lambda:w.update(*args,status='completed',result={'assets':[]}))
        rev=job['revisions'][-1];assets=[]
        for aid in [rev['preview_asset_id'],rev['background_asset_id']]:
            path=f"autodev-static/workshop/{d['id']}/{rev['id']}/{aid}.webp"
            assets.append(dict(id=aid,oss_key=path,url='https://auto-dev-oss.oss-cn-chengdu.aliyuncs.com/'+path,expires_at=None))
        rejects(lambda:w.update(*args,status='completed',result=dict(assets=assets,visibility='public',oss_verified=True)))
        rejects(lambda:w.update(*args,status='completed',result=dict(assets=assets,visibility='private',oss_verified=True)))
        assert w.detail(d['id'],owner)['live_revision_id'] is None
        ''')

    def test_stale_jobs_fail_safely_and_retry_preserves_history(self):
        self.check('''
        d=create();w.submit_round(d['id'],owner,'design','test',[],key());old=w.claim(settings.runner_id)
        with db.transaction() as conn:conn.execute("UPDATE theme_jobs SET updated_at='2000-01-01T00:00:00+00:00'")
        w.list_designs(owner);assert w.detail(d['id'],owner)['jobs'][0]['status']=='failed'
        rejects(lambda:w.update(old['id'],old['runner_id'],old['claim_token'],status='completed'))
        d=w.retry_job(old['id'],owner,key());assert len(d['jobs'])==2 and d['jobs'][0]['status']=='failed'
        new=w.claim(settings.runner_id);assert new['input']['retry_of']==old['id'];finish(new)
        ''')

    def test_usage_persists_by_account_without_duplicate_poll_events(self):
        self.check('''
        d=publish(production());tid='workshop-'+d['id'];values={**ui_preferences.DEFAULTS,'theme_id':tid};ui_preferences.save(owner['id'],values)
        ui_preferences.save(owner['id'],values);assert ui_preferences.get(owner['id'])['theme_id']==tid
        for i in range(100):w.record_usage(owner['id'],tid,'open')
        summary=w.usage_summary(tid);assert summary==dict(users=1,activations=1,visits=1,current=1),summary
        assert len(w.usage_history(owner['id']))==2 and not w.usage_history(other['id'])
        ''')

    def test_mail_outbox_deduplicates_and_respects_recommendation_opt_out(self):
        self.check('''
        d=publish(production());notifications=db.rows("SELECT * FROM theme_jobs WHERE kind='notification'")
        assert notifications and all(len(json.loads(j['input_json'])['emails'])==1 for j in notifications)
        d=publish(d,True);w.mail_preference(other['id'],False)
        for i in range(2):w.action(d['id'],owner,'recommend',d['live_revision_id'],key())
        jobs=db.rows("SELECT * FROM theme_jobs WHERE kind='recommendation'");assert len(jobs)==1,jobs
        assert json.loads(jobs[0]['input_json'])['recipient_id']==owner['id']
        w.mail_preference(owner['id'],False);job=w.claim(settings.runner_id)
        while job['kind']=='notification':
            w.update(job['id'],job['runner_id'],job['claim_token'],status='completed',answer='mock mail');job=w.claim(settings.runner_id)
        assert job['recipient']['emails']==[]
        ''')

    def test_raster_validation_and_color_injection(self):
        self.check('''
        d=create();rejects(lambda:w.add_asset(d['id'],owner,b'<svg onload="alert(1)">','attack'))
        rejects(lambda:w.validate_palette({'skin-bg':'url(https://evil)'}))
        rejects(lambda:w.validate_palette({'unknown':'#FFFFFF'}))
        a=w.add_asset(d['id'],owner,image(),'good');assert a['mime']=='image/webp' and a['size_bytes']<12000
        assert a['width']==256 and 'local_path' not in a
        ''')

    def test_production_keeps_all_five_references_plus_approved_and_latest_context(self):
        self.check('''
        import threading
        from app.services.workshop_runner import WorkshopProcessor,WorkshopImagegen
        base=ui_preferences.catalog()['themes'][0];refs=['ref-'+str(i) for i in range(5)]
        job=dict(id='job',design_id='design',kind='production',input={'reference_ids':refs,'approved_design_id':'approved'},
          model_config={'model':'gpt-6.1-sol','effort':'xhigh'},base_theme=base,revisions=[{'id':'approved','preview_asset_id':'approved-art'},{'id':'latest','preview_asset_id':'latest-art'}])
        class Store:
            def workshop_update(self,*args,**kw):self.last=kw
            def workshop_asset(self,*args):return image()
            def workshop_upload(self,*args):return {'id':key()}
        store=Store()
        with patch.object(WorkshopImagegen,'generate',return_value={'answer':'完成','thread_id':'thread','images':[image(),image()],
             'imagegen_verified':True,'palette':{}}) as generator:
            WorkshopProcessor(store).run(job,threading.Event())
            files=generator.call_args.args[2]
            assert {p.stem for p in files[1:]}==set(refs+['approved-art','latest-art']),files
            assert len(files)==8 and store.last['status']=='completed',store.last
        ''')

    def test_heartbeats_and_failure_preserve_thread_and_recovery_evidence(self):
        self.check('''
        d=create();w.submit_round(d['id'],owner,'design','test',[],key());j=w.claim(settings.runner_id)
        args=(j['id'],j['runner_id'],j['claim_token'])
        w.update(*args,thread_id='native-thread',progress='自动重试',result={'attempts':[{'attempt':1}]})
        w.update(*args,progress='等待模型恢复')
        r=w.update(*args,status='failed',error_message='overloaded')
        assert r['thread_id']=='native-thread' and r['progress']=='等待模型恢复',r
        assert r['output']['attempts']==[{'attempt':1}],r
        from app.main import app
        from app.workshop_api import WorkshopResult
        assert WorkshopResult(runner_id=j['runner_id'],claim_token=j['claim_token']).result is None
        d=w.retry_job(j['id'],owner,key());assert len(d['jobs'])==2
        assert d['jobs'][0]['thread_id']=='native-thread' and d['jobs'][0]['output']['attempts']
        ''')

    def test_custom_theme_owns_component_contract_and_legacy_palette_remains_compatible(self):
        self.check('''
        d=production();theme=w.themes_for_user(owner['id'],include_trials=True)[0]
        assert theme['is_custom'] and theme['components']['button_shape']=='soft'
        assert theme['tokens']['skin-field-focus']==theme['tokens']['skin-accent']
        assert 'skin-primary-ink' in theme['tokens'] and 'skin-button-radius' in theme['tokens']
        assert theme['tokens']['skin-component-font'].endswith('sans-serif')
        ''')

    def test_mail_uses_server_default_sender_has_creator_and_deduplicates_retries(self):
        self.check('''
        from app.services.workshop_mail import send_claimed
        d=publish(production());d=publish(d,True)
        w.action(d['id'],owner,'recommend',d['live_revision_id'],key())
        j=w.claim(settings.runner_id)
        while j['kind']=='notification':
            w.update(j['id'],j['runner_id'],j['claim_token'],status='completed',answer='mock');j=w.claim(settings.runner_id)
        with patch('app.services.workshop_mail.Mailer.configured',return_value=True),patch('app.services.workshop_mail.Mailer.send') as send:
            result=send_claimed(j['id'],j['runner_id'],j['claim_token'])
            assert result['sent'] and result['sender']==settings.smtp_from and result['author']==owner['display_name'],result
            assert not send.call_args.kwargs['include_admin_copy']
            assert '由 <strong' in send.call_args.kwargs['html_body'] and owner['display_name'] in send.call_args.kwargs['html_body']
            assert send_claimed(j['id'],j['runner_id'],j['claim_token'])['deduplicated']
            w.update(j['id'],j['runner_id'],j['claim_token'],status='failed',error_message='lost reply')
            retry=w.retry_job(j['id'],owner,key())
            # Other queued recipients can be processed independently; this retry shares the same durable key.
            with db.transaction() as conn:
                r=conn.execute('SELECT * FROM theme_jobs WHERE id=?',(retry['jobs'][-1]['id'],)).fetchone()
                conn.execute("UPDATE theme_jobs SET status='running',claim_token='retry-token' WHERE id=?",(r['id'],))
            assert send_claimed(r['id'],j['runner_id'],'retry-token')['deduplicated']
            assert send.call_count==1
        ''')

    def test_mail_rechecks_opt_out_and_rejects_wrong_lease_or_non_mail_jobs(self):
        self.check('''
        from app.services.workshop_mail import send_claimed
        d=publish(production());d=publish(d,True)
        w.action(d['id'],owner,'recommend',d['live_revision_id'],key());j=w.claim(settings.runner_id)
        while j['kind']=='notification':
            w.update(j['id'],j['runner_id'],j['claim_token'],status='completed');j=w.claim(settings.runner_id)
        rejects(lambda:send_claimed(j['id'],'other-runner',j['claim_token']))
        w.mail_preference(j['input']['recipient_id'],False)
        with patch('app.services.workshop_mail.Mailer.send') as send:
            result=send_claimed(j['id'],j['runner_id'],j['claim_token']);assert not result['sent'];send.assert_not_called()
        fresh=create();w.submit_round(fresh['id'],owner,'design','x',[],key())
        # Directly lease this isolated design fixture to verify the endpoint cannot act as an arbitrary mail relay.
        with db.transaction() as conn:
            row=conn.execute("SELECT * FROM theme_jobs WHERE design_id=?",(fresh['id'],)).fetchone()
            conn.execute("UPDATE theme_jobs SET status='running',claim_token='design-token' WHERE id=?",(row['id'],))
        rejects(lambda:send_claimed(row['id'],settings.runner_id,'design-token'))
        ''')

    def test_mail_pending_and_ambiguous_delivery_never_sends_another_copy(self):
        self.check('''
        from app.services.workshop_mail import send_claimed
        d=publish(production());j=w.claim(settings.runner_id)
        k='live:'+j['input']['revision_id']+':'+__import__('hashlib').sha256('\\n'.join(sorted(e.casefold() for e in j['input']['emails'])).encode()).hexdigest()
        with db.transaction() as conn:conn.execute("INSERT INTO theme_mail_deliveries(delivery_key,job_id,status,updated_at) VALUES(?,?,'sending',?)",(k,j['id'],db.utc_now()))
        with patch('app.services.workshop_mail.Mailer.send') as send:
            assert send_claimed(j['id'],j['runner_id'],j['claim_token'])['pending'];send.assert_not_called()
        with db.transaction() as conn:conn.execute("DELETE FROM theme_mail_deliveries WHERE delivery_key=?",(k,))
        with patch('app.services.workshop_mail.Mailer.configured',return_value=True),patch('app.services.workshop_mail.Mailer.send',side_effect=TimeoutError('server response lost')) as send:
            rejects(lambda:send_claimed(j['id'],j['runner_id'],j['claim_token']))
            rejects(lambda:send_claimed(j['id'],j['runner_id'],j['claim_token']))
            assert send.call_count==1 and db.row('SELECT status FROM theme_mail_deliveries WHERE delivery_key=?',(k,))['status']=='uncertain'
        ''')

    def test_cloud_mail_endpoint_authentication_and_no_recipient_override(self):
        self.check('''
        from fastapi.testclient import TestClient
        from app.main import app
        from app.workshop_api import WorkshopNotify
        client=TestClient(app)
        assert client.post('/api/runner/workshop/jobs/missing/notify',json={'runner_id':'runner','claim_token':'token'}).status_code==401
        from pydantic import ValidationError
        rejects(lambda:WorkshopNotify(runner_id='runner',claim_token='token',to=['arbitrary@example.com']),errors=(ValidationError,))
        ''')

    def test_mail_queue_is_processed_on_control_plane_even_for_older_runner(self):
        self.check('''
        from app.main import app
        from app.workshop_api import claim,WorkshopClaim
        d=publish(production())
        with patch('app.services.workshop_mail.Mailer.configured',return_value=True),patch('app.services.workshop_mail.Mailer.send') as send:
            assert claim(WorkshopClaim(runner_id=settings.runner_id))=={'job':None}
            assert send.call_count==1
        mail=db.rows("SELECT status,output_json FROM theme_jobs WHERE kind='notification'")
        assert any(j['status']=='completed' and json.loads(j['output_json']).get('transport')=='server_smtp' for j in mail),mail
        fresh=create();w.submit_round(fresh['id'],owner,'design','new artwork',[],key())
        job=claim(WorkshopClaim(runner_id=settings.runner_id))['job'];assert job['kind']=='design',job
        ''')
