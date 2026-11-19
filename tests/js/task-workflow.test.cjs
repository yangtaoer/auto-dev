const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const workflow = require('../../app/static/task-workflow.js');

const review = {status:'waiting_merge', current_step:'submit', delivery_mode:'product_manual_review',
  delivery_options:['auto_release'], steps:[{step_code:'validate',status:'completed'},{step_code:'submit',status:'completed'}]};
const phase = (record, id) => workflow.stages(record).find(node => node.id === id);

test('review keeps submit and merge separate and requires receipts for every PR', () => {
  const record = {...review, repository_states:[
    {name:'web',pr_id:11,merge_commit:'abc',changed_file_count:2},
    {name:'api',pr_id:12,changed_files:['api.java']},
  ], task_trace:{milestones:{'pr.merged':{message:'PR #11 已合并'}}}};
  const nodes = workflow.stages(record);
  assert.deepEqual(nodes.map(node=>node.id), ['intake','requirement','development','submit','merge','release','delivery','acceptance']);
  assert.equal(phase(record,'submit').state,'done');
  assert.equal(phase(record,'merge').state,'waiting');
  assert.deepEqual(workflow.counts(record),{repos:2,files:3});
});

test('a completed build paired with a deployment failure stays a deployment failure', () => {
  const record = {...review,status:'failed',current_step:'release',task_trace:{release:{
    build_id:1244194,result:'succeeded',deployment_status:'failed-rolled-back',
  }}};
  assert.equal(phase(record,'release').state,'done');
  assert.equal(phase(record,'deploy').state,'failed');
  assert.equal(workflow.currentStage(record),'deploy');
  assert.equal(phase(record,'delivery').state,'pending');
});

test('delivery does not infer readiness or acceptance for old records', () => {
  const record = {...review,status:'delivered',steps:[],
    deployment_services:[{url:'http://service.invalid/',readiness_status:'unverified'}]};
  assert.equal(phase(record,'delivery').state,'done');
  assert.equal(phase(record,'development').state,'unknown');
  assert.equal(phase(record,'deploy').state,'waiting');
  assert.equal(phase(record,'acceptance').state,'waiting');
  assert.equal(phase({...record,acceptance_progress:{status:'accepted'}},'acceptance').state,'done');
});

test('analysis omits the code delivery stages, and routing only exposes intake', () => {
  assert.deepEqual(workflow.stages({...review,task_type:'analysis'}).map(node=>node.id),
    ['intake','requirement','development','delivery','acceptance']);
  const intake = {record_type:'intake',status:'waiting_runner',runner_online:false};
  assert.deepEqual(workflow.stages(intake).map(node=>[node.id,node.state]),[['intake','waiting']]);
});

test('rollback is a header action for eligible tasks and keeps the existing permission and busy checks', () => {
  const context = {window:{}, escapeHtml:String, isAnalysisTask:detail=>detail.task_type==='analysis'};
  vm.runInNewContext(fs.readFileSync('app/static/task-actions.js','utf8'),context);
  const actions = context.window.TaskActions;
  const delivered = {id:'task', status:'delivered', repository_states:[{changed_files:['a.js'],commit_hash:'123456'}]};
  assert.match(actions.render(delivered).rollback,/data-task-action="rollback"/);
  assert.equal(actions.render(delivered).menu,'');
  assert.equal(actions.render({...delivered,can_manage_request:false}).rollback,'');
  assert.equal(actions.render({...delivered,task_type:'analysis'}).rollback,'');
  assert.equal(actions.render({...delivered,repository_states:[]}).rollback,'');
  const running = actions.render({...delivered,status:'developing'});
  assert.equal(running.rollback,'');
  assert.match(running.menu,/data-task-action="cancel".*data-task-action="restart"/);
  assert.match(actions.render({...delivered,controls:[{action:'restart',status:'pending'}]}).rollback,/disabled/);
  assert.equal(actions.render({...delivered,controls:[{action:'rollback',status:'completed'}]}).rollback,'');
});
