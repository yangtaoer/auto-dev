"""Dated deployment destinations, never a claim that a Build deployed or is ready."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs,unquote,urlsplit

NOTICE='构建产物已生成；TFS 将按既有规则自动部署。本次部署进度和服务就绪未验证，请稍后访问，且需处于可访问该内网地址的网络。'


@lru_cache(maxsize=1)
def registry():
    return json.loads(Path(__file__).with_name('release-deployments.json').read_text('utf-8'))


def _object(value):
    if isinstance(value,dict):return value
    try:
        parsed=json.loads(value) if isinstance(value,str) else None
        return parsed if isinstance(parsed,dict) else {}
    except (TypeError,ValueError):return {}


def safe_url(value):
    if not isinstance(value,str) or len(value)>2000 or re.search(r'[\x00-\x20<>"\x27\\]',value):return ''
    try:
        parsed=urlsplit(value)
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:return ''
        parsed.port
        return value
    except ValueError:return ''


def _build_identity(value):
    if not safe_url(value):return None
    parsed=urlsplit(value);collection=urlsplit(registry()['collectionUrl'])
    prefix=collection.path.rstrip('/')+'/'
    path=unquote(parsed.path)
    if parsed.netloc.casefold()!=collection.netloc.casefold() or not path.casefold().startswith(prefix.casefold()):return None
    relative=path[len(prefix):]
    if not relative.casefold().endswith('/_build/results'):return None
    project=relative.split('/',1)[0]
    try:
        values=parse_qs(parsed.query)['buildId']
        if len(values)!=1 or int(values[0])<=0:return None
        return project.casefold(),int(values[0])
    except (KeyError,ValueError,TypeError):return None


def services_for_result(result):
    """Use exact definition + TFS collection/project, and only successful delivery builds."""
    if not isinstance(result,dict) or result.get('result')!='succeeded' or result.get('expectedArtifactFound') is not True:return []
    identity=_build_identity(result.get('artifactsUrl',''))
    if not identity:return []
    try:definition_id=int(result.get('definitionId',0));build_id=int(result.get('buildId',0))
    except (ValueError,TypeError):return []
    if build_id!=identity[1]:return []
    definition=next((p for p in registry()['pipelines'] if p['definitionId']==definition_id and p['tfsProject'].casefold()==identity[0]),None)
    if not definition or str(result.get('tfsProject') or identity[0]).casefold()!=identity[0]:return []
    candidates=[];checked=registry()['checkedAt']
    audit=result.get('deploymentAudit')
    if isinstance(audit,dict):
        # Fresh explicit negative/manual/failed audit overrides the dated fallback.
        if audit.get('status')!='linked-releases-found':return []
        checked=audit.get('checkedAt') or checked
        for release in audit.get('releases') or []:
            if not isinstance(release,dict):continue
            if release.get('automaticTrigger') is not True:continue
            for env in release.get('environments') or []:
                if not isinstance(env,dict) or env.get('automaticApproval') is not True or env.get('unexpandedTaskGroupIds'):continue
                for target in env.get('targets') or []:
                    if not isinstance(target,dict):continue
                    candidates.append((target,release.get('releaseDefinitionId'),release.get('definitionUrl','')))
    else:
        candidates=[(definition,definition['releaseDefinitionId'],registry()['collectionUrl']+'/'+definition['tfsProject']+'/_release?definitionId='+str(definition['releaseDefinitionId'])+'&view=mine&_a=releases')]
    links=[]
    for target,release_id,release_url in candidates:
        url=safe_url(target.get('applicationUrl'))
        if not url:continue
        verification=target.get('portVerification','not-verified')
        links.append({'project_key':definition['projectKey'],'project_name':definition['standardName'],'url':url,
            'build_id':build_id,'definition_id':definition_id,'release_definition_id':release_id,'release_url':safe_url(release_url),
            'audit_checked_at':checked,'port_verification':verification,'deployment_status':'not_monitored',
            'readiness_status':'not_checked','notice':NOTICE,
            'warning':'登记时仅核对到配置，未观察到应用端口监听。' if verification=='configuration-only-not-listening' else ''})
    return list({(item['url'],item['release_definition_id']):item for item in links}.values())


def enrich_result(result):
    return {**result,'autoDeploymentServices':services_for_result(result),'deploymentMonitoring':'not-included-in-build-monitoring'}


def services_for_request(detail):
    if detail.get('task_type')=='analysis':return []
    if detail.get('joint_children'):
        return list({(s['project_key'],s['url'],s['build_id']):s for child in detail['joint_children']
            for s in (child.get('deployment_services') if isinstance(child.get('deployment_services'),list) else services_for_request(child))}.values())
    policy=_object(detail.get('policy_snapshot'))
    if policy.get('simulation_mode'):return []
    options=detail.get('delivery_options')
    if options is not None and 'auto_release' not in options:return []
    evidence={_build_identity(a.get('external_url','')) for a in detail.get('artifacts',[]) if a.get('kind')=='release_artifact'}-{None}
    if not evidence:return []
    links=[]
    for event in detail.get('events',[]):
        if event.get('event_type') not in {'release.completed','release.shared'}:continue
        result=_object(event.get('metadata'))
        if event.get('event_type')=='release.shared':result=_object(result.get('result'))
        identity=_build_identity(result.get('artifactsUrl',''))
        if identity not in evidence:continue
        for service in services_for_result(result):
            definition=next(p for p in registry()['pipelines'] if p['definitionId']==service['definition_id'])
            key=detail.get('project_key') or policy.get('project_key')
            if key:
                if key!=definition['projectKey']:continue
            elif (detail.get('project_name') or policy.get('name')) not in [definition['standardName'],*definition.get('projectNames',[])]:continue
            links.append(service)
    return list({(item['url'],item['build_id']):item for item in links}.values())
