"""Manually scoped source rules and scenarios, not observed station incidents.

Table transcriptions were checked against PDF images. Expected choices below are
candidate annotations, not expert approval or current operating instructions.
"""

SOURCES = {
    'pcs974': {'path': 'rawdoc/姑苏站/产品说明书/PCS-974FG-D非电量及辅助保护装置说明书.pdf',
               'sha256': '3fc7e8d19c2067bb077f122a61f9d98e5dbfa0a35eacaac66f12ba448f098439'},
    'pcs9586': {'path': 'rawdoc/姑苏站/产品说明书/PCS-9586A阀控制单元说明书.pdf',
                'sha256': '26cbcbb315e894899255cedabbc41cc7f8b1e5ed6c49320f5a17b30838f1ff05'},
    'reg5': {'path': 'rawdoc/姑苏站/运行规程/规程五：±800kV姑苏换流站运行规程（设备异常与事故处理）.pdf',
             'sha256': '98d56bcdf4e494127af59c3e5cd4f535d0324d0f6832827eac0a6fb473074bd3'},
}

BLOCK_OPTIONS = {
    'blocks_device': '适用规则明确该项自检信号闭锁装置。',
    'does_not_block': '适用规则明确该项自检信号不闭锁装置；不代表其他保护或整个系统正常。',
    'insufficient_evidence': '型号、规则适用性或必要状态未确认，不能判定。',
}
VERIFY_SCOPE = '先核对装置完整型号、软件/手册版本及该规则是否适用。'

# These are per-alarm rows, not a universal mapping from alarm severity to health.
TABLE = [
    (1, '装置闭锁', True, '查看其它详细自检信', '检查其他详细自检信息，继续定位原因。', 'train'),
    (2, '板卡配置错误', True, '板卡配置错误', '查看板卡信息和异常信息，检查板卡安装及工作状态。', 'train'),
    (3, '定值超范围', True, '定值超出可整定的范围', '对照说明书核查定值范围，按规定处理超范围项。', 'test'),
    (4, '定值项变化报警', True, '定值项变化报警', '在定值设置中核对定值确认，并通知厂家处理。', 'calibration'),
    (5, '装置报警', False, '装置报警总信号', '查看其他详细报警信息，继续定位原因。', 'train'),
    (6, '通讯传动报警', False, '装置在通讯传动试验状', '针对已确认的通讯传动试验报警无需特别处理，核对试验结束后报警消失。', 'train'),
    (7, '定值区不一致', False, '定值区不一致', '检查区号开入与装置定值区号，核对两者一致性。', 'test'),
    (9, '版本校验出错', False, '投运时报警通知厂家', '投运阶段通知厂家处理；工程调试阶段按规定核对打包程序。', 'train'),
    (10, '对时异常', False, '对时模式是否一致', '检查时钟源、对时模式、接线和网络对时参数。', 'calibration'),
    (11, 'CT异常报警', False, 'CT 二次回路异常报警', '检查CT二次回路是否正确。', 'train'),
    (13, 'Bxx跳闸出口异常', True, '动电路损坏', '按手册板卡故障处理意见联系检修，安排板卡处理。', 'train'),
    (14, '投检修态', False, '确认是否人员主动操', '先确认投检修态是否为人员主动操作；非主动操作需检查开入回路。', 'test'),
]

def table_rules():
    result = []
    for number, alarm, blocks, anchor, action, split in TABLE:
        context = f'PCS-974FG-D说明书表7.2-1第{number}行：自检报警元件“{alarm}”；是否闭锁装置：{"是" if blocks else "否"}。处理意见：{action}'
        options = {'follow_alarm_guidance': action, 'verify_applicability': VERIFY_SCOPE,
                   'reset_first': '不核查原因，先简单复归或重启装置。',
                   'assume_healthy': '把该事件直接解释为全系统健康，停止检查。'}
        observed = f'装置为PCS-974FG-D，已确认所给手册适用。自检报告明确记录“{alarm}”当前出现。尚未完成对应检查。'
        if number == 6:
            observed += '已确认正在进行通讯传动试验。'
        if number == 7:
            observed += '已确认该装置采用表内华东地区定值区开入规则。'
        if number == 9:
            observed += '装置已投运，当前不处于工程调试阶段。'
        result.append({
            'id': f'pcs974-table-{number:02}', 'split': split, 'source': 'pcs974',
            'pages': [58, 59], 'locator': f'表7.2-1第{number}行；7.4节',
            'anchors': {58: [anchor], 59: ['不要轻易按保护大屏上的复归按钮']},
            'rule_context': context, 'options': options, 'block_question': True,
            'cases': [
                {'name': 'identified', 'observations': observed,
                 'gold': {'blocking': 'blocks_device' if blocks else 'does_not_block',
                          'next_check': 'follow_alarm_guidance'},
                 'reason': f'适用性已确认，按表7.2-1第{number}行；不推断全系统状态。'},
                {'name': 'unknown-device',
                 'observations': f'事件摘录为“{alarm}”出现。来源装置的完整型号及手册版本尚未核对，无法确认是否为PCS-974FG-D。',
                 'gold': {'blocking': 'insufficient_evidence', 'next_check': 'verify_applicability'},
                 'reason': '证据适用条件未确认；不能把特定型号的规则泛化到未知装置。'},
            ],
            'annotation_note': '闭锁是表中该项信号的作用，不等同于系统诊断；处理选项为规则约束下的核查建议。',
        })
    return result

def branch_rule(id, split, source, pages, locator, anchors, context, options, cases):
    return {'id': id, 'split': split, 'source': source, 'pages': pages, 'locator': locator,
            'anchors': anchors, 'rule_context': context, 'options': options,
            'block_question': False,
            'cases': [{'name': name, 'observations': observations, 'gold': {'next_check': gold}, 'reason': reason}
                      for name, observations, gold, reason in cases],
            'annotation_note': '按条件分支选择当前尚未完成的核查；属于规则派生模拟场景。'}

def rules():
    return table_rules() + [
        branch_rule('pcs9586-dsp', 'train', 'pcs9586', [29], '7.1.2装置功能报警，DSP板卡功能报警',
            {29: ['投入阀测试模式时', '运行情况下', '将故障系统切为非值班状态']},
            'PCS-9586A的DSP板卡功能报警：将故障系统切为非值班状态后排查。投入阀测试模式时该报警为正常现象，不需处理；运行情况下需上招装置运行日志，并按程序重启复位。',
            {'confirm_mode': '先核对阀测试模式是否投入。', 'expected_test': '按已确认的阀测试模式解释该报警，不增加故障处理。',
             'collect_logs': '上招装置运行日志，分析运行情况下的软件异常。', 'replace_fiber': '直接更换对时光纤。'},
            [
                ('test', '已确认PCS-9586A手册适用；故障系统已处于非值班状态。DSP板卡功能报警出现，阀测试模式明确已投入。', 'expected_test', '原文明确测试模式分支。'),
                ('running', '已确认PCS-9586A手册适用；故障系统已切为非值班。报警发生于运行情况下，阀测试模式未投入；运行日志尚未上招。', 'collect_logs', '运行分支需查看运行日志，不能套用测试模式无需处理。'),
                ('unknown-mode', '已确认PCS-9586A手册适用；故障系统已处于非值班。DSP板卡功能报警出现，阀测试模式投入状态未知。', 'confirm_mode', '区分两个分支所需模式信息缺失。'),
            ]),
        branch_rule('pcs9586-power', 'test', 'pcs9586', [30], '7.1.2第6项装置A(B)路电源故障',
            {30: ['屏柜电源故障、空开故障或 NR2304 板卡硬件故障', '若端子电压', '将故障系统切为非值班状态']},
            'PCS-9586A装置A(B)路电源故障可能由屏柜电源、空开或NR2304板卡故障引起。将故障系统切为非值班后，先查屏柜电源与空开，再查NR2304端子A/B两路电源输入；端子电压正常时，按板卡故障处理。',
            {'confirm_role': '先核对故障系统是否已满足非值班排查前提。', 'inspect_supply': '检查屏柜电源、空开是否正常。',
             'measure_terminals': '检查NR2304端子A/B两路输入电压。', 'board_followup': '依据端子电压正常的结果，联系检修按NR2304板卡故障处理。'},
            [
                ('role-missing', '已确认PCS-9586A手册适用。A路电源故障出现；故障系统当前是否非值班尚未确认，尚未检查电源。', 'confirm_role', '必须先确认非值班前提。'),
                ('supply-unchecked', '已确认PCS-9586A手册适用，故障系统为非值班；A路电源故障持续。屏柜电源、空开及端子电压均未检查。', 'inspect_supply', '从原文第一步开始，不能直接归因板卡。'),
                ('terminals-unchecked', '已确认PCS-9586A手册适用，故障系统为非值班；电源故障持续。已检查屏柜电源及空开正常，NR2304端子输入电压尚未测量。', 'measure_terminals', '前一步完成，继续查端子输入。'),
                ('terminals-normal', '已确认PCS-9586A手册适用，故障系统为非值班；电源故障持续。屏柜电源、空开及NR2304端子A/B两路输入电压都已检查正常。', 'board_followup', '端子电压正常是进一步指向板卡的必要观测。'),
            ]),
        branch_rule('pcs9586-process', 'train', 'pcs9586', [30], '7.1.2第7项进程退出',
            {30: ['短时间内报警消失', '长时间报警不消失']},
            'PCS-9586A进程退出：故障系统切为非值班后排查。进程退出后短时间内报警消失，说明进程重启成功，不需处理；长时间不消失，需上招运行日志分析并按程序重启。原文未给出短/长时间的数值界限。',
            {'check_recovery': '先核对报警是否恢复及持续情况，不能凭空设定秒数阈值。',
             'recovered': '按已确认的短时间恢复分支，不增加该项故障处理。',
             'collect_logs': '上招运行日志，分析长期未恢复的退出事件。',
             'replace_board': '不查看恢复情况，直接认定板卡硬件损坏。'},
            [
                ('short', 'PCS-9586A适用性已确认，故障系统为非值班。进程退出后报警已经消失，现场按该条款确认属于短时间恢复，进程重启成功。', 'recovered', '沿用已确认的原文定性条件，不虚构数字阈值。'),
                ('long', 'PCS-9586A适用性已确认，故障系统为非值班。现场按该条款确认进程退出后报警长时间不消失，运行日志未上招。', 'collect_logs', '长时间不恢复分支。'),
                ('unknown-duration', 'PCS-9586A适用性已确认，故障系统为非值班。仅有一次进程退出事件，没有恢复事件或持续情况记录。', 'check_recovery', '缺少区分分支的恢复情况。'),
            ]),
        branch_rule('pcs9586-time', 'calibration', 'pcs9586', [30], '7.1.2第8项对时故障',
            {30: ['若站内多台装置同时报对时故障', '若单台装置报对时故障']},
            'PCS-9586A对时故障：多台装置同时报对时故障，基本可判断对时源故障；只有单台报对时故障，需要排除该装置对时光纤故障。多台同时故障的描述是优先排查线索，不保证唯一根因。',
            {'inspect_source': '优先核查共同对时源；暂不宣称唯一根因已确认。',
             'inspect_fiber': '先排查该单台装置的对时光纤。',
             'check_extent': '先核查是否还有其他装置同时报对时故障。',
             'replace_all': '直接更换站内所有装置板卡。'},
            [
                ('multiple', '已确认PCS-9586A手册适用；已核对站内多台装置同时报告对时故障。对时源尚未检查。', 'inspect_source', '多台同时故障指向共同对时源的排查优先级。'),
                ('single', '已确认PCS-9586A手册适用；核查后只有该单台装置报告对时故障，其他装置对时正常；该装置对时光纤未检查。', 'inspect_fiber', '单台故障分支。'),
                ('unknown-extent', '已确认PCS-9586A手册适用；收到该装置对时故障，尚不知道其他装置是否也在同一时刻报警。', 'check_extent', '先补齐故障范围，不能直接认定源故障。'),
            ]),
        branch_rule('reg5-sf6', 'train', 'reg5', [36], '6.1.2.1、6.1.2.4',
            {36: ['现场检查交流滤波器断路器三相 SF6 压力', '如果压力正常', '检查交流滤波器备用情况']},
            '规程五6.1：交流滤波器断路器SF6压力低报警后，现场检查三相压力；现场压力正常时通知检修人员进一步检查。后续隔离方案需检查备用滤波器情况；若备用能力不能满足大组退出后的运行条件，经领导批准申请调度降低直流功率。',
            {'check_pressure': '现场核查该交流滤波器断路器三相SF6压力。',
             'maintenance_check': '按现场压力正常分支，通知检修进一步检查。',
             'check_reserve': '核查备用滤波器能否满足大组退出后的直流运行条件。',
             'reduce_without_approval': '不核对备用情况和批准条件，直接降低直流功率。'},
            [
                ('unverified-pressure', '已确认规程五适用于本场景。交流滤波器断路器SF6压力低报警出现；没有现场三相压力数据，尚未开展该项检查。', 'check_pressure', '报警本身不能替代现场压力。'),
                ('normal-pressure', '已确认规程五适用。SF6压力低报警出现后，现场检查该交流滤波器断路器三相压力均正常；尚未联系检修。', 'maintenance_check', '原文压力正常分支。'),
                ('reserve-unknown', '已确认规程五适用。前序压力检查及故障分支处理已完成，现在进入大组隔离方案的备用能力核查阶段；备用滤波器能否满足大组退出后的运行条件尚未确定。', 'check_reserve', '不越过备用能力及批准条件直接降功率。'),
            ]),
        branch_rule('reg5-spring', 'test', 'reg5', [36], '6.2.2.1、6.2.2.2',
            {36: ['现场检查交流滤波器断路器弹簧储能指示', '储能监视回路是否正常', '操作机构电源是否正常']},
            '规程五6.2交流滤波器断路器储能不正常：先检查弹簧储能指示是否正常；若正常，联系检修检查储能监视回路。另需检查操作机构电源；电源开关跳开时只允许试合一次，试合不成功联系检修。',
            {'check_spring': '现场检查该断路器弹簧储能指示是否处于正常范围。',
             'check_monitor': '联系检修检查弹簧储能监视回路。',
             'contact_maintenance': '试合一次仍不成功，联系检修处理。',
             'retry_repeatedly': '继续反复试合电源开关直至成功。'},
            [
                ('unchecked', '已确认规程五适用。本事件已归入交流滤波器断路器储能不正常预案；只收到断路器告警，弹簧储能指示尚未现场检查。', 'check_spring', '第一步核实指示。'),
                ('normal', '已确认规程五适用，正在处理该断路器储能异常。现场弹簧储能指示检查正常，监视回路尚未检查。', 'check_monitor', '指示正常分支指向监视回路。'),
                ('retry-failed', '已确认规程五适用。操作机构电源开关跳开，已按规定试合一次但未成功；没有进行第二次试合。', 'contact_maintenance', '原文限制一次，不能训练为重复试合。'),
            ]),
    ]
