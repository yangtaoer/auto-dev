import copy
import json
import unittest

from app.services.deployment_links import registry,services_for_result,services_for_request,enrich_result,safe_url


def result(definition=1974,build=123):
    return {'definitionId':definition,'tfsProject':'DCS','buildId':build,'result':'succeeded','expectedArtifactFound':True,
            'artifactsUrl':f'http://dev.tellhowsoft.com/DefaultCollection/DCS/_build/results?buildId={build}&view=artifacts'}


def request(project='sichuan-dispatch-network-command',value=None):
    value=value or result()
    return {'id':'test','project_key':project,'project_name':'四川省调网络发令','work_item_id':123,'task_type':'development',
            'created_at':'2026-10-15T03:00:00+00:00','started_at':'2026-10-15T03:00:00+00:00','completed_at':'2026-10-15T03:10:00+00:00',
            'delivery_mode':'sichuan_auto_review','delivery_options':['auto_release'],'status':'delivered','title':'部署链接验证',
            'policy_snapshot':{'simulation_mode':False},'steps':[],'repository_states':[],
            'artifacts':[{'id':1,'kind':'release_artifact','name':'自动发版','external_url':value['artifactsUrl']}],
            'events':[{'event_type':'release.completed','metadata':json.dumps(value)}]}


class DeploymentLinksTests(unittest.TestCase):
    def test_exact_four_automatic_pipelines_and_manual_or_unknown_never_get_link(self):
        expected={1974:8581,8442:58389,5489:8609,7100:18080}
        self.assertEqual({p['definitionId'] for p in registry()['pipelines']},set(expected))
        for definition,port in expected.items():
            service=services_for_result(result(definition))[0]
            self.assertEqual(service['url'],f'http://192.168.62.39:{port}/netorder')
            self.assertEqual(service['readiness_status'],'not_checked');self.assertEqual(service['deployment_status'],'not_monitored')
        for definition in [1972,1956,7096,6495,7065,7945,5481,9596,9479,9480,9180,9181,9000]:
            self.assertEqual(services_for_result(result(definition)),[],definition)

    def test_scope_requires_matching_actual_build_artifact_and_project(self):
        valid=request();self.assertEqual(len(services_for_request(valid)),1)
        for mutation in [lambda d:d.update(project_key='network-command-app'),lambda d:d.update(task_type='analysis'),
                         lambda d:d.update(delivery_options=[]),lambda d:d['policy_snapshot'].update(simulation_mode=True),
                         lambda d:d.update(artifacts=[]),lambda d:d['artifacts'][0].update(external_url=result(build=999)['artifactsUrl'])]:
            d=copy.deepcopy(valid);mutation(d);self.assertEqual(services_for_request(d),[])

    def test_failed_build_wrong_collection_wrong_tfs_project_and_unknown_success_are_hidden(self):
        for patch in [{'result':'failed'},{'expectedArtifactFound':False},{'buildId':124},{'tfsProject':'XiNanArea-New'},
                      {'artifactsUrl':'https://evil.example/DCS/_build/results?buildId=123'},
                      {'artifactsUrl':'http://dev.tellhowsoft.com/DefaultCollection/DCOMS/_build/results?buildId=123'}]:
            self.assertEqual(services_for_result({**result(),**patch}),[],patch)

    def test_manual_or_incomplete_live_audit_overrides_static_registration(self):
        target={'applicationUrl':'http://192.168.62.39:8581/netorder','portVerification':'runtime-listener-verified'}
        release={'automaticTrigger':True,'releaseDefinitionId':53,'environments':[{'automaticApproval':True,'targets':[target]}]}
        audit={'status':'linked-releases-found','checkedAt':'2026-10-15T11:05:31+08:00','releases':[release]}
        self.assertEqual(len(services_for_result({**result(),'deploymentAudit':audit})),1)
        for mutate in [lambda a:a.update(status='audit-failed'),lambda a:a['releases'][0].update(automaticTrigger=False),
                       lambda a:a['releases'][0]['environments'][0].update(automaticApproval=False),
                       lambda a:a['releases'][0]['environments'][0].update(unexpandedTaskGroupIds=['unknown']),
                       lambda a:a['releases'][0]['environments'][0]['targets'][0].update(applicationUrl='javascript:alert(1)')]:
            a=copy.deepcopy(audit);mutate(a);self.assertEqual(services_for_result({**result(),'deploymentAudit':a}),[])

    def test_shared_releases_and_joint_requests_preserve_each_project_service(self):
        first=request();first['events']=[{'event_type':'release.shared','metadata':{'result':enrich_result(result())}}]
        second=request('bazhong-network-command',result(7100,124))
        self.assertEqual(len(services_for_request(first)),1)
        self.assertEqual(len(services_for_request({'joint_children':[first,second,first]})),2)
        first['events'][0]['metadata']={'result':{'artifactsUrl':result()['artifactsUrl']}}
        self.assertEqual(services_for_request(first),[])

    def test_mail_includes_clickable_service_and_warning_but_manual_and_analysis_have_no_service(self):
        from app.services.delivery import Mailer
        body=Mailer().delivery_html(request())
        self.assertIn('href="http://192.168.62.39:8581/netorder"',body)
        self.assertIn('自动部署服务入口',body);self.assertIn('本次部署进度和服务就绪未验证',body)
        yibin=Mailer().delivery_html(request('yibin-network-command',result(5489)))
        self.assertIn('未观察到应用端口监听',yibin)
        self.assertNotIn('自动部署服务入口',Mailer().delivery_html(request('chengdu-network-command',result(1972))))
        analysis=request();analysis['task_type']='analysis'
        self.assertNotIn('自动部署服务入口',Mailer().delivery_html(analysis))

    def test_malformed_metadata_and_credential_urls_do_not_break_details(self):
        for value in ['[]','bad json','null']:
            d=request();d['events'][0]['metadata']=value;self.assertEqual(services_for_request(d),[])
        for value in ['javascript:alert(1)','http://user:secret@host/path','http://host:bad/path','http://host/<script>']:
            self.assertEqual(safe_url(value),'')


if __name__=='__main__':unittest.main()
