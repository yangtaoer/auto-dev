"""Audited metadata correction; never replay, revert, or discard delivered code."""
import json
import re

from .. import db
from ..project_catalog import matching_project_terms


def repair_app_groups() -> int:
    repaired = 0
    with db.transaction() as conn:
        catalog = [db.project_for_api(dict(row)) for row in conn.execute("SELECT * FROM projects WHERE enabled=1")]
        groups = conn.execute("SELECT * FROM request_intakes WHERE status IN ('delivered','failed') AND classification_summary<>'[]'").fetchall()
        for group in groups:
            brackets = re.findall(r"【([^】]+)】", group["title"] or "")
            if not brackets:
                continue
            try:
                matches = matching_project_terms(''.join(f'【{value}】' for value in brackets), catalog)
            except RuntimeError:
                continue
            if len(matches) != 1:
                continue
            key = next(iter(matches))
            project = next((p for p in catalog if p['project_key'] == key), {})
            if not str(project.get('name') or '').casefold().endswith('app'):
                continue
            children = [dict(row) for row in conn.execute("SELECT r.*,p.project_key FROM delivery_requests r JOIN projects p ON p.id=r.project_id WHERE joint_group_id=?", (group['id'],))]
            if len(children) < 2 or any(child['status'] not in {'delivered','failed','cancelled','rejected'} for child in children):
                continue
            canonical = next((child for child in children if child['project_key'] == key and child['status'] == 'delivered'), None)
            if not canonical or any(conn.execute("SELECT 1 FROM request_controls WHERE request_id=? AND status IN ('pending','running','waiting_merge')", (child['id'],)).fetchone() for child in children):
                continue
            now = db.utc_now()
            prior = {'intake': dict(group), 'children': [{k: child.get(k) for k in ('id','status','joint_group_id','joint_project_index','joint_project_count','policy_snapshot')} for child in children]}
            conn.execute("INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(NULL,'routing.app_scope_corrected','request_intake',?,?,?)",
                         (group['id'], json.dumps(prior, ensure_ascii=False), now))
            for child in children:
                snapshot = db.json_value(child['policy_snapshot'], {})
                for name in ('joint_classification','joint_project_keys','joint_group_id'):
                    snapshot.pop(name, None)
                conn.execute("""UPDATE delivery_requests SET joint_group_id=NULL,joint_project_index=0,joint_project_count=1,
                    routing_superseded_by=?,policy_snapshot=?,updated_at=? WHERE id=?""",
                             (None if child['id'] == canonical['id'] else canonical['id'], json.dumps(snapshot, ensure_ascii=False), now, child['id']))
                conn.execute("INSERT INTO delivery_events(request_id,level,event_type,message,created_at) VALUES(?,'info','routing.corrected',?,?)",
                             (child['id'], '已更正 APP 项目归类；地区列表不再产生 PC 联合任务。原执行与提交历史保留，不重复研发或自动回滚。', now))
            classification = [{'project_key':key,'project_name':project['name'],'source':'explicit_bracket_scope','matched_terms':matches[key]}]
            conn.execute("""UPDATE request_intakes SET status='delivered',error_message='',result_request_id=?,result_request_ids=?,
                matched_project_keys=?,classification_summary=?,updated_at=? WHERE id=?""",
                         (canonical['id'], json.dumps([canonical['id']]), json.dumps([key]), json.dumps(classification, ensure_ascii=False), now, group['id']))
            repaired += 1
    return repaired
