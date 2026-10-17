---
name: hermes-upgrade-guard
description: Preserve and verify Hermes customizations across updates.
version: 0.1.0
author: 0x458
platforms: [linux]
metadata:
  hermes:
    tags: [upgrade, backup, recovery, verification]
    category: devops
    related_skills: [host-health-observation]
---

# Hermes Upgrade Guard Skill

维护本机 Hermes 自主执行配置、SOUL 规则、恢复补丁和健康观察技能。用户要求升级、修改参数或检查升级后失效时使用；只询问升级影响时先做只读检查，不能据此启动升级。

## When to Use

升级前备份和兼容性评估；升级后检查自主执行功能是否仍生效；修改参数后验证并同步已授权的 GitHub 仓库。

## Prerequisites

Linux Hermes 安装、Git、Python 3 和 PyYAML。通过 `terminal` 执行脚本，`read_file` 检查输出，`skill_view` 加载相关技能。使用实际 HERMES_HOME 和安装根目录，不能假定所有实例共享同一个配置。

用户已要求本实例修改先备份、实测通过再同步到 https://github.com/0x458/hermes-config 。该仓库当前经用户确认允许公开，仅上传脱敏内容。访问 GitHub 使用已配置的认证；无认证则保留本地待提交文件并报告阻塞，不读取或输出令牌，不扩展发布范围。

## How to Run

用支持 PyYAML 的 Python 在本技能目录运行 `scripts/audit.py --home HERMES_HOME --repo HERMES_SOURCE_ROOT`。两个路径替换为本机真实路径。默认仅检查，不改参数、不升级、不重启、不创建定时任务。

## Quick Reference

- `scripts/audit.py`：参数值、SOUL 新增章节、采样脚本哈希和恢复补丁结构检查。
- `references/baseline.json`：已验证基准；只有有意修改并实测通过才更新，不能为了消除检查失败而重建基准。
- 存档仓库根目录 `config.overlay.yaml`、`SOUL.additions.md`、`patches/hermes-recovery.patch` 是对应配置、规则和补丁的来源。
- 审计退出码 0 仅代表静态一致性；`runtime_verified` 固定为 false，不能当成升级验收。

## Procedure

1. 明确用户授权的是检查、参数修改还是升级。读取当前源码提交、工作树差异、升级选项、服务状态及活动任务。若有工作进行中，保存连续性记录并等待合理排空，不突然终止任务。
2. **先备份并核验。** 使用本机原生 `hermes backup --help` 确认语法，当前已验证语法为 `hermes backup -o BACKUP_DIR/home.zip -k 0`。使用权限 0700 的唯一目录，目录放在 HERMES_HOME 和源码目录之外，避免备份递归；备份包含凭据，权限 0600，仅本地保存。核验 ZIP 可读、CRC 正常、配置/SOUL/技能/数据库确实包含。对活动 SQLite 数据库另做一致性备份并执行 quick_check。备份失败或缺关键内容则停止变更。
3. **源码单独保存。** 原生完整备份不包含 hermes-agent 源码。记录 HEAD、工作树/暂存区差异和未跟踪文件清单；保存实际修改文件与补丁。保留旧版本代码或可恢复的 Git 对象、服务单元与依赖版本记录。不能把只保存 config 的备份当成完整升级回退点。
4. 对升级，先查目标版本是否已原生修复恢复窗口。优先在隔离代码目录和复制的测试配置中检查，不能用新版进程试写生产数据库。备份后才能执行已授权升级；不默认升级到未知版本。
5. 用户参数和技能通常位于源码仓库外，但配置迁移可能改变字段含义。逐项核对新版本读取路径并运行原生加载检查，不能只确认 YAML 文件存在。SOUL 保留原规则并避免重复追加。
6. 桌面更新可能使用 `--keep-stash`，把源码修改保存却不重新启用；`non_interactive_local_changes: discard` 会丢弃更新暂存。不要依赖自动恢复。补丁先做 apply --check；若上游已有等价修复，用行为测试证明后退休旧补丁并记录版本。冲突时按新源码适配，不能硬套旧文件。当前 `pre_update_backup: false` 意味着自动备份关闭，本技能要求的显式备份仍必须执行；需要改为 full 时作为独立参数变更先备份再验证。
7. **验收：** 运行审计、原生配置/SOUL/两个技能加载、恢复相关测试。核心恢复测试必须覆盖 24h 窗口内续跑、窗口外不续跑和重复回复防护。至少运行补丁中的测试文件，若新版本重构则定位等价测试，不能跳过后宣称通过。新版本原生实现可替换静态补丁检查，但必须记录理由和行为证据。
8. 用 `host-health-observation` 在全新目录复测：11 条全新样本、实际跨度至少 300 秒；进度和最终验收两条 cron 真正完成，finalizer=cron，后台进程退出，调度停用。恢复能力另用隔离后台进程和 checkpoint 测试；普通五分钟观察不能证明异常重启恢复。生产强杀演练不属于普通升级验证。
9. 失败则保留证据，修复或恢复备份。若升级迁移了数据库，回退代码也要协调恢复匹配的数据版本，不能仅切回旧提交。最终报告区分配置保留、补丁/上游修复生效、运行验收三项结果。
10. **实测通过再同步 GitHub。** 保存参数覆盖项、规则差异、补丁/退休记录、技能、版本和脱敏验收摘要；更新 SHA256SUMS。不提交整个 HERMES_HOME、数据库、会话、auth、.env、主机地址和原始私有日志。读取远端当前分支，保留他人变更，正常提交，不强推；回读文件逐项比对，报告提交链接。目标版本未通过验收时不覆盖已验证基准。

## Pitfalls

技能是操作流程和检查工具，不能自动拦截所有升级入口，也不能凭提示词替代源码补丁。GitHub 存档不是包含认证和运行状态的灾备。插件只有在当前版本提供稳定的对应扩展点时才适合替代核心修改，不使用运行时 monkey patch 掩盖升级冲突。

## Verification

检查审计各项、备份完整性、原生加载、恢复测试结果、全新真实任务结果，以及 GitHub 回读一致性。技能安装自身用审计与故障样本测试即可，不将安装技能误记为已验证一次新版升级。
