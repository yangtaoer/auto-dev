from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class RecoveryRecordTests(unittest.TestCase):
    def setUp(self):
        from app import db
        from app.config import settings
        self.db=db
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        values={key:getattr(settings,key) for key in ('data_dir','seed_demo','environment','worker_enabled','runner_token')}
        for key,value in dict(data_dir=Path(self.temp.name),seed_demo=True,environment='development',worker_enabled=False,runner_token='recovery-token').items():
            object.__setattr__(settings,key,value)
        self.addCleanup(lambda:[object.__setattr__(settings,k,v) for k,v in values.items()])
        db.init_db()
        self.project=db.row('SELECT * FROM projects LIMIT 1')
        self.admin=db.row("SELECT * FROM users WHERE role='admin' LIMIT 1")
        self.sid='new-session'
        self.runner='recovery-runner'
        self.sequence=0
        self.heartbeat()

    def execute(self,sql,parameters):
        with self.db.transaction() as connection:
            connection.execute(sql,parameters)

    def heartbeat(self, ids=None, session=None, state='idle'):
        self.execute('INSERT OR REPLACE INTO runners(runner_id,hostname,version,state,current_request_id,last_seen_at,detail) VALUES(?,?,?,?,?,?,?)',
            (self.runner,'test','18',state,None,self.db.utc_now(),json.dumps({'execution_session_id':session or self.sid,'current_request_ids':ids or []})))

    def request(self,status='submitting',old=True):
        self.sequence+=1
        rid=self.db.create_delivery_request(self.project,self.admin['id'],200000+self.sequence,'sichuan_auto_review',[],[])
        self.db.update_request(rid,runner_id=self.runner,status=status,current_step='submit',result_summary='实现已完成',
            repository_states=[{'name':'regional','worktree_path':'C:/test/repo','repository_path':'C:/repo',
                'changed_files':['src/Feature.java'],'commit_hash':'a'*40,'branch':'feature/test','base_branch':'dev','status':'pushed'}])
        if old:
            self.execute('UPDATE delivery_requests SET updated_at=? WHERE id=?',((datetime.now(UTC)-timedelta(minutes=10)).isoformat(),rid))
        return rid

    def recover(self,session=None):
        from app.services.execution_recovery import recover
        return recover(self.runner,session or self.sid)

    def test_old_submission_is_queued_once_without_development_or_code_writes(self):
        rid=self.request()
        result=self.recover()
        self.assertEqual(result['recovered'],[rid])
        detail=self.db.request_detail(rid)
        self.assertEqual(detail['status'],'waiting_retry')
        self.assertEqual(detail['recovery_context']['kind'],'submission')
        self.assertIsNone(detail['completed_at'])
        self.assertEqual(self.recover(),{'recovered':[],'interrupted':[]})
        self.assertEqual(self.db.row("SELECT count(*) n FROM delivery_events WHERE request_id=? AND event_type='execution.interrupted'",(rid,))['n'],1)

    def test_a_long_running_request_in_the_live_heartbeat_is_not_orphaned(self):
        rid=self.request()
        self.heartbeat([rid],state='working')
        self.assertFalse(self.recover()['recovered'])
        self.assertEqual(self.db.request_detail(rid)['status'],'submitting')

    def test_recent_claim_between_heartbeats_is_not_orphaned(self):
        rid=self.request(old=False)
        self.assertFalse(self.recover()['recovered'])
        self.assertEqual(self.db.request_detail(rid)['status'],'submitting')

    def test_completed_failed_cancelled_and_other_runner_requests_are_untouched(self):
        for status in ('delivered','failed','cancelled','waiting_merge'):
            rid=self.request(status)
            self.recover()
            self.assertEqual(self.db.request_detail(rid)['status'],status)
        rid=self.request()
        self.db.update_request(rid,runner_id='another-runner')
        self.assertFalse(self.recover()['recovered'])

    def test_unknown_side_effect_stages_are_not_blindly_replayed(self):
        for status in ('developing','building','releasing','delivering'):
            rid=self.request(status)
            self.assertEqual(self.recover()['interrupted'],[rid])
            detail=self.db.request_detail(rid)
            self.assertEqual(detail['status'],'failed')
            self.assertIn('不自动重复研发或发版',detail['error_message'])
            self.assertEqual(detail['recovery_context']['previous_status'],status)

    def test_incomplete_commit_checkpoint_and_local_package_do_not_auto_resume(self):
        rid=self.request()
        self.execute('UPDATE delivery_requests SET repository_states=? WHERE id=?',(json.dumps([{'status':'prepared','changed_files':['Feature.java']}]),rid))
        self.assertEqual(self.recover()['interrupted'],[rid])
        rid=self.request()
        self.execute("UPDATE delivery_requests SET delivery_mode='local_package' WHERE id=?",(rid,))
        self.assertEqual(self.recover()['interrupted'],[rid])

    def test_pending_user_control_has_priority_over_automatic_recovery(self):
        rid=self.request()
        now=self.db.utc_now()
        self.execute('INSERT INTO request_controls(id,request_id,action,status,actor_id,runner_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                        ('cancel-control',rid,'cancel','pending',self.admin['id'],self.runner,now,now))
        self.assertFalse(self.recover()['recovered'])
        self.assertEqual(self.db.request_detail(rid)['status'],'submitting')

    def test_fresh_registered_session_is_required(self):
        from app.services.execution_recovery import recover
        self.request()
        for sid in ('wrong-session',''):
            with self.assertRaises(ValueError):recover(self.runner,sid)
        self.execute('UPDATE runners SET last_seen_at=? WHERE runner_id=?',((datetime.now(UTC)-timedelta(minutes=5)).isoformat(),self.runner))
        with self.assertRaises(ValueError):self.recover()

    def test_recovery_context_survives_repeat_schema_initialization(self):
        rid=self.request()
        self.recover()
        before=self.db.request_detail(rid)['recovery_context']
        self.db.init_db()
        self.assertEqual(self.db.request_detail(rid)['recovery_context'],before)

    def test_session_takeover_refuses_a_live_job_including_image_jobs(self):
        from app.services.execution_recovery import session_conflict
        for ids in (['job'],[]):
            record={'state':'working','last_seen_at':self.db.utc_now(),'detail':json.dumps({'execution_session_id':'old','current_request_ids':ids})}
            self.assertTrue(session_conflict(record,'new'))
            self.assertTrue(session_conflict(record,''))
            self.assertFalse(session_conflict(record,'old'))
            record['last_seen_at']=(datetime.now(UTC)-timedelta(minutes=3)).isoformat()
            self.assertFalse(session_conflict(record,'new'))

    def test_api_rejects_old_session_and_recovery_uses_server_heartbeat_ids(self):
        from fastapi.testclient import TestClient
        from app.main import app
        rid=self.request()
        self.heartbeat([rid],state='working')
        auth={'Authorization':'Bearer recovery-token'}
        with TestClient(app) as client:
            response=client.post('/api/runner/heartbeat',headers=auth,json={'runner_id':self.runner,'execution_session_id':'old','state':'working','current_request_ids':[rid]})
            self.assertEqual(response.status_code,409)
            self.heartbeat([rid],state='working')
            response=client.post('/api/runner/recover-interrupted',headers=auth,json={'runner_id':self.runner,'execution_session_id':self.sid,'current_request_ids':[]})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['recovered'],[])
            old={**auth,'X-AutoDev-Runner-Id':self.runner,'X-AutoDev-Execution-Session':'old'}
            response=client.post('/api/runner/claim',headers=old,json={'runner_id':self.runner})
            self.assertEqual(response.status_code,409)
            self.assertEqual(self.db.request_detail(rid)['status'],'submitting')

    def test_offline_task_displays_loss_of_heartbeat_without_mutating_status(self):
        from app.main import add_runner_display_state
        with patch('app.main.runner_is_online',return_value=False):
            detail=add_runner_display_state({'status':'submitting','runner_id':self.runner})
        self.assertEqual(detail['status'],'submitting')
        self.assertIn('不代表任务仍在运行',detail['current_activity'])

    def test_slow_usage_lookup_is_not_on_the_liveness_thread(self):
        from app import local_runner_main
        source=inspect.getsource(local_runner_main.main)
        heartbeat=source.split('def heartbeat_loop()',1)[1].split('def usage_loop()',1)[0]
        self.assertNotIn('refresh_codex_usage(',heartbeat)
        startup=source.split('    try:\n        monitor.start()',1)[1]
        self.assertNotIn('refresh_codex_usage(',startup)


class SubmissionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.states=[]
        for name,letter in (('region-ui','a'),('region-service','b')):
            path=self.root/name;path.mkdir()
            self.states.append({'name':name,'worktree_path':str(path),'repository_path':str(path),
                'base_commit':'c'*40,'commit_hash':letter*40,'branch':'feature/7-test','base_branch':'dev',
                'status':'pushed','changed_files':['src/Feature.java']})
        self.detail={'id':'job','status':'waiting_retry','work_item_id':7,'title':'Regional fix','requirement_summary':'frozen requirement',
            'result_summary':'implemented','delivery_mode':'sichuan_auto_review','repository_states':self.states,
            'policy_snapshot':{'project_key':'regional','tfs_collection_url':'https://tfs.invalid','verification_command':'verify-only'},
            'quality_gate_result':{'delivery_assets':[]},'recovery_context':{'kind':'submission','state':'pending',
                'verified_commits':{s['name']:s['commit_hash'] for s in self.states}}}
        store=Mock(remote=True)
        store.detail.return_value=self.detail
        store.update_request.side_effect=lambda _id,**fields:self.detail.update(fields)
        self.worker=Mock(store=store)
        self.worker._plain_text.side_effect=lambda text:text
        self.worker._build_environment.return_value={}
        self.tfs=Mock()
        self.tfs.get_work_item.return_value={'title':'Regional fix','description':'frozen requirement','state':'新建'}
        self.tfs.parse_origin.return_value=('DCS','regional')
        self.tfs.pull_requests_for_branch.return_value=[]
        self.tfs.branch_head.side_effect=lambda path,_branch:next(s['commit_hash'] for s in self.states if s['worktree_path']==path)
        self.tfs.create_pull_request.side_effect=lambda path,*_args:{'PullRequestId':101 if path.endswith('region-ui') else 102,'WebUrl':'https://tfs.invalid/pr'}
        self.tfs.repository_info.return_value=SimpleNamespace(project='DCS',name='regional')
        def git(path,*args):
            state=next(s for s in self.states if s['worktree_path']==str(path))
            if args==('rev-parse','HEAD'):return state['commit_hash']
            if args==('rev-parse','--abbrev-ref','HEAD'):return state['branch']
            if args[:1]==('diff',):return ''
            if args[:1]==('log',):return 'fix(#7):regional workflow'
            self.fail(f'Unexpected potentially mutating git call: {args}')
        self.git_patch=patch('app.services.submission_recovery.git',side_effect=git)
        self.git_mock=self.git_patch.start();self.addCleanup(self.git_patch.stop)
        p=patch('app.services.submission_recovery.TfsClient',return_value=self.tfs);p.start();self.addCleanup(p.stop)
        p=patch('app.services.submission_recovery.run_command');self.build=p.start();self.addCleanup(p.stop)

    def resume(self):
        from app.services.submission_recovery import resume
        resume(self.worker,'job')

    def test_pushed_commits_resume_without_model_commit_push_or_rebuild(self):
        with patch('app.services.codex_runner.CodexRunner.run') as model:
            self.resume()
        self.assertEqual(self.detail['status'],'waiting_merge')
        self.assertEqual(self.tfs.create_pull_request.call_count,2)
        self.assertEqual(self.tfs.approve_pull_request.call_count,2)
        self.assertEqual(self.detail['recovery_context']['state'],'resumed')
        self.build.assert_not_called()
        model.assert_not_called()

    def test_legacy_checkpoint_is_revalidated_once_without_new_development(self):
        self.detail['recovery_context'].pop('verified_commits')
        self.resume()
        self.build.assert_called_once()
        self.assertEqual(self.detail['status'],'waiting_merge')

    def test_all_repositories_are_checked_before_any_pr_write(self):
        self.tfs.branch_head.side_effect=lambda path,_branch:'d'*40 if path.endswith('region-service') else 'a'*40
        with self.assertRaisesRegex(RuntimeError,'新增了其他提交'):
            self.resume()
        self.tfs.create_pull_request.assert_not_called()
        self.build.assert_not_called()

    def test_changed_requirement_stops_before_pr_and_build(self):
        self.tfs.get_work_item.return_value['description']='new business scope'
        with self.assertRaisesRegex(RuntimeError,'正文已变化'):
            self.resume()
        self.tfs.create_pull_request.assert_not_called()
        self.build.assert_not_called()

    def test_completed_pr_with_deleted_source_branch_is_reused_not_reapproved(self):
        self.tfs.branch_head.side_effect=lambda *_args:None
        self.tfs.pull_requests_for_branch.side_effect=lambda path,*_args:[{'pullRequestId':101 if path.endswith('region-ui') else 102,
            'status':'completed','lastMergeSourceCommit':{'commitId':'a'*40 if path.endswith('region-ui') else 'b'*40}}]
        self.resume()
        self.tfs.create_pull_request.assert_not_called()
        self.tfs.approve_pull_request.assert_not_called()
        self.assertEqual(self.detail['status'],'waiting_merge')

    def test_abandoned_pr_is_not_reopened(self):
        self.tfs.pull_requests_for_branch.return_value=[{'pullRequestId':99,'status':'abandoned','lastMergeSourceCommit':{'commitId':'a'*40}}]
        with self.assertRaisesRegex(RuntimeError,'已被放弃'):
            self.resume()
        self.tfs.create_pull_request.assert_not_called()

    def test_multiple_active_prs_are_not_guessed(self):
        self.tfs.pull_requests_for_branch.return_value=[{'pullRequestId':1,'status':'active'},{'pullRequestId':2,'status':'active'}]
        with self.assertRaisesRegex(RuntimeError,'多个'):
            self.resume()
        self.tfs.create_pull_request.assert_not_called()

    def test_tracked_worktree_changes_stop_without_overwrite(self):
        original=self.git_mock.side_effect
        self.git_mock.side_effect=lambda root,*args:'src/changed.java' if args==('diff','HEAD','--name-only') else original(root,*args)
        with self.assertRaisesRegex(RuntimeError,'已跟踪文件修改'):
            self.resume()
        self.tfs.create_pull_request.assert_not_called()

    def test_saved_pr_must_match_remote_checkpoint(self):
        self.states[0]['pr_id']=22
        with self.assertRaisesRegex(RuntimeError,'检查点不一致'):
            self.resume()
        self.tfs.create_pull_request.assert_not_called()

    def test_worker_dispatches_recovery_instead_of_running_development(self):
        from app.orchestrator import Worker
        with patch('app.services.submission_recovery.resume') as resumed:
            Worker.poll_merge(self.worker,'job')
        resumed.assert_called_once_with(self.worker,'job')
        self.worker.run_request.assert_not_called()


if __name__=='__main__':unittest.main()
