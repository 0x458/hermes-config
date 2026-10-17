---
name: host-health-observation
description: Observe Linux host and Hermes health with timed follow-ups.
version: 0.1.1
platforms: [linux]
metadata:
  hermes:
    tags: [health, monitoring, background, cron, operations]
    category: devops
---

# Host Health Observation Skill

执行只读的服务器与 Hermes 网关健康观察。复用随附的已验证采样脚本，先启动实际采样，再安排跟进；无需重新阅读 Hermes 源码或重写采样器。

## When to Use

用户要求本机/服务器短时健康观察、后台采样、CPU/内存/磁盘/网关/cron 心跳检查及定时验收。当前模板固定为至少5分钟、每30秒一次，共11条真实样本；不同参数先改模板或选择其他方法，不能假装已支持。

## Prerequisites

Linux、Python3 标准库、可查询的用户级 hermes-gateway.service、运行中的 Hermes cron。只写用户指定的任务目录，所有调度 deliver=local；不修改服务或系统配置。

## How to Run

通过 `terminal` 在任务目录执行下面命令，设置 background=true、persist_on_release=true、notify=true。替换 WORKDIR 为用户给出的绝对工作目录，SKILL_DIR 为本技能所在目录；使用参数路径正常引用空格。

```sh
python3 SKILL_DIR/scripts/prepare.py --workdir WORKDIR && python3 -u WORKDIR/healthwatch.py collect > WORKDIR/sampler.log 2>&1
```

准备器只复制代码，不复制历史样本或报告，拒绝覆盖已有任务产物。采样器使用独占锁，启动后立即写第一条样本。先启动这一命令；发现错误再针对性检查。

## Quick Reference

- `healthwatch.py collect`：后台采样，300秒采满；390秒仍无完成标志则本地兜底验收。
- `healthwatch.py progress`：记录定时检查的样本数与进程状态。
- `healthwatch.py finalize`：严格验收采样后生成 report.md/completion.json。
- `process_manage`：核验后台进程状态及完成通知。
- `cronjob_manage`：列出、创建、回读本任务一次性跟进。

## Procedure

1. 启动后台采样后，调用 `cronjob_manage` 列出现有任务。仅复用当前工作目录的匹配任务，不能复用旧任务的结果。
2. 创建两个一次性任务：progress 使用 schedule="in 2m"，finalize 使用 schedule="in 5m"；都 repeat=1、deliver="local"、failure_deliver="local"、workdir=任务目录、enabled_toolsets=["terminal"]。
3. progress 提示词（务必明确状态文件只读）：只在该工作目录运行 python3 healthwatch.py progress，读取 progress_cron.json 确认采样仍推进；禁止重复启动采样或创建后续任务；task_state.md 由脚本独占写入，代理只能读取，不得补写或 patch。
4. finalize 提示词：运行 python3 healthwatch.py finalize，读取 completion.json、validation.json、cron_cleanup.json、report.md；若尚未采满，只允许等待30秒再试一次。不得提前标记完成，不创建后续任务；task_state.md 由脚本独占写入，代理只能读取，独立复核备注写 cron_audit.json，不得补写或 patch task_state.md。
5. 用 `write_file` 保存 jobs.json（严格只有 progress/finalize 两个键；后台进程元数据另存 background_process.json），格式 {"progress":"实际ID","finalize":"实际ID"}。用一次 `process_manage` poll 或读取首条样本、回读任务，确认已启动。无需持续同步等待或重复审查源码。
6. 收到完成通知后，只读核对报告、11条样本、实际跨度至少300秒、采样进程退出、两条任务结束。task_state.md 由脚本独占写入，主会话也不得补写/patch；附加复核记录写 main_audit.json。避免主会话与 cron 代理同时更新状态文件。仅报告真实结果。

## Pitfalls

单次健康观察不代表长期稳定性。不能把旧的重启计数归因于本窗口。任务完成前不得报告完成。cron 故障时本地兜底可以保留报告，但必须明确标记，不能算作定时跟进成功。已完成的 cron 任务无需再次暂停。

## Verification

检查 samples.jsonl、progress_cron.json、validation.json、completion.json、runner_exit.json 与真实 cron 历史。最终 finalizer="cron" 才表示定时验收真实完成。
