#!/usr/bin/env python3
"""Read-only 30-second host observation; writes only to this directory."""
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get('HERMES_HOME', str(Path.home()/'.hermes')))
UNIT = 'hermes-gateway.service'

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def save(name, data):
    p = ROOT / name
    tmp = ROOT / (name + '.' + str(os.getpid()) + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) if not isinstance(data, str) else data)
    tmp.replace(p)

def run(args):
    p = subprocess.run(args, text=True, capture_output=True, timeout=15)
    return {'returncode': p.returncode, 'stdout': p.stdout.strip(), 'stderr': p.stderr.strip()}

def rows():
    p = ROOT / 'samples.jsonl'
    return [json.loads(s) for s in p.read_text().splitlines() if s] if p.exists() else []

def jobs():
    p = ROOT / 'jobs.json'
    return json.loads(p.read_text()) if p.exists() else {}

def state(message):
    r = rows()
    next_step = '无；任务完成，保留本地报告与原始证据。' if message.startswith('验收通过') else '采样期间自动检查进度；采样结束后自动验收、停用本任务调度并生成 report.md。'
    save('task_state.md', '# Hermes 短时健康观察\n\n'
         '目标：每30秒采样，实际跨度至少300秒，验收后才写 completion.json。\n'
         '约束：不修改生产服务、不安装软件；输出仅本地。\n'
         f'更新时间：{now()}\n当前阶段：{message}\n实际样本数：{len(r)}\n'
         f'采样起点：{r[0]["timestamp"] if r else "未开始"}\n'
         f'后台进程记录：runner.json；定时任务：{json.dumps(jobs(), ensure_ascii=False)}\n'
         '证据：samples.jsonl、progress.json、sampling_done.json、validation.json、cron_cleanup.json。\n'
         f'下一步：{next_step}\n')

def sample(index, start, prev):
    wall = time.time()
    cpu = [int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    total, idle = sum(cpu), cpu[3] + cpu[4]
    usage = None if prev is None else 100 * (1 - (idle-prev[1]) / max(1, total-prev[0]))
    mem = {l.split(':')[0]: int(l.split()[1])*1024 for l in Path('/proc/meminfo').read_text().splitlines()}
    result = run(['systemctl','--user','show',UNIT,'-p','ActiveState','-p','SubState','-p','MainPID','-p','NRestarts','-p','ActiveEnterTimestamp','-p','ExecMainStartTimestamp','-p','Result'])
    gateway = dict(l.split('=',1) for l in result['stdout'].splitlines() if '=' in l)
    gateway['query_returncode'] = result['returncode']
    gateway['query_error'] = result['stderr']
    cron = {}
    for name in ('ticker_heartbeat','ticker_last_success'):
        try:
            raw = (HOME/'cron'/name).read_text().strip()
            parts = raw.split()
            cron[name] = {'raw':raw,'epoch':float(parts[0]),'age_seconds':wall-float(parts[0])}
            if len(parts)>1:
                pid = int(parts[1]); cron[name]['pid'] = pid
                cron[name]['writer_alive'] = Path(f'/proc/{pid}').exists()
        except Exception as e:
            cron[name] = {'error':str(e)}
    disks = {}
    for path in ('/', str(ROOT)):
        d = shutil.disk_usage(path)
        disks[path] = {'total_bytes':d.total,'free_bytes':d.free,'used_percent':100*d.used/d.total}
    pressure = {name:Path('/proc/pressure'/Path(name)).read_text().strip() for name in ('cpu','memory','io')}
    s = {'index':index,'timestamp':now(),'epoch':wall,'elapsed_seconds':time.monotonic()-start,
         'cpu_count':os.cpu_count(),'load_1_5_15':list(os.getloadavg()),'cpu_used_percent':usage,
         'memory':{k:mem[k] for k in ('MemTotal','MemAvailable','SwapTotal','SwapFree')},
         'disks':disks,'gateway':gateway,'cron':cron,'pressure':pressure}
    with (ROOT/'samples.jsonl').open('a') as f:
        f.write(json.dumps(s,ensure_ascii=False)+'\n'); f.flush(); os.fsync(f.fileno())
    return total,idle

def progress(source):
    r = rows()
    data = {'checked_at':now(),'source':source,'samples':len(r),'last_timestamp':r[-1]['timestamp'] if r else None,
            'elapsed_seconds':r[-1]['elapsed_seconds'] if r else None,
            'sampler_alive':False}
    if (ROOT/'runner.json').exists():
        pid = json.loads((ROOT/'runner.json').read_text())['pid']
        data['sampler_alive'] = Path(f'/proc/{pid}').exists()
    save('progress_'+source+'.json',data)
    save('progress.json',data)
    if not (ROOT/'completion.json').exists():
        state('进度已检查，继续采样' if data['sampler_alive'] else '进度检查：采样进程已结束或异常，需核对采样完成标志')
    print(json.dumps(data,ensure_ascii=False),flush=True)

def finalize(source):
    with (ROOT/'finalize.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (ROOT/'completion.json').exists():
            print((ROOT/'completion.json').read_text(),flush=True); return
        r = rows()
        gaps = [b['epoch']-a['epoch'] for a,b in zip(r,r[1:])]
        checks = {'sampling_finished':(ROOT/'sampling_done.json').exists(),'sample_count':len(r),
                  'span_seconds':r[-1]['epoch']-r[0]['epoch'] if r else 0,
                  'monotonic_span_seconds':r[-1]['elapsed_seconds']-r[0]['elapsed_seconds'] if r else 0,
                  'intervals_seconds':gaps,'indices_sequential':[s['index'] for s in r]==list(range(len(r))),
                  'all_gateway_queries_ok':all(s['gateway']['query_returncode']==0 for s in r),
                  'all_heartbeat_reads_ok':all('age_seconds' in s['cron']['ticker_heartbeat'] and 'age_seconds' in s['cron']['ticker_last_success'] for s in r)}
        ok = checks['sampling_finished'] and len(r)>=11 and checks['span_seconds']>=300 and checks['monotonic_span_seconds']>=300 and checks['indices_sequential'] and all(28<=g<=32 for g in gaps) and checks['all_gateway_queries_ok'] and checks['all_heartbeat_reads_ok']
        checks.update({'passed':ok,'source':source,'validated_at':now()}); save('validation.json',checks)
        if not ok:
            state('验收未通过，未标记完成；详见 validation.json')
            raise RuntimeError('实际采样尚未完成或验收未通过')
        ids = jobs()
        cleanup = []
        for role,jid in ids.items():
            before = json.loads((HOME/'cron/jobs.json').read_text())
            records_before = before.get('jobs',[]) if isinstance(before,dict) else before
            existing = next((j for j in records_before if j['id']==jid),None)
            if existing is None or not existing.get('enabled',False):
                result = {'returncode':0,'stdout':'Already inactive; no pause needed.','stderr':''}
            else:
                result = run([str(Path.home()/'.local/bin/hermes'),'cron','pause',jid])
            cleanup.append({'role':role,'job_id':jid,**result})
        stored = json.loads((HOME/'cron/jobs.json').read_text())
        records = stored.get('jobs',[]) if isinstance(stored,dict) else stored
        byid = {j['id']:j for j in records}
        cleanup_ok = all(jid not in byid or not byid[jid].get('enabled',True) for jid in ids.values())
        save('cron_cleanup.json',{'checked_at':now(),'results':cleanup,'all_task_jobs_disabled':cleanup_ok,
             'jobs':[{k:byid[jid].get(k) for k in ('id','enabled','state','next_run_at','last_status')} for jid in ids.values() if jid in byid]})
        if not cleanup_ok or len(ids)!=2:
            state('采样已完成，定时任务清理待核验'); raise RuntimeError('定时任务尚未全部停用')
        cpu = [s['cpu_used_percent'] for s in r if s['cpu_used_percent'] is not None]
        loads = [s['load_1_5_15'][0] for s in r]
        mem = [s['memory']['MemAvailable']/1024**3 for s in r]
        disk = [s['disks']['/']['free_bytes']/1024**3 for s in r]
        pids = sorted(set(s['gateway']['MainPID'] for s in r))
        restarts = [int(s['gateway']['NRestarts']) for s in r]
        starts = sorted(set(s['gateway']['ExecMainStartTimestamp'] for s in r))
        stable = len(pids)==1 and pids[0]!='0' and len(set(restarts))==1 and len(starts)==1 and all(s['gateway']['ActiveState']=='active' and s['gateway']['SubState']=='running' for s in r)
        hb = [s['cron']['ticker_heartbeat']['age_seconds'] for s in r]
        success = [s['cron']['ticker_last_success']['age_seconds'] for s in r]
        advancing = r[-1]['cron']['ticker_heartbeat']['epoch']>r[0]['cron']['ticker_heartbeat']['epoch'] and r[-1]['cron']['ticker_last_success']['epoch']>r[0]['cron']['ticker_last_success']['epoch']
        scheduler_ok = advancing and max(hb)<120 and max(success)<120 and all(s['cron']['ticker_heartbeat'].get('writer_alive',False) for s in r)
        issues=[]
        if not stable: issues.append('网关状态、PID、启动时间或累计重启计数在窗口内发生异常/变化，详见每条 gateway 字段。')
        if not scheduler_ok: issues.append('cron心跳或成功tick记录滞后、未推进或写入进程不可见。')
        if max(loads)>r[0]['cpu_count']: issues.append('1分钟负载曾超过CPU逻辑核数；需结合CPU使用率与PSI判断。')
        if min(mem)*1024**3/r[0]['memory']['MemTotal']<0.1: issues.append('可用内存曾低于总内存10%。')
        if max(s['disks']['/']['used_percent'] for s in r)>90: issues.append('根分区使用率曾高于90%。')
        if max(cpu)>90: issues.append('至少一个30秒区间CPU使用率超过90%，不能据此单独断言持续压力。')
        pressure = {k:max(float(s['pressure'][k].split('avg10=')[1].split()[0]) for s in r) for k in ('cpu','memory','io')}
        for k in ('memory','io'):
            if pressure[k]>1: issues.append(f'{k} PSI some avg10曾超过1%，最大{pressure[k]:.2f}%。')
        tz=dt.timezone(dt.timedelta(hours=8))
        local=lambda s:dt.datetime.fromisoformat(s).astimezone(tz).isoformat()
        text = '# Hermes 短时运行健康观察报告\n\n'
        text += f'观察时间（UTC）：{r[0]["timestamp"]} 至 {r[-1]["timestamp"]}\n北京时间：{local(r[0]["timestamp"])} 至 {local(r[-1]["timestamp"])}\n'
        text += f'实际跨度：{checks["span_seconds"]:.3f}秒；实际样本数：{len(r)}；采样间隔：{min(gaps):.3f}—{max(gaps):.3f}秒。\n\n'
        text += '## 结论\n'+('采样窗口内网关运行稳定，未观察到重启。' if stable else '窗口内发现网关异常或状态变化。')+'\n'+('未发现明显的持续资源压力信号。' if not issues else '发现以下需要关注的信号，不能直接认定服务故障。')+'\n\n'
        text += f'## 资源变化\nCPU逻辑核数：{r[0]["cpu_count"]}；1分钟负载 起点/终点/最小/最大：{loads[0]:.2f}/{loads[-1]:.2f}/{min(loads):.2f}/{max(loads):.2f}。\n'
        text += f'CPU区间使用率 最小/平均/最大：{min(cpu):.2f}%/{sum(cpu)/len(cpu):.2f}%/{max(cpu):.2f}%；首条没有前一采样，使用率为null。\n'
        text += f'可用内存 起点/终点/最小/最大：{mem[0]:.3f}/{mem[-1]:.3f}/{min(mem):.3f}/{max(mem):.3f} GiB。Swap总量：{r[0]["memory"]["SwapTotal"]}字节。\n'
        text += f'根磁盘剩余 起点/终点/最小：{disk[0]:.3f}/{disk[-1]:.3f}/{min(disk):.3f} GiB；使用率最大：{max(s["disks"]["/"]["used_percent"] for s in r):.2f}%。工作目录所在文件系统也逐条采集。\n'
        text += f'PSI some avg10峰值：CPU {pressure["cpu"]:.2f}%，内存 {pressure["memory"]:.2f}%，IO {pressure["io"]:.2f}%；原始some/full记录保存在samples.jsonl。\n\n'
        text += f'## 网关\n单元：{UNIT}；PID集合：{pids}；NRestarts起点/终点：{restarts[0]}/{restarts[-1]}；启动时间集合：{starts}。\n'
        text += 'NRestarts是systemd本次计数范围内的自动重启累计值，不代表本次观察发生的重启数，也不是永久历史总数；PID与启动时间一并用于检测手动重启。\n\n'
        text += f'## 调度器\n心跳判定：{"正常" if scheduler_ok else "异常或证据不足"}。心跳年龄范围：{min(hb):.2f}—{max(hb):.2f}秒；成功tick年龄范围：{min(success):.2f}—{max(success):.2f}秒；两类时间戳持续推进：{advancing}。\n'
        text += '本机源码cron/jobs.py中TICKER_INTERVAL_SECONDS=60；采用120秒新鲜度阈值。心跳正常仅证明调度循环存活，不证明所有业务定时任务成功。\n'
        text += f'自动进度检查证据：progress_background.json；cron进度检查实际执行：{(ROOT/"progress_cron.json").exists()}；最终验收执行方：{source}。\n\n'
        text += '## 问题及依据\n'+ ('\n'.join('- '+x for x in issues) if issues else '窗口内未发现符合上述阈值的明显异常。')+'\n'
        if restarts[0]>0: text += f'观察前已存在累计自动重启计数{restarts[0]}；本报告不追溯其原因，不能将其归因于本窗口。\n'
        text += '\n## 验收与范围\nvalidation.json验证样本数、实际跨度、序列、间隔及核心字段读取。采样循环已经结束，两个本任务一次性调度均已停用（cron_cleanup.json读回确认）。未重启/修改生产服务，未安装软件，输出仅本地。\n'
        text += '这是短时只读观察，30秒采样可能漏掉短暂故障；未进行消息收发、模型推理或网络端到端压测，不代表长期稳定性保证。\n'
        save('report.md',text)
        state('验收通过；报告已生成；采样结束；本任务定时调度均已停用')
        save('completion.json',{'status':'completed','sampling_started_at':r[0]['timestamp'],'sampling_ended_at':r[-1]['timestamp'],
             'sample_count':len(r),'report_file':'report.md','completed_at':now(),'span_seconds':checks['span_seconds'],'validation_file':'validation.json','finalizer':source})
        print((ROOT/'completion.json').read_text(),flush=True)

def collect():
    with (ROOT/'sampler.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (ROOT/'samples.jsonl').exists(): raise RuntimeError('拒绝覆盖已有采样')
        save('runner.json',{'pid':os.getpid(),'started_at':now(),'command':str(ROOT/'healthwatch.py')+' collect'})
        start=time.monotonic(); prev=None
        for i in range(11):
            target=start+30*i+(0.05 if i==10 else 0)
            time.sleep(max(0,target-time.monotonic()))
            prev=sample(i,start,prev)
            state('后台采样进行中')
            if i==4: progress('background')
        save('sampling_done.json',{'finished_at':now(),'pid':os.getpid(),'samples':len(rows())})
        state('300秒采样完成，等待一次性验收调度；390秒有本地兜底验收')
        # Bounded fallback: survive a broken cron dispatcher without changing it.
        while time.monotonic()-start<390 and not (ROOT/'completion.json').exists(): time.sleep(2)
        if not (ROOT/'completion.json').exists(): finalize('background_fallback')
        save('runner_exit.json',{'exited_at':now(),'pid':os.getpid(),'completed':(ROOT/'completion.json').exists()})

if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='collect': collect()
    elif mode=='progress': progress('cron')
    elif mode=='finalize': finalize('cron')
    else: raise ValueError(mode)
