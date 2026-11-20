"""Linux-side bounded Compose switch. Input/output are credential-free JSON."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

ROOT = Path('/home/czp/netorder')
HOST = '192.168.101.14'
JAVA_IMAGE = 'openjdk:8@sha256:86e863cc57215cfb181bd319736d0baf625fe8f150577f9eb58bd937f5452cb8'
ZK_IMAGE = 'zookeeper:3.9.3@sha256:a8fecbc6f2a66749d02bca621708bbd811ba077406432662d2371f66da536919'
COMPOSE_NAMES = ['compose.yaml', 'compose.yml', 'docker-compose.yaml', 'docker-compose.yml']
ENTRYPOINT = r'''#!/bin/sh
set -eu
container_ip=$(hostname -i | awk '{print $1}')
export DUBBO_IP_TO_BIND="$container_ip" DUBBO_IP_TO_REGISTRY="$container_ip"
case "$1" in
auth)
 export DUBBO_PORT_TO_BIND=20880 DUBBO_PORT_TO_REGISTRY=20880
 exec java -Xms256m -Xmx1024m -Dfile.encoding=UTF-8 -Duser.timezone=Asia/Shanghai -jar /app/auth.jar --spring.config.location=file:/app/config/application.properties
 ;;
netorder)
 export DUBBO_PORT_TO_BIND=20881 DUBBO_PORT_TO_REGISTRY=20881
 exec java -Xms512m -Xmx3072m -Dfile.encoding=UTF-8 -Duser.timezone=Asia/Shanghai "-Djasypt.encryptor.password=${JASYPT_ENCRYPTOR_PASSWORD:?Missing retained Jasypt setting}" -Djasypt.encryptor.algorithm=PBEWithMD5AndDES -Djasypt.encryptor.iv-generator-classname=org.jasypt.iv.NoIvGenerator -javaagent:/app/release/sjt_agent.jar -jar "/app/release/${APP_JAR:?Missing versioned JAR}" "--spring.profiles.active=${SPRING_PROFILE:?Missing region profile}" --spring.config.additional-location=file:/app/config/
 ;;
*) exit 2;;
esac
'''


def progress(message):
    try:
        print(json.dumps({'phase': message}, ensure_ascii=False), file=sys.stderr, flush=True)
    except (BrokenPipeError, OSError):
        pass  # Complete/roll back under the project lock even if the SSH observer disconnects.


def command(args, *, cwd=None, timeout=60):
    result = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=timeout)
    if result.returncode:
        # Config/Java command output can contain secrets; retain only the step identity.
        raise RuntimeError('Command failed (' + str(result.returncode) + '): ' + ' '.join(args[:3]))
    return result.stdout


def docker_json(args):
    return json.loads(command(['docker'] + args))


def inspect(name):
    result = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def snapshot(exclude=()):
    ids = command(['docker', 'ps', '-aq']).split()
    items = docker_json(['inspect'] + ids) if ids else []
    return {i['Name'].lstrip('/'): dict(id=i['Id'], startedAt=i['State']['StartedAt'],
                status=i['State']['Status'], pid=i['State']['Pid']) for i in items
            if i['Name'].lstrip('/') not in exclude}


def text(path):
    raw = Path(path).read_bytes()
    for encoding in ['utf-8-sig', 'gb18030']:
        try:
            return raw.decode(encoding)
        except UnicodeError:
            pass
    raise RuntimeError('Cannot decode regional configuration: ' + str(path))


def properties(raw):
    return {m.group(1): m.group(2).strip() for line in raw.splitlines()
            if (m := re.match(r'^\s*([^#!\s=:]+)\s*[:=]\s*(.*)$', line))}


def update_properties(raw, values, remove=()):
    remaining, output = dict(values), []
    for line in raw.splitlines():
        match = re.match(r'^\s*([^#!\s=:]+)\s*[:=]', line)
        key = match.group(1) if match else None
        if key in remove:
            continue
        if key in values:
            if key in remaining:
                output.append(key + '=' + str(remaining.pop(key)))
        else:
            output.append(line)
    output.extend(k + '=' + str(v) for k, v in remaining.items())
    return '\n'.join(output) + '\n'


def atomic(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.new-' + uuid.uuid4().hex)
    temp.write_bytes(value.encode('utf-8') if isinstance(value, str) else value)
    temp.chmod(mode)
    temp.replace(path)


def inside(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    if path != parent and parent not in path.parents:
        raise RuntimeError('Resolved path escapes the intended project directory')
    return path


def checked_area(request):
    target = request['target']
    if request.get('root') != str(ROOT) or '重庆' in target.get('standardName', '') or target.get('deferredByUser'):
        raise RuntimeError('Request is outside the authorized server-14 scope')
    area, profile = target['directory'], target['profile']
    if not re.fullmatch(r'[a-z][a-z0-9_-]*', area) or not re.fullmatch(r'sichuan[a-z]*', profile):
        raise RuntimeError('Invalid region mapping')
    if not ROOT.is_dir():
        raise RuntimeError('Server-14 netorder root does not exist')
    return inside(ROOT/area, ROOT)


def compose(plan, args, timeout=60):
    return command(['docker', 'compose', '--project-directory', plan['composeDirectory'],
                    '-f', plan['composeFile']] + args, timeout=timeout)


def mapped_port(service, internal):
    values = [int(p['published']) for p in service.get('ports', [])
              if int(p['target']) == internal and p.get('published')]
    if len(set(values)) != 1:
        raise RuntimeError('HTTP port has no unique Compose host publication')
    return values[0]


def java_secret(start):
    raw = text(start)
    match = re.search(r'-Djasypt\.encryptor\.password=(?:"([^"\r\n]*)"|\x27([^\x27\r\n]*)\x27|([^\s\\]+))', raw)
    value = next((g for g in match.groups() if g is not None), '') if match else ''
    if not value or ('$' in value and match.group(2) is None) or '\n' in value or '\r' in value:
        raise RuntimeError('Retained Jasypt setting is unavailable; do not copy another region credential')
    return value


def preflight(request):
    area = checked_area(request)
    target = request['target']
    result = dict(standardName=target['standardName'], directory=target['directory'],
                  projectDirectory=str(area), host=HOST, eligible=area.is_dir(), applicationUrl=None,
                  authUrl=None, status='directory-absent' if not area.is_dir() else 'blocked')
    if not area.is_dir():
        result['reason'] = 'project-directory-does-not-exist'
        return result
    profile = target['profile']
    modern = area/'compose'
    found = [modern/name for name in COMPOSE_NAMES if (modern/name).is_file()]
    if found:
        if len(found) != 1:
            raise RuntimeError('More than one standard Compose file; select one explicitly before deploying')
        result.update(composeDirectory=str(inside(modern, area)), composeFile=str(found[0]), mode='existing-compose')
        cfg = json.loads(compose(result, ['config', '--format', 'json']))
        services = cfg.get('services', {})
        if not {'netorder', 'auth', 'zk'}.issubset(services):
            raise RuntimeError('Standard Compose must contain netorder, auth, and zk')
        for service in ['netorder', 'auth', 'zk']:
            if services[service].get('container_name') != target['directory'] + '-' + service:
                raise RuntimeError('Compose container name belongs to a different region')
        service = services['netorder']
        if service.get('environment', {}).get('SPRING_PROFILE', profile) != profile:
            raise RuntimeError('Compose Spring profile belongs to a different region')
        props_path = modern/'config/netorder'/('application-' + profile + '.properties')
        if not props_path.is_file():
            raise RuntimeError('Missing exact regional external application configuration')
        app = properties(text(props_path))
        entrypoint = modern/'entrypoint.sh'
        if entrypoint.is_file():
            explicit = re.findall(r'--spring\.profiles\.active=(sichuan[a-z]*)', text(entrypoint))
            if explicit and set(explicit) != {profile}:
                raise RuntimeError('Entrypoint activates a different regional Spring profile')
        internal = int(app.get('server.port', 0))
        http = mapped_port(service, internal)
        release_vars = re.findall(r'\$\{([A-Z_][A-Z0-9_]*)[^}]*\}:/app/release', text(found[0]))
        if len(set(release_vars)) != 1:
            raise RuntimeError('Cannot identify the Compose release pointer variable')
        context = app.get('server.servlet.context-path', app.get('server.context-path', '/netorder'))
        if not re.fullmatch(r'/[A-Za-z0-9/_-]*', context):
            raise RuntimeError('Unexpected application context path')
        auth = mapped_port(services['auth'], 8080)
        required = [internal]
        if app.get('netty.enable', '').lower() == 'true' and app.get('netty.port'):
            required.append(int(app['netty.port']))
        result.update(status='ready', composeProject=cfg.get('name'), httpPort=http,
                      containerHttpPort=internal, contextPath=context, authPort=auth,
                      releaseVariable=release_vars[0], requiredListeningPorts=required,
                      containerName=service['container_name'],
                      applicationUrl=f'http://{HOST}:{http}{context.rstrip("/")}/',
                      authUrl=f'http://{HOST}:{auth}/auth/')
        return result
    expected = 'application-' + profile + '.properties'
    candidates = [area/'netorder-docker', area/'netorder']
    app_dirs = [p for p in candidates if (p/expected).is_file()]
    if len(app_dirs) != 1:
        other = sorted({p.name for d in candidates if d.is_dir() for p in d.glob('application-*.properties')})
        result.update(reason='missing-or-mismatched-regional-config', expectedConfig=expected, foundConfigs=other)
        return result
    app_dir = inside(app_dirs[0], area)
    auth_dir = inside(ROOT/target.get('authDirectory', target['directory']+'/auth-docker'), ROOT)
    auth_props = [p for p in [auth_dir/'application.properties', auth_dir/'config/application.properties'] if p.is_file()]
    zoo_configs = [p for p in [auth_dir/'zoo.cfg', auth_dir/'conf/zoo.cfg'] if p.is_file()]
    if len(auth_props) != 1 or len(zoo_configs) != 1 or not (auth_dir/'auth.jar').is_file():
        result.update(reason='missing-or-ambiguous-regional-auth-configuration')
        return result
    if not (app_dir/'start.sh').is_file():
        result.update(reason='missing-retained-regional-startup-setting')
        return result
    java_secret(app_dir/'start.sh')  # Verify availability; never include it in plan/result.
    app, auth, zoo = properties(text(app_dir/expected)), properties(text(auth_props[0])), properties(text(zoo_configs[0]))
    http, auth_port, zk_port = int(app.get('server.port', 0)), int(auth.get('server.port', 0)), int(zoo.get('clientPort', 0))
    if not all(1 <= value <= 65535 for value in [http, auth_port, zk_port]) or len({http, auth_port, zk_port}) != 3:
        raise RuntimeError('Legacy application/auth/ZK ports are invalid or conflicting')
    context = app.get('server.servlet.context-path', '/netorder')
    if context != '/netorder':
        raise RuntimeError('Unexpected legacy application context path')
    admin_port=int(zoo.get('admin.serverPort',0))
    if admin_port and (not 1<=admin_port<=65535 or admin_port in {http,auth_port,zk_port}):
        raise RuntimeError('Legacy Zookeeper admin port conflicts with this project')
    result.update(status='ready', mode='bootstrap-compose', composeDirectory=str(modern),
                  composeFile=str(modern/'compose.yaml'), composeProject=target['directory']+'-netorder',
                  containerName=target['directory']+'-netorder', httpPort=http, authPort=auth_port,
                  containerHttpPort=http, zkPort=zk_port, zkAdminPort=admin_port or None, contextPath=context,
                  releaseVariable=target['directory'].upper()+'_RELEASE_PATH',
                  legacyAppDirectory=str(app_dir), authDirectory=str(auth_dir),
                  authConfig=str(auth_props[0]), zooConfig=str(zoo_configs[0]),
                  requiredListeningPorts=[http]+([int(app['netty.port'])] if app.get('netty.enable','').lower()=='true' and app.get('netty.port') else []),
                  applicationUrl=f'http://{HOST}:{http}/netorder/', authUrl=f'http://{HOST}:{auth_port}/auth/')
    return result


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def package_valid(package, target):
    build = str(package['buildId'])
    if not build.isdigit() or int(package.get('definitionId', 0)) != int(target['definitionId']):
        raise RuntimeError('Package build identity does not match target mapping')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.jar', package['jar']) or not package['jar'].startswith('dcsd-springboot-'+target['profile']+'-'):
        raise RuntimeError('Package belongs to another regional profile')
    names = [f['name'] for f in package['files']]
    if len(set(names)) != len(names) or not {package['jar'], 'license.txt', 'sjt_agent.jar'}.issubset(names):
        raise RuntimeError('Incomplete or duplicate package files')
    if set(names) - {package['jar'], 'license.txt', 'sjt_agent.jar', 'ReadMe.txt'}:
        raise RuntimeError('Unexpected release file name')


def release_matches(path, package):
    return Path(path).is_dir() and all((Path(path)/f['name']).is_file()
            and (Path(path)/f['name']).stat().st_size == f['size']
            and digest(Path(path)/f['name']) == f['sha256'] for f in package['files'])


def runtime(plan, package=None):
    info = inspect(plan['containerName'])
    if not info or not info['State']['Running'] or info['State'].get('Health', {}).get('Status') != 'healthy':
        raise RuntimeError('Application container is not healthy')
    dependencies={}
    for service in ['auth','zk']:
        item=inspect(plan['directory']+'-'+service)
        dependencies[service]=item['State'].get('Health',{}).get('Status') if item and item['State']['Running'] else 'not-running'
    if set(dependencies.values())!={'healthy'}:
        raise RuntimeError('Compose auth/ZK dependencies are not healthy')
    top = command(['docker', 'top', plan['containerName'], '-eo', 'pid,comm,args'])
    java_line = next((line.split(None, 2)[2] for line in top.splitlines()[1:]
                      if len(line.split(None, 2)) == 3 and line.split(None, 2)[1] == 'java'), '')
    jar_match = re.search(r'(?:^|\s)-jar\s+([^\s]+)', java_line)
    if not jar_match:
        raise RuntimeError('No actual Java -jar process found')
    jar_path = jar_match.group(1)
    sha = command(['docker', 'exec', plan['containerName'], 'sha256sum', jar_path], timeout=90).split()[0]
    expected = str(Path(plan['composeDirectory'])/'releases'/str(package['buildId'])) if package else None
    mount = next((m['Source'] for m in info['Mounts'] if m['Destination'] == '/app/release'), None)
    if package and (Path(jar_path).name != package['jar'] or sha != package['packageSha256'] or mount != expected):
        raise RuntimeError('Actual JVM package/mount does not match the requested official artifact')
    sockets = command(['nsenter', '-t', str(info['State']['Pid']), '-n', 'ss', '-ltnH'])
    ports = sorted({int(m.group(1)) for line in sockets.splitlines() if len(line.split()) >= 4
                    if (m := re.search(r':(\d+)$', line.split()[3]))})
    if not set(plan['requiredListeningPorts']).issubset(ports):
        raise RuntimeError('Required application listeners are not ready')
    status = command(['curl', '-sS', '--max-time', '10', '-o', '/dev/null', '-w', '%{http_code}',
                       plan['applicationUrl']]).strip()
    if status not in {'200', '302'}:
        raise RuntimeError('External application URL is not HTTP ready')
    return dict(containerId=info['Id'], startedAt=info['State']['StartedAt'], restarts=info['RestartCount'],
                health='healthy', actualJar=Path(jar_path).name, actualPackageSha256=sha,
                releaseDirectory=mount, listeningPorts=ports, httpStatus=int(status),dependencyHealth=dependencies)


def free_subnet(port):
    import ipaddress
    occupied = []
    ids = command(['docker', 'network', 'ls', '-q']).split()
    for item in docker_json(['network', 'inspect']+ids) if ids else []:
        # Docker's built-in host/none networks have no IPAM configuration.
        configs = (item.get('IPAM') or {}).get('Config') or []
        for config in configs:
            subnet = config.get('Subnet')
            if subnet and ':' not in subnet:
                occupied.append(ipaddress.ip_network(subnet))
    preferred = port % 1000
    for index in ([preferred] if preferred < 256 else []) + [n for n in range(32, 250) if n != preferred]:
        subnet = ipaddress.ip_network(f'10.214.{index}.0/24')
        if not any(subnet.overlaps(other) for other in occupied):
            return str(subnet)
    raise RuntimeError('No free configured Docker subnet found')


def bootstrap(plan, target, package):
    directory = Path(plan['composeDirectory'])
    if any((directory/n).exists() for n in COMPOSE_NAMES):
        raise RuntimeError('A Compose file appeared concurrently; re-plan before switching')
    app_dir, auth_dir = Path(plan['legacyAppDirectory']), Path(plan['authDirectory'])
    profile, area = target['profile'], target['directory']
    app = update_properties(text(app_dir/('application-'+profile+'.properties')), {
        'dubbo.registry.address':'zookeeper://zk:2181', 'server.servlet.session.cookie.name':area.upper()+'_NETORDER_SESSION',
        'server.servlet.session.tracking-modes':'cookie', 'spring.session.redis.namespace':area+'-netorder:session'},
        remove=['netorder.package.finalname'])
    auth = update_properties(text(plan['authConfig']), {'server.port':8080,
        'spring.dubbo.registry.address':'zookeeper://zk:2181', 'server.session.cookie.name':area.upper()+'_AUTH_SESSION',
        'server.session.tracking-modes':'cookie', 'spring.session.redis.namespace':area+'-auth:session'})
    zoo_source = text(plan['zooConfig'])
    zoo_config = properties(zoo_source)
    data_dir = zoo_config.get('dataDir','/data').rstrip('/')
    log_dir = zoo_config.get('dataLogDir',data_dir).rstrip('/')
    zoo = update_properties(zoo_source, {'clientPort':2181, 'dataDir':'/data',
        'dataLogDir':'/data' if log_dir == data_dir else '/datalog',
        'admin.serverPort':8080, '4lw.commands.whitelist':'srvr,ruok,stat,mntr'})
    for name in ['config/netorder','auth','logs/netorder','logs/auth','logs/zk','logs/requests',
                 'data/netorder-registry','data/auth-registry','data/work','data/word','rollback']:
        (directory/name).mkdir(parents=True,exist_ok=True)
    atomic(directory/'config/netorder'/('application-'+profile+'.properties'),app)
    atomic(directory/'config/auth.properties',auth)
    # The official entrypoint drops to the zookeeper user before reading this mount.
    atomic(directory/'config/zoo.cfg',zoo,0o644)
    shutil.copy2(auth_dir/'auth.jar', directory/'auth/auth.jar')
    atomic(directory/'.env.jvm','JASYPT_ENCRYPTOR_PASSWORD='+java_secret(app_dir/'start.sh')+'\n')
    atomic(directory/'entrypoint.sh',ENTRYPOINT,0o755)
    env = plan['releaseVariable']+'=./releases/'+str(package['buildId'])+'\nAPP_JAR='+package['jar']+'\n'
    atomic(directory/'.env',env)
    logging = {'driver':'json-file','options':{'max-size':'20m','max-file':'5'}}
    common = dict(image=JAVA_IMAGE,restart='unless-stopped',working_dir='/app',init=True,
        environment={'TZ':'Asia/Shanghai'},networks=['backend'],logging=logging,stop_grace_period='60s')
    auth_service = dict(common,container_name=area+'-auth',entrypoint=['sh','/app/entrypoint.sh','auth'],
        ports=[str(plan['authPort'])+':8080'],
        volumes=['./entrypoint.sh:/app/entrypoint.sh:ro','./auth/auth.jar:/app/auth.jar:ro',
                 './config/auth.properties:/app/config/application.properties:ro','./logs/auth:/app/logs','./data/auth-registry:/app/dubbo-registry'],
        depends_on={'zk':{'condition':'service_healthy'}},
        healthcheck={'test':['CMD-SHELL',"curl -sS --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/auth/ | grep -qE '^(200|302)$$'"],
                     'interval':'15s','timeout':'8s','retries':16,'start_period':'60s'})
    release='${'+plan['releaseVariable']+'}'
    app_service=dict(common,container_name=area+'-netorder',entrypoint=['sh','/app/entrypoint.sh','netorder'],
        environment={'TZ':'Asia/Shanghai','APP_JAR':'${APP_JAR}','SPRING_PROFILE':profile},
        env_file=[{'path':'./.env.jvm','format':'raw'}],
        ports=[str(p)+':'+str(p) for p in plan['requiredListeningPorts']],
        volumes=['./entrypoint.sh:/app/entrypoint.sh:ro',release+':/app/release:ro',release+'/license.txt:/app/license.txt:ro',
          './config/netorder:/app/config:ro','./logs/netorder:/app/logs','./logs/requests:/home/czp/log',
          './data/netorder-registry:/app/dubbo-registry','./data/work:/app/work','./data/word:/app/word'],
        depends_on={'zk':{'condition':'service_healthy'},'auth':{'condition':'service_healthy'}},
        healthcheck={'test':['CMD','curl','--fail','--silent','--max-time','5','--output','/dev/null',
                            'http://127.0.0.1:'+str(plan['containerHttpPort'])+'/netorder/'],
                     'interval':'15s','timeout':'8s','retries':20,'start_period':'90s'})
    zk_ports=[str(plan['zkPort'])+':2181']
    if plan.get('zkAdminPort'): zk_ports.append(str(plan['zkAdminPort'])+':8080')
    zk_service=dict(image=ZK_IMAGE,container_name=area+'-zk',restart='unless-stopped',
        environment={'TZ':'Asia/Shanghai','JVMFLAGS':'-Xms256m -Xmx512m'},
        ports=zk_ports,networks=['backend'],logging=logging,
        volumes=['./config/zoo.cfg:/conf/zoo.cfg:ro','./data/zk:/data','./data/zk-datalog:/datalog','./logs/zk:/logs'],
        healthcheck={'test':['CMD-SHELL',"zkServer.sh status 2>/dev/null | grep -q 'Mode: standalone'"],
                     'interval':'10s','timeout':'5s','retries':12,'start_period':'15s'})
    doc=dict(name=area+'-netorder',services={'zk':zk_service,'auth':auth_service,'netorder':app_service},
        networks={'backend':{'name':area+'-netorder-backend','driver':'bridge',
                            'ipam':{'config':[{'subnet':free_subnet(plan['httpPort'])}]}}})
    atomic(directory/'compose.yaml',json.dumps(doc,ensure_ascii=False,indent=2)+'\n',0o600)
    compose(plan,['config','--quiet'])


def clone_zk_state(plan, old_zk):
    if not old_zk:
        return
    config=properties(text(plan['zooConfig']))
    data_dir=config.get('dataDir','/data').rstrip('/')
    log_dir=config.get('dataLogDir',data_dir).rstrip('/')
    # When dataLogDir was omitted, old transaction logs share dataDir. Do not
    # copy an unused /datalog volume instead and lose transactions after a snapshot.
    storage = [(data_dir,'data/zk')]
    if log_dir != data_dir:
        storage.append((log_dir,'data/zk-datalog'))
    for source_dir,destination in storage:
        matches=[m for m in old_zk.get('Mounts',[]) if m['Destination'].rstrip('/')==source_dir]
        output=Path(plan['composeDirectory'])/destination
        if len(matches)==1 and Path(matches[0]['Source']).is_dir():
            shutil.copytree(matches[0]['Source'],output,dirs_exist_ok=True)
        elif source_dir in {'/data','/datalog','/tmp/zookeeper','/var/lib/zookeeper'}:
            output.mkdir(parents=True,exist_ok=True)
            command(['docker','cp',old_zk['Name'].lstrip('/')+':'+source_dir+'/.',str(output)],timeout=180)
        else:
            raise RuntimeError('Legacy Zookeeper storage is not mapped; inspect before migrating')


def deploy(request):
    area = checked_area(request)
    package, target = request['package'], request['target']
    package_valid(package,target)
    if not area.is_dir():
        return preflight(request)  # Never create a region excluded by the directory-exists rule.
    with (area/'.codex-deploy.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(status='blocked',eligible=True,reason='another-deployment-holds-the-project-lock',applicationUrl=None)
        plan=preflight(request)
        if plan['status']!='ready':
            return plan
        if request.get('preventOlderBuild') and plan['mode']=='existing-compose':
            compose_dir=Path(plan['composeDirectory'])
            manifest=compose_dir/'release.json'
            recorded=json.loads(manifest.read_text('utf-8')).get('buildId') if manifest.exists() else None
            env_value=properties(text(compose_dir/'.env')).get(plan['releaseVariable'],'')
            matched=re.fullmatch(r'(?:\./)?releases/(\d+)',env_value)
            current_ids=[int(value) for value in (recorded, matched.group(1) if matched else None)
                         if value is not None and str(value).isdigit()]
            if current_ids and max(current_ids)>int(package['buildId']):
                return dict(plan,status='blocked',reason='target-already-on-newer-build',currentBuildId=max(current_ids),applicationUrl=None)
        if plan['mode']=='existing-compose':
            dependencies={}
            for service in ['auth','zk']:
                item=inspect(target['directory']+'-'+service)
                dependencies[service]=item['State'].get('Health',{}).get('Status') if item and item['State']['Running'] else 'not-running'
            if set(dependencies.values())!={'healthy'}:
                return dict(plan,status='blocked',reason='compose-auth-or-zookeeper-not-ready',dependencyHealth=dependencies)
        release=inside(Path(plan['composeDirectory'])/'releases'/str(package['buildId']),area)
        if release.exists() and not release_matches(release,package):
            return dict(plan,status='blocked',reason='immutable-release-directory-content-differs')
        if not release.exists():
            staging=request.get('staging')
            if not staging:
                raise RuntimeError('Verified release has not been uploaded')
            stage=inside(staging,area/'.staging')
            if stage.parent != (area/'.staging').resolve() or not re.fullmatch(str(package['buildId'])+r'-[a-f0-9]{32}',stage.name):
                raise RuntimeError('Staging directory does not belong to this build attempt')
            if not release_matches(stage,package):
                raise RuntimeError('Uploaded release integrity check failed')
            release.parent.mkdir(parents=True,exist_ok=True)
            stage.rename(release)
        atomic(release/'deployment-manifest.json',json.dumps(package,ensure_ascii=False,indent=2)+'\n')
        if plan['mode']=='existing-compose' and not request.get('forceRedeploy'):
            try:
                observed=runtime(plan,package)
            except RuntimeError:
                observed=None
            if observed:
                return dict(plan,status='already-current',runtime=observed)
        area_name=target['directory']
        old_names=['th-netorder-'+area_name,'th-auth-'+area_name,'th-zk-'+area_name]
        legacy=[i for name in old_names if (i:=inspect(name))] if plan['mode']=='bootstrap-compose' else []
        excluded=[plan['containerName']]+([area_name+'-auth',area_name+'-zk']+old_names if legacy else [])
        before=snapshot(excluded)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        backup=Path(plan['composeDirectory'])/'rollback'/stamp
        backup.mkdir(parents=True,exist_ok=False)
        env_path=Path(plan['composeDirectory'])/'.env'
        env_before=env_path.read_bytes() if env_path.exists() else None
        release_metadata=Path(plan['composeDirectory'])/'release.json'
        meta_before=release_metadata.read_bytes() if release_metadata.exists() else None
        if env_before is not None: atomic(backup/'previous.env',env_before)
        if meta_before is not None: atomic(backup/'previous-release.json',meta_before)
        atomic(backup/'unrelated-before.json',json.dumps(before,indent=2))
        atomic(backup/'request.json',json.dumps({'target':target,'package':package},ensure_ascii=False,indent=2))
        switched=False
        try:
            if plan['mode']=='bootstrap-compose':
                progress('prepare region-specific Compose without changing legacy files')
                for image in [JAVA_IMAGE,ZK_IMAGE]: command(['docker','image','inspect',image])
                bootstrap(plan,target,package)
                # Stop only this region's retained legacy containers before cloning ZK state.
                for item in legacy:
                    if item['State']['Running']: command(['docker','stop','-t','60',item['Name'].lstrip('/')],timeout=90)
                old_zk=next((i for i in legacy if i['Name'].lstrip('/')=='th-zk-'+area_name),None)
                clone_zk_state(plan,old_zk)
                word=Path(plan['legacyAppDirectory'])/'word'
                if word.is_dir(): shutil.copytree(word,Path(plan['composeDirectory'])/'data/word',dirs_exist_ok=True)
                switched=True
                compose(plan,['up','-d','--pull','never','--force-recreate','--wait','--wait-timeout','300'],timeout=360)
            else:
                progress('switch only the application service to the verified immutable release')
                env=update_properties(env_before.decode('utf-8'),{
                    plan['releaseVariable']:'./releases/'+str(package['buildId']),'APP_JAR':package['jar']})
                atomic(env_path,env)
                switched=True
                compose(plan,['config','--quiet'])
                compose(plan,['up','-d','--pull','never','--no-deps','--force-recreate','--wait','--wait-timeout','300','netorder'],timeout=360)
            progress('verify actual JVM package, HTTP readiness, and required listeners')
            observed=runtime(plan,package)
            if plan['mode']=='bootstrap-compose':
                for item in legacy: command(['docker','update','--restart=no',item['Name'].lstrip('/')])
            after=snapshot(excluded)
            changed=sorted(n for n in before if before[n]!=after.get(n))
            result=dict(plan,status='deployed',runtime=observed,backupDirectory=str(backup),
                        unrelatedContainersUnchanged=not changed,unrelatedContainerCount=len(before),
                        changedUnrelatedContainers=changed)
            atomic(release_metadata,json.dumps(dict(package,applicationUrl=plan['applicationUrl']),ensure_ascii=False,indent=2)+'\n')
        except Exception as error:
            progress('deployment failed; restoring the exact prior release/state')
            rollback_error=None
            try:
                if plan['mode']=='bootstrap-compose':
                    if Path(plan['composeFile']).exists(): compose(plan,['stop'],timeout=180)
                    for item in legacy:
                        policy=item['HostConfig'].get('RestartPolicy',{})
                        restart=policy.get('Name') or 'no'
                        if restart=='on-failure' and policy.get('MaximumRetryCount'): restart+=':'+str(policy['MaximumRetryCount'])
                        command(['docker','update','--restart='+restart,item['Name'].lstrip('/')])
                        if item['State']['Running']: command(['docker','start',item['Name'].lstrip('/')],timeout=120)
                    # A rolled-back bootstrap must be planned as a bootstrap on retry.
                    # Preserve the generated Compose in the rollback record.
                    if Path(plan['composeFile']).exists():
                        Path(plan['composeFile']).replace(backup/'failed-compose.yaml')
                elif switched and env_before is not None:
                    atomic(env_path,env_before)
                    compose(plan,['up','-d','--pull','never','--no-deps','--force-recreate','--wait','--wait-timeout','300','netorder'],timeout=360)
                    runtime(plan)
                if meta_before is not None: atomic(release_metadata,meta_before)
            except Exception as failure:
                rollback_error=str(failure)
            result=dict(plan,status='failed-rollback-failed' if rollback_error else 'failed-rolled-back',
                        reason=str(error),rollbackError=rollback_error,backupDirectory=str(backup))
        atomic(backup/'result.json',json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        atomic(Path(plan['composeDirectory'])/'last-deployment.json',json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        return result


def main(request):
    operation=request.get('operation')
    if operation=='plan': return preflight(request)
    if operation=='allocate-staging':
        plan=preflight(request)
        if plan.get('status')!='ready':return plan
        package_valid(request['package'],request['target'])
        area=checked_area(request)
        parent=inside(area/'.staging',area)
        parent.mkdir(mode=0o700,exist_ok=True)
        directory=parent/(str(request['package']['buildId'])+'-'+uuid.uuid4().hex)
        directory.mkdir(mode=0o700,exist_ok=False)
        return dict(status='staging-allocated',stagingDirectory=str(directory))
    if operation=='package-state':
        plan=preflight(request)
        package_valid(request['package'],request['target'])
        release=inside(Path(plan.get('composeDirectory', str(checked_area(request)/'compose')))/'releases'/str(request['package']['buildId']), checked_area(request))
        return dict(releaseMatches=release_matches(release,request['package']))
    if operation=='deploy': return deploy(request)
    raise RuntimeError('Unknown server-14 operation')


if __name__=='__main__':
    try:
        request=json.loads(base64.b64decode(sys.argv[1]))
        result=main(request)
    except Exception as error:
        result=dict(status='failed',eligible=True,reason=str(error),applicationUrl=None)
    try:
        print(json.dumps(result,ensure_ascii=False),flush=True)
    except (BrokenPipeError,OSError):
        pass
    sys.exit(0 if result.get('status') not in {'failed','failed-rolled-back','failed-rollback-failed','blocked'} else 5)
