# 第五批逐题文档忠实性复核

本批7个业务单元、53题、90个事实规划条目，主题为双极/单极/单换流器闭锁、直流回降、滤波器最小组数、线路再启动和线路保护跳闸。累计候选将另行汇总；不导出训练。

## 版本修订

v1建立53题和87个事实条目。v2补入回线转换前后两端核查、共用接地极电流、备用锁定后的重新核查、功率计划越限检查，并将主控站立即控功率置于方式申请前。v3根据事实/语义复核删除超出原文的禁止句、无证据附录编号和申请对象判断；保留来源缺口作为题目结论。

第五分册9.6.2.7关于频繁全压再启动成功后的降压建议，与第三分册D.3“建苏直流无降压运行方式”存在适用边界张力。两处证据并列保存，题目只说明未闭合边界，不生成降压运行步骤。线路故障且另一极过负荷时，带线路OLT条件不具备或试验失败的立即限流要求也保留；完整OLT/复役票列为缺口。

## 模型调用与来源复核

本版重新执行事实核验、53题盲复答和7个单元语义审阅；原始v1/v2响应保留，v3不复用答案盲复答。模型意见只作审阅线索；逐题来源绑定由Codex按实际source_quote和完整上下文完成，未经业务专家审核。

模型建议统计：{"pass": 48, "revise": 3, "hold": 2}；事实意见：{"supported": 88, "ambiguous": 2}。模型提出的异议和格式问题没有被删除，均在source_reviews.json中保留。

## 覆盖边界

本批另行按完整提取单元核对，覆盖台账不把短引文重叠当作整条覆盖；不同册日期、旧Word自动编号、图表数值和操作票缺口继续保留。

## 逐题核对


### bipole_block/whole

逐项核对3.2全部现象和处理，双极闭锁不自动套用3.1全站失电黑启动。 初报条件及两阶段另对照2.2.4。

模型建议：pass；候选摘要：`605876da3df631e5350cac89cfef06a9290d45471cff44ff3d7a0ff2648805b1`。

### bipole_block/filters_assumed

现象和实际退出检查分别有条款，未删掉检查。

3.2.2.2在现象之后单列“检查交流滤波器是否全部退出运行”；最终题只说应按该检查核实，没有把引申禁止句写入事实。
模型建议：pass；候选摘要：`d49fb68637c37849e0804476e83f7907356a67438b6c2c8b56d64e93fcd5f28f`。

### bipole_block/dc_power_only

保留交流潮流/电压/频率及保护范围一次设备、录波汇报，不用功率零证明全站失电。

模型建议/格式意见原样保留在model_semantic_reviews及本记录字段；本条最终状态仅表示Codex按列示文档完成来源核对，不代表业务专家认证。
模型建议：revise；候选摘要：`94b5e380e7d46872d43388f3a82f84d18f13d31b660c1776d0dcc060cd29dd55`。

### bipole_block/not_single_pole

双极均闭锁隔离与单极预案另一极分支前提不同；不推断实际功率仍能转移。

模型建议：pass；候选摘要：`9c357bf5b1e0fe9410d13080b344eee2861c2da640d835229e8065cfe16a9fe1`。

### bipole_block/restore_without_repair

3.2终点是隔离转检修而不是复役许可；缺口只限定本预案。

3.2.2.5明确处置路径止于隔离、转检修、通知检修；答案把缺失的复役流程标成来源范围缺口，没有伪造许可。
模型建议：pass；候选摘要：`4b52671f2f56b20cfb61f800fd4da69f730acf4e0bc503306a088e7e8b9843f0`。

### bipole_block/blackstart_not_implied

3.2描述双极状态不证明全站失电，3.1是不同触发条件；先核实际状态。

模型建议：pass；候选摘要：`b5cefd539a280b29b05d463b1bdc087cf805cb2c4c96a5b7c04edd7e5e661090`。

### pole_block_and_ground_current/whole

3.3全条与第三分册主控站紧急功率控制、入地电流总要求及两故障原因分支共同核对，未把2小时作为立即分支的等待授权。 初报条件及两阶段另对照2.2.4。 v2把主控站立即控功率置于方式申请前，并明确编号不产生等待授权。 v2保留单极闭锁另极过负荷背景；完整题增加转换前共用电流、两站设备及远控检查和失败返回条件。

cp47550整段的2小时总要求、非线路/线路分支及cp42776等转换前后条件均逐项核对；“立即”是原文分支用语，未被2小时改写。
模型对部分原始事实的“ambiguous/unsupported”意见已保留；Codex以v3收紧后的事实文本逐条回到对应source_quote和上下文核对，删除了越出原文的附加限制或出处，保留有原文依据的条件关系。
模型建议/格式意见原样保留在model_semantic_reviews及本记录字段；本条最终状态仅表示Codex按列示文档完成来源核对，不代表业务专家认证。
模型建议：revise；候选摘要：`98dc89f41d3f6443c3870345c5a064211e1e2c5188410e5345f6ea27844aab1e`。

### pole_block_and_ground_current/transfer_is_safe

功率转移不保证无过负荷，不能删掉直流功率、滤波器及接地极电流检查。

模型建议：pass；候选摘要：`058a4b47c0b0bd4e6f343d404564f6af25723ed078e99393d7c2ead764959cd6`。

### pole_block_and_ground_current/master_waits

主控站明确权限与立即功率控制、立即汇报同时保留，方式申请不能延迟该专项动作。

模型建议：pass；候选摘要：`a5408e257ae9739551444738cc75186586c0348059103a4eb98909ca18360a36`。

### pole_block_and_ground_current/unknown_master

授权主体必须核实，主控轮换及故障临时转换上下文不能按站名直接认定；不声称非主控一律不能按国调命令操作。

模型建议：pass；候选摘要：`29a8d9a828d9da3ce8de0b6a75d80fda6ac71975a09e6bddfca7787844c58297`。

### pole_block_and_ground_current/two_hours_not_wait

核实当前非线路故障且不具备转换条件，选择立即控制到安全限值或以下；总时限不覆盖更具体立即要求。 v2保留单极闭锁另极过负荷背景；完整题增加转换前共用电流、两站设备及远控检查和失败返回条件。

模型建议：pass；候选摘要：`26e82fa2fc1e5d2f4e548087cc543f6da10445bdd5f95b4da5cdb4ea155b8884`。

### pole_block_and_ground_current/line_fault_olt

OLT成功恢复与不具备/失败立即限流分支分开；没有给失败后继续试验次数。 v2保留单极闭锁另极过负荷背景；完整题增加转换前共用电流、两站设备及远控检查和失败返回条件。

模型建议：pass；候选摘要：`af6c27e65fa3af847fd20916bb23e722f376b7f1ea5d55bc4ac554e16318490b`。

### pole_block_and_ground_current/changed_failure_cause

保留合成历史中的非线路故障，最新信息改变故障原因和条件；不能不核条件就沿用旧方案。 v2保留单极闭锁另极过负荷背景；完整题增加转换前共用电流、两站设备及远控检查和失败返回条件。

模型建议：pass；候选摘要：`d5a1a3f3f906dc770f36e7613a9af8092527b5d5330793275981a2b27ecf9c90`。

### pole_block_and_ground_current/maximum_power_number

按电压当前限额而非固定功率，原文只有OLT条件引用，不能补参数表或顺控票。

模型建议/格式意见原样保留在model_semantic_reviews及本记录字段；本条最终状态仅表示Codex按列示文档完成来源核对，不代表业务专家认证。
模型建议：hold；候选摘要：`46faac8b8a8e5a6067108f52a7a52f1237dead94e5c58522e715c7f45a2d458e`。

### single_converter_block/whole

核对3.4全条，限定单换流器状态，不套3.2全部交流滤波器退出；第三分册主控站紧急功率条款共同适用。 初报条件及两阶段另对照2.2.4。 v2把主控站立即控功率置于方式申请前，并明确编号不产生等待授权。

模型建议：pass；候选摘要：`ca56aaa20ad6c1e394087a6c16a850c4e817e7c099f76cee96f25a7224f83fde`。

### single_converter_block/only_other_pole

不能只因另一极正常省略本极另一台换流器对象。

模型建议：pass；候选摘要：`45dcadc955d1abdc1be2f09d4ef2a0e0c44a13d64d1a2461b9b6ab083843a5ae`。

### single_converter_block/all_filters_out

故障对象单个，3.4只要求查滤波器退出情况，未说全部退出；用3.2完整上下文对照。

模型建议：pass；候选摘要：`8b993eddfd6c039fc35874a136e1deaa4aac490bd0118f8a17a1386c0f628d99`。

### single_converter_block/master_overload

第三分册明确单换流器闭锁触发同一紧急授权，保留当前电压限额与立即汇报。

模型建议：pass；候选摘要：`ba74d0552d84dc7524e774aaf8935d67c1feace7487e8f8ed00abd560b236fd9`。

### single_converter_block/maintenance_scope

申请范围按具体故障换流器，没有扩张其他设备。

模型建议：pass；候选摘要：`5063cac67f784919d8e22141690384824e637a31e9d8115f5e4218222ed00f11`。

### single_converter_block/restore_after_isolation

本节到检修处理止，不含故障换流器复役完整票；在线投入条件与验收要另查。

模型建议：pass；候选摘要：`6771a52a1a04ce66c3c254004b1686de6ce98c68cae670787eabbff5922bee16`。

### dc_power_rundown/whole

3.5全条及第三分册可用、投切禁令、放电时间、6.15小组投入申请共同核对，不把“有备用”当作立即投入许可。 初报条件及两阶段另对照2.2.4。 v2把相关申请及前提置于恢复建议之前，补第三分册cp50255功率计划随限制变化检查。

3.5.2.5只在备用投入后申请恢复功率；小组保护条款的备用投入申请和3.5的恢复功率事项分开表述，最终答案未宣称原文未给出的受理对象。
模型建议/格式意见原样保留在model_semantic_reviews及本记录字段；本条最终状态仅表示Codex按列示文档完成来源核对，不代表业务专家认证。
模型建议：revise；候选摘要：`9f3c3ded88380cb6704f97e71f0fc7313eb6c1efebb30bbb48594d052e9aa3fb`。

### dc_power_rundown/recover_before_filter

题设已经确认可用和时段；仍按先投入后汇报申请恢复功率，小组保护需求不足的投入申请另保留。 v2把相关申请及前提置于恢复建议之前，补第三分册cp50255功率计划随限制变化检查。

模型建议：pass；候选摘要：`350e1968404f074599c65aba6e7d1b692ffc3d0ff7ace31a662b3aec7a7f65b6`。

### dc_power_rundown/hot_standby_five_min

题设5min小于放电及退出时间条件，全部可用条件列明，不编造安全裕量。

模型建议：pass；候选摘要：`d63fd2937a6b351c09dc131679e0b2056725e47e0692986e5cb9c788d85c45b4`。

### dc_power_rundown/still_ramping

手动投切禁令覆盖功率升降阶段，3.5先投后恢复不取消该状态限制。

模型建议：pass；候选摘要：`0175df57904d5287674de596af14598158b55c087b3e2d61e9f07b8d1763acf3`。

### dc_power_rundown/no_available_backup

无可用备用走申请降低至报警复归分支，不沿用可用时的恢复分支。

模型建议：pass；候选摘要：`c00c013157312da035e6db67b7938cb6421f19c6cb6be1799dc76770c3cae06a`。

### dc_power_rundown/water_not_confirmed

高温先查水冷，异常再相应预案；不能跳过诊断指定主泵。

模型建议：pass；候选摘要：`adf171b8e5d868996980b45056bc0e3a3220875d1a84e56ede56f3c5e56280cb`。

### dc_power_rundown/ground_not_filter

原因不同分支不同，不能用滤波器投入掩盖接地极过负荷。

模型建议：pass；候选摘要：`b1c7d1c6c2270f2a3019c52e6dacf0b9ebd30529044aa461a010cca1bb6b919b`。

### dc_power_rundown/backup_failed_followup

合成历史先给可用前提，最新锁定信号撤销可用性；改用不可用分支，不忽略最新设备状态。 v2把相关申请及前提置于恢复建议之前，补第三分册cp50255功率计划随限制变化检查。 这组锁定不证明全站无其他备用，显式区分重新核其他备用与确无备用分支。

模型建议：pass；候选摘要：`08e525bdb67282945fb23b20276610373f06dc9add68b0012c385428be4be51c`。

### dc_power_rundown/nonmaster_reports

非主控条件及及时通知主控站完整，没有用汇报领导代替站间通知。

模型建议：pass；候选摘要：`25c65a29cd636308e4ca2031b3ba81ae7a7268af065429158a3aa4089c3da2ac`。

### minimum_filter_and_rpc/whole

6.11全条及第三分册两个模式段、ABS/Min定义和替换原则逐项对照，不合并两个最小概念。 初报条件及两阶段另对照2.2.4。 v2显式把Min通用能力放回当前RPC模式约束，手动操作仍服从可用性、时间、投切禁令和调度申请。

ABS和Min按第三分册原文区分；RPC手动时Min/Q/U由运行人员承担，ABS等高优先级仍自动。未保留无证据的附录编号。
模型对部分原始事实的“ambiguous/unsupported”意见已保留；Codex以v3收紧后的事实文本逐条回到对应source_quote和上下文核对，删除了越出原文的附加限制或出处，保留有原文依据的条件关系。
模型建议：pass；候选摘要：`ad453a63f4a02313ebdea132b2a89d97bdd150e89fc481d4002dc9f70df9d41b`。

### minimum_filter_and_rpc/min_equals_abs

Min与ABS目的条件后果不同，不由近似报警名推唯一停运结论。

模型对部分原始事实的“ambiguous/unsupported”意见已保留；Codex以v3收紧后的事实文本逐条回到对应source_quote和上下文核对，删除了越出原文的附加限制或出处，保留有原文依据的条件关系。
模型建议：pass；候选摘要：`e7f76d9a22e0afafdb8d806fcd42f01bc8b7434ac0db821b544e8ea4ffdbda11`。

### minimum_filter_and_rpc/manual_no_protection

仍自动高优先级与人工承担低优先级明确区分，不把模块失效当人工操作无限授权。

模型建议：pass；候选摘要：`c04caa58c409916f38d81f344af1c646260dd2144f7290a297856475f1a24679`。

### minimum_filter_and_rpc/all_checks_normal

均正常条件和必要时调度申请保留，另外投切禁令和可用时间在上下文限制，不取消。

模型建议：pass；候选摘要：`8a5a93ebf9ebcbe74335d0592052f9c5c3c9d1ddcc209e168cb2fe47dd493f06`。

### minimum_filter_and_rpc/no_backup

无备用加快检修恢复备用、监视报告完整，没有固定MW；不把3.5特定回降原因分支当作6.11统一动作。

模型建议：pass；候选摘要：`dda7fcc4ee930f40fdada6928032215cb7d081d90c459ba0380931f8c5fa80b7`。

### minimum_filter_and_rpc/one_or_five_seconds

按ABS/Min两需求分支1s和5s，另一组替代不等于原故障组再送。

模型建议/格式意见原样保留在model_semantic_reviews及本记录字段；本条最终状态仅表示Codex按列示文档完成来源核对，不代表业务专家认证。
模型建议：hold；候选摘要：`3c02d1ee975ff2d1119e65efcf2b73f42c42514be6c551f8714c01520dea74e8`。

### minimum_filter_and_rpc/fixed_groups_delay

Min取决四参数，ABS配置表另见附件且未解释；预设延时未给秒数，不猜表格数字。

模型建议：pass；候选摘要：`582948a5974d7f1fe41200f211ea2403306787a61cb4f33655a461b8a4f6f815`。

### minimum_filter_and_rpc/manual_changed

最新模式限制覆盖原来未给模式的通用说明；人工承担与仍有效ABS不混。

模型建议：pass；候选摘要：`fc267164d8132f04ed57b5f0816ff90d1b55801cf1b3805f54749037b4be0496`。

### dc_line_restart_boundary/whole

9.6全条及第三分册再启动次数/角色/带电作业、对站测距及D.3共读；回答指出真实适用疑点，不给确定降压步骤。 初报条件及两阶段另对照2.2.4。

第五分册频繁全压再启动成功后的降压建议与第三分册D.3无降压方式并列保留，最终只回答适用边界缺口，不生成降压操作步骤。
模型建议：pass；候选摘要：`770464f62b0f379fd368dff4af0e0d3e9191d55dd6e67b2b8bfb655f78c4414b`。

### dc_line_restart_boundary/restart_success_is_end

再启动成功不免除预案核查及报告，避免成功样本被当完整故障终点。

模型建议：pass；候选摘要：`a55d1a6e074d7cb2caa3dc64e257e5dd467ff41dba8912886e0e023773d36022`。

### dc_line_restart_boundary/communication_attempts

通信故障条件选择原压1次、降压0次，故障仍在闭锁，不把正常2次用于异常模式。

模型建议：pass；候选摘要：`802223ce794fa9bec34f2320a5651720fbd57349ce804b953d9fce741574e16d`。

### dc_line_restart_boundary/live_work_one_function

两项分别退出，国调许可和整流站停用主体完整；不把带电作业条件当作免许可。

模型建议：pass；候选摘要：`09ab04eea72f1c472b8003e0b7e406164bd21ed8c627d4f7b97f4bf734e4d127`。

### dc_line_restart_boundary/local_disable

国调许可、由整流站停用两个不同角色限制都保留。

模型建议：pass；候选摘要：`d42143d86214a01b72db2a8451224612e9a35aff2a4f809b5c3d311daa46f83a`。

### dc_line_restart_boundary/obvious_equipment

明确异常选择领导批准后申请停极、必要时调整功率、隔离检修；不是只成功监視。

模型建议：pass；候选摘要：`3632a93f229c92b2cb18c584203b16f48ec3777e17c7857f99ef652818d7c869`。

### dc_line_restart_boundary/reduced_voltage_conflict

明确两处实际文字与降压重启0次是三个不同问题，未擅自确认现行版本或设备能力。

模型建议：pass；候选摘要：`3647cc47c2ca75a7df25addd33eb99147ad5f7110f5b1c901627196fddaf81ab`。

### dc_line_restart_boundary/measurement_peer

本站保护/安控录波与测距和对站核对、调度报告完整。

模型建议：pass；候选摘要：`e65b0b0d009cdcdf336d51290e2569caebf5e6d00307a9012a5559698514c327`。

### dc_line_restart_boundary/frequency_numeric

本节无频繁时间次数判据，跨册降压边界未解决，两种缺口分别指出。

模型建议：pass；候选摘要：`10cae3b6b279c5d1e24f033b474019783dbe707c947a1985141a02b15aded1cd`。

### dc_line_protection_trip/whole

9.7全部现象与处理及第三分册线路故障入地电流分支共读，没有由监控隔离或再启动成功规则给出立即恢复。 初报条件及两阶段另对照2.2.4。 v2显式限定引用的单极闭锁另极过负荷背景，保留立即动作及试验成功才恢复。

9.7现象、立即汇报、现场检查、记录传真、通知检修及第三分册线路故障且另一极过负荷分支分别核对；OLT试验票和复役条件未补造。
模型建议：pass；候选摘要：`c33918be40dec569b3eb5f1e87ec64e281dc1f3557ceb028244bf6245699eb1e`。

### dc_line_protection_trip/signal_isolation_enough

现象与现场核实有分别编号，两个检查对象保留。

模型建议：pass；候选摘要：`7e52d1385ff4782e3f4a3bf59c750e13de64696a70b285055d730f869340d483`。

### dc_line_protection_trip/restart_success_branch

当前9.7已跳闸隔离，不按9.6可能成功情形抹掉故障状态。

模型建议：pass；候选摘要：`a14aac15f7647b73d462eed0ed18d8ed21db2817ea416443107dfd1dbabd81e5`。

### dc_line_protection_trip/other_pole_check

另一极运行及过负荷、必要时方式申请完整。

模型建议：pass；候选摘要：`6b7295e4c94b39901a52f097f8ec0b06a89cd7655a4f26bfabfe8a9ced8ac553`。

### dc_line_protection_trip/olt_failure

失败立即限流3000A或以下，不走成功恢复；不将2小时泛化为可等待授权。 v2显式限定引用的单极闭锁另极过负荷背景，保留立即动作及试验成功才恢复。

模型建议：pass；候选摘要：`54f4d12cc599debcdc3a6fd75f67f4ecfc49e4a3529ae6accf31582181a3649c`。

### dc_line_protection_trip/return_to_service_ticket

预案没有完整票或解除锁定流程，资料缺口限定当前条款，不断言所有rawdoc均没有。 v2显式限定引用的单极闭锁另极过负荷背景，保留立即动作及试验成功才恢复。

模型建议：pass；候选摘要：`233e67ad8c8edd1f74300af94fbfb38e94978dd1393e3cc34f220ddec365eeca`。

### dc_line_protection_trip/submission

初报、现场隔离及滤波器、录波事件传真、检修检查通知齐全。

模型建议：pass；候选摘要：`fba4d65c69f841d7152335937a8deede3b6825af322895a1f4535a60508d03ec`。

