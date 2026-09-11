"""Isolated task modal / acceptance browser regression. No production requests."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
temp = tempfile.TemporaryDirectory(prefix='autodev-task-tabs-')
os.environ.update(AUTODEV_DATA_DIR=temp.name, AUTODEV_WORKER_ENABLED='0', AUTODEV_SEED_DEMO='1',
                  BOOTSTRAP_ADMIN_PASSWORD='local-ui-check-password', BOOTSTRAP_PM_PASSWORD='local-ui-pm-password',
                  AUTODEV_RUNNER_TOKEN='ui-test-token')
from app import db
from app.services import project_learning as learning
from playwright.sync_api import sync_playwright
import httpx
db.init_db()
admin = db.row("SELECT * FROM users WHERE username='admin'")
project = db.row('SELECT * FROM projects LIMIT 1')
request_id = db.create_delivery_request(project, admin['id'], 881038, 'local_package', ['test@example.com'])
description = '1、配网调控班待办提醒按当前角色展示；2、点击弹窗后进入对应票据；3、保持其他地区的消息提醒逻辑不变'
db.update_request(request_id, title='【成都网络发令】配网调控班消息提醒与弹窗交互优化', requirement_summary=description,
                  status='delivered', completed_at=db.utc_now(), result_summary='完成当前班组待办筛选与弹窗交互；保留其他地区的既有逻辑。', commit_hash='ui-build-38')
learning.ensure_acceptance(request_id, description, revision=7, source=learning.DESCRIPTION_SOURCE)
for code in ['validate','prepare','develop','clarify','submit','release','deliver']:
    db.update_step(request_id, code, 'completed', '已完成对应步骤与检查')
for index in range(25):
    db.add_event(request_id, 'development.check', f'第 {index + 1} 条执行记录：完成对应功能的构建与逻辑检查。')
db.add_artifact(request_id, 'frontend_package', 'chengdu-web-v38.zip', external_url='https://example.invalid/web.zip')
db.add_artifact(request_id, 'backend_package', 'chengdu-server-v38.zip', external_url='https://example.invalid/server.zip')
db.add_artifact(request_id, 'delivery_manifest', 'delivery-validation-manifest.json', external_url='https://example.invalid/manifest.json')
blocked_id = db.create_delivery_request(project, admin['id'], 881039, project['delivery_mode'], [])
db.update_request(blocked_id, title='【成都网络发令】复杂逻辑冲突确认', requirement_summary='1、修改当前调控班待办。', status='waiting_approval', error_message='重大代码逻辑冲突，需要明确业务取舍')
restart_id = db.create_delivery_request(project, admin['id'], 881040, project['delivery_mode'], [])
db.update_request(restart_id, title='重新开始测试', status='developing')
analysis_id = db.create_delivery_request(project, admin['id'], 881041, 'local_package', [], [], task_type='analysis')
db.update_request(analysis_id, title='分析已完成但 TFS 同步失败', status='failed', current_step='deliver',
                  analysis_result={'decision':'completed','summary':'数据关联缺失，分析报告已生成'})
db.add_artifact(analysis_id, 'analysis_report', 'TFS-881041-问题分析报告.md', external_url='https://example.invalid/report.md')
process = subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','28769'],cwd=root,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
try:
    for _ in range(80):
        try:
            if httpx.get('http://127.0.0.1:28769/healthz').status_code==200: break
        except httpx.HTTPError: pass
        time.sleep(.2)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge',headless=True)
        page = browser.new_page(viewport={'width':1440,'height':950})
        errors = []
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:28769/')
        page.locator('input[name=username]').fill('admin')
        page.locator('input[name=password]').fill('local-ui-check-password')
        page.locator('#login-form button').click()
        page.wait_for_url('http://127.0.0.1:28769/')
        page.evaluate('(id)=>openDetail(id)',request_id)
        page.locator('#task-panel-overview .artifact').first.wait_for(timeout=8000)
        assert page.locator('[role=tab]').all_text_contents() == ['01交付概览','02需求拆解','03研发过程','04合并发版','05验收反馈']
        assert page.locator('#task-panel-overview .artifact').count() == 2
        assert page.locator('#task-panel-overview').inner_text().find('交付产物') < page.locator('#task-panel-overview').inner_text().find('当前进展')
        assert page.evaluate("getComputedStyle(document.body).overflow==='hidden'")
        page.wait_for_function("document.querySelector('#detail-drawer').contains(document.activeElement)",timeout=2000)
        page.screenshot(path=str(root/'data/alpha38-task-overview.png'))
        page.get_by_role('tab',name='验收反馈').click()
        page.get_by_role('button',name='验证不通过',exact=True).wait_for()
        assert page.locator('[name=tested_version]').count()==0
        page.get_by_role('button',name='验证不通过',exact=True).click()
        page.locator('.acceptance-optional summary').click()
        page.locator('.acceptance-failed-points input').nth(1).check()
        page.locator('[name=raw_feedback]').fill('第二项弹窗跳转尚未生效，其他尚未逐项复核。')
        page.evaluate('(id)=>refreshDetail(id,true)',request_id)
        assert page.locator('#task-tab-acceptance').get_attribute('aria-selected')=='true'
        assert page.locator('[name=raw_feedback]').input_value().startswith('第二项弹窗')
        assert page.locator('.acceptance-failed-points input').nth(1).is_checked()
        page.get_by_role('tab',name='需求拆解').click()
        assert page.locator('.task-points li').count()==3
        page.get_by_role('tab',name='验收反馈').click()
        assert page.locator('[name=raw_feedback]').input_value().startswith('第二项弹窗')
        page.screenshot(path=str(root/'data/alpha38-task-acceptance.png'))
        page.get_by_role('tab',name='研发过程').click()
        page.locator('#task-panel-development').evaluate('(node)=>node.scrollTop=200')
        page.evaluate('(id)=>refreshDetail(id,true)',request_id)
        assert page.locator('#task-panel-development').evaluate('(node)=>node.scrollTop') == 200
        page.get_by_role('tab',name='验收反馈').click()
        page.locator('.acceptance-submit').click()
        page.wait_for_function("document.querySelector('.acceptance-heading h3')?.textContent==='需要返修'")
        result=page.request.get(f'http://127.0.0.1:28769/api/requests/{request_id}/acceptance').json()['acceptance']
        assert [item['human_status'] for item in result['items']] == ['unverified','failed','unverified']
        page.get_by_role('button',name='验证不通过',exact=True).click()
        page.locator('.acceptance-submit').click()
        page.wait_for_function("document.querySelector('.acceptance-history > summary')?.textContent.includes('2 次')")
        result=page.request.get(f'http://127.0.0.1:28769/api/requests/{request_id}/acceptance').json()['acceptance']
        assert result['status']=='changes_requested'
        assert all(item['human_status']=='unverified' for item in result['items'])
        page.get_by_role('button',name='验证通过',exact=True).click()
        page.locator('.acceptance-submit').click()
        page.wait_for_function("document.querySelector('.acceptance-heading h3')?.textContent==='验收通过'")
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'mobile page overflow'
        assert page.evaluate("document.querySelector('.task-tab-panel:not([hidden])').scrollWidth<=document.querySelector('.task-tab-panel:not([hidden])').clientWidth"),'mobile modal overflow'
        page.screenshot(path=str(root/'data/alpha38-task-mobile.png'))
        page.locator('#task-tab-acceptance').focus()
        page.keyboard.press('Home')
        assert page.locator('#task-tab-overview').get_attribute('aria-selected')=='true'
        page.locator('#close-detail').focus()
        page.keyboard.press('Shift+Tab')
        assert page.evaluate("document.querySelector('#detail-drawer').contains(document.activeElement)")
        page.keyboard.press('Escape')
        assert page.locator('#detail-drawer').get_attribute('aria-hidden')=='true'
        assert not page.evaluate("document.querySelector('.app-shell').inert")
        page.set_viewport_size({'width':1440,'height':950})
        page.evaluate('(id)=>openDetail(id)',blocked_id)
        page.locator('#continuation-prompt').fill('保持当前工作区，先对照已有实现确认冲突范围。')
        page.evaluate('(id)=>refreshDetail(id,true)',blocked_id)
        assert page.locator('#continuation-prompt').input_value().startswith('保持当前工作区')
        page.get_by_role('tab',name='研发过程').click()
        page.evaluate('(id)=>refreshDetail(id,true)',blocked_id)
        assert page.locator('#task-tab-development').get_attribute('aria-selected')=='true'
        page.get_by_role('tab',name='交付概览').click()
        assert page.locator('#continuation-prompt').input_value().startswith('保持当前工作区')
        page.locator('[data-task-action=cancel]').click()
        page.locator('#task-action-confirm[open]').wait_for()
        page.keyboard.press('Escape')
        assert page.locator('#detail-drawer').get_attribute('aria-hidden')=='false'
        page.locator('[data-task-action=cancel]').click()
        page.locator('#confirm-task-action').click()
        page.locator('[data-control-status=pending]').wait_for()
        assert db.request_detail(blocked_id)['status']=='cancelled'
        page.evaluate('(id)=>openDetail(id)',restart_id)
        page.locator('[data-task-action=restart]').click()
        page.locator('#confirm-task-action').click()
        page.locator('[data-control-status=pending]').wait_for()
        assert db.request_detail(restart_id)['controls'][0]['action']=='restart'
        db.update_request(request_id, repository_states=[{'changed_files':['src/change.js'],'commit_hash':'a'*40}])
        page.evaluate('(id)=>openDetail(id)',request_id)
        page.locator('[data-task-action=rollback]').click()
        page.locator('#task-action-confirm[open]').wait_for()
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate("document.querySelector('#task-action-confirm').scrollWidth<=document.querySelector('#task-action-confirm').clientWidth")
        page.locator('#confirm-task-action').click()
        page.locator('[data-control-status=pending]').wait_for()
        assert db.request_detail(request_id)['controls'][0]['action']=='rollback'
        page.evaluate('(id)=>openDetail(id)',analysis_id)
        page.locator('#retry-run').wait_for()
        assert page.locator('#retry-run').inner_text() == '重试报告同步 ↻'
        assert page.locator('#task-panel-overview .analysis-report-artifact').count() == 1
        page.once('dialog',lambda d:d.accept())
        page.locator('#retry-run').click()
        page.wait_for_function("document.querySelector('.detail-head')?.textContent.includes('分析完成，待同步交付')")
        assert db.request_detail(analysis_id)['status']=='waiting_analysis_sync'
        assert len(db.request_detail(analysis_id)['artifacts'])==1
        assert page.locator('#task-panel-overview .analysis-report-artifact').count()==1
        assert not errors,errors
        print('TASK_UI_OK: ordered tabs, products first, pass/fail, polling drafts, mobile width, keyboard, cancellation, restart, rollback confirmation; zero JS errors')
        browser.close()
finally:
    process.terminate()
    process.wait(timeout=15)
    temp.cleanup()
