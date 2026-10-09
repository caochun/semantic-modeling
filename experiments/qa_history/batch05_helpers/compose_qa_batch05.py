import json
import re
from pathlib import Path

ROOT = Path('/Users/chun/Develop/semantic-modeling')
units = {u['id']:u for u in json.loads((ROOT/'.semantic/qa_coverage_20261008_batch04/source_units.json').read_text())}
PDF, WORD = 'd_98d56bcdf4e49412', 'd_4b964d3ff677714b'
sources = {p.stem:json.loads(p.read_text()) for p in (ROOT/'.semantic/qa_operations_20261008/sources').glob('*.json')}
chains, notes, coverage_notes = [], {}, {}

def uid(key):return WORD+'/'+key if key.startswith('cp') else PDF+'/clause/'+key
def selectors(keys):return [{k:s[k] for k in ['source_id','block_id','start','end']} for key in keys for s in units[uid(key)]['spans']]
def block_selectors(sid,keys):return [{'source_id':sid,'block_id':key,'start':0,'end':len(next(b['text'] for b in sources[sid]['blocks'] if b['id']==key))} for key in keys]
def section(*prefixes):return [u['number'] for u in units.values() if u['source_id']==PDF and any(u.get('number','')==p or u.get('number','').startswith(p+'.') for p in prefixes)]
COMMON = selectors(section('2.1','2.2.5')+['2.2.4']) + block_selectors(PDF,['page2']) + block_selectors(WORD,['cp74_93','cp94_98','cp99_112','cp113_121','cp290_297','cp2275_2313','cp20268_20275','cp21938_21993','cp21994_22015','cp22016_22042','cp22043_22066','cp22067_22091'])

def chain(cid,title,scope,prefixes,context_keys=(),context_blocks=()):
 keys=section(*prefixes)
 c={'id':cid,'title':title,'scope':sources[PDF]['scope']+' '+scope,
    'source_unit_ids':[uid(k) for k in keys],'source_spans':selectors(keys),
    'context_source_spans':COMMON+selectors(context_keys)+block_selectors(WORD,context_blocks),
    'facts':[],'questions':[],'order_edges':[],'branches':[]}
 chains.append(c);return c

def f(c,fid,key,text,rationale):
 u=units[uid(key)]
 c['facts'].append({'id':fid,'quote':re.sub(r'\s+','',u['text'])[:44],'text':text,'source_unit_id':u['id']})
 if u['id'] not in c['source_unit_ids']:
  c['source_unit_ids'].append(u['id']);c['source_spans']+=selectors([key])
 coverage_notes.setdefault(u['id'],[]).append(rationale)

def q(c,key,typ,question,required,note,lead='',tail='',history=None,standalone=None):
 item={'key':key,'type':typ,'question':question,'required':required}
 if lead:item['lead']=lead
 if tail:item['tail']=tail
 if history:item['history']=history
 if standalone:item['standalone_question']=standalone
 c['questions'].append(item);notes[c['id']+'/'+key]=note

def whole(c,question,note,lead='不同状态分别选择分支，以下概括所问专项条款。'):
 lead+=' 国调管辖系统事故须立即首报概况，查明后尽快详细汇报，查因不能延迟初报。'
 q(c,'whole','full_chain',question,[x['id'] for x in c['facts']],note+' 初报条件及两阶段另对照2.2.4。',lead)

def protection_intro(c,prefix):
 f(c,'signal',prefix+'.1.1','OWS出现相关保护动作信号，故障录波器启动。','OWS保护信号及故障录波启动两项完整。')

c=chain('bipole_block','双极闭锁：交流系统核查、滤波器退出与检修','3.2',['3.2'],context_keys=section('3.1','3.3'))
protection_intro(c,'3.2')
f(c,'bipole_block','3.2.1.2','双极闭锁。','双极对象保留，不改为只一个换流器或单极。')
f(c,'ac_locked','3.2.1.3','所有换流器交流侧开关跳闸并锁定。','所有换流器、交流侧开关、跳闸及锁定均有。')
f(c,'isolated','3.2.1.4','双极均退至隔离状态。','双极均隔离状态，不把闭锁与现场确认混为一项。')
f(c,'filters_out','3.2.1.5','交流滤波器全部退出运行。','现象为全部退出，后续仍需核实实际退出。')
f(c,'ac_limits','3.2.2.1','检查交流系统潮流、电压和频率是否越限，汇报调度及领导。','潮流、电压、频率三对象及调度领导均保留。')
f(c,'verify_filters','3.2.2.2','检查交流滤波器是否全部退出运行，不能仅凭双极闭锁信号认为退出核查已完成。','是否全部退出的检查要求完整，未将现象描述当作确认结果。')
f(c,'primary_report','3.2.2.3','检查一次设备状态变化及动作保护范围内设备是否正常，及时向调度和领导详细汇报。','一次状态、保护范围设备、及时详细汇报两对象齐全。')
f(c,'recording','3.2.2.4','查看保护装置动作，整理故障录波，及时传真至调度和相关部门。','保护动作、录波、及时传真及接收对象完整。')
f(c,'maintenance','3.2.2.5','将双极（故障设备）隔离并转检修，通知检修人员处理；本条没有给出故障设备未经检修即可恢复的许可。','隔离、转检修、检修人员对象完整，没有补故障设备恢复动作。')
whole(c,'双极闭锁后，交流开关、极状态和滤波器有哪些现象，应怎样核查、汇报并转检修？','逐项核对3.2全部现象和处理，双极闭锁不自动套用3.1全站失电黑启动。')
q(c,'filters_assumed','false_premise','双极已经闭锁，是否可以认为滤波器必然全退，不再核查？',['filters_out','verify_filters'],'现象和实际退出检查分别有条款，未删掉检查。','不能省略实际退出情况核查。')
q(c,'dc_power_only','preconditions','直流功率已消失，是否只检查直流侧就够了，交流侧还需检查什么？',['ac_limits','primary_report','recording'],'保留交流潮流/电压/频率及保护范围一次设备、录波汇报，不用功率零证明全站失电。','只看直流功率还不够。')
q(c,'not_single_pole','source_scope','这是双极闭锁，可以直接按“另一极继续承担功率”的单极闭锁分支处理吗？',['bipole_block','isolated','ac_locked'],'双极均闭锁隔离与单极预案另一极分支前提不同；不推断实际功率仍能转移。','不能直接套用单极预案的另一极运行前提。')
q(c,'restore_without_repair','restoration','故障设备已隔离，但原因未处理，3.2是否支持直接恢复双极送电？',['maintenance'],'3.2终点是隔离转检修而不是复役许可；缺口只限定本预案。','本预案没有给出这种直接恢复许可。','完整复役前提与操作票须另查运行规定、检修验收及相应操作票，不能由“隔离完成”补造。')
q(c,'blackstart_not_implied','clarification','双极闭锁是否就等于全站失电，可以跳过状态检查开始黑启动？',['bipole_block','ac_limits','primary_report'],'3.2描述双极状态不证明全站失电，3.1是不同触发条件；先核实际状态。','不能仅凭双极闭锁认定全站失电并套用黑启动。')

master_context=['cp41379_41474','cp41475_41577','cp41578_41671','cp47442_47549','cp42776_42910','cp42911_42940','cp43073_43165','cp45773_45796','cp172188_172274']
c=chain('pole_block_and_ground_current','单极闭锁：另一极过负荷、主控权限与入地电流','3.3，关联第三分册过负荷及接地极规定',['3.3'],context_blocks=master_context)
protection_intro(c,'3.3')
f(c,'transfer','3.3.1.2','故障极闭锁，功率全部或部分转移至另一极；“全部或部分”不等于保证健康极不会过负荷。','故障极、功率全部或部分转移两种范围完整。')
f(c,'ac_locked','3.3.1.3','故障极内换流器交流侧开关跳闸并锁定。','范围是故障极内的换流器，不外推全部换流器。')
f(c,'isolated','3.3.1.4','故障极退至隔离状态。','故障极隔离，未将另一极也自动算隔离。')
f(c,'power_filters','3.3.2.1','检查直流功率变化和滤波器退出情况，及时汇报调度及领导。','功率变化和滤波器退出、及时汇报两对象完整。')
f(c,'overload_mode','3.3.2.2','故障极闭锁引起另一极过负荷时，申请调度调整运行方式。','由闭锁引起另一极过负荷的条件及调度申请保留。')
f(c,'primary_report','3.3.2.3','检查一次设备状态变化及保护动作范围内设备是否正常，及时向调度和领导详细汇报。','一次状态、保护范围及报告完整。')
f(c,'recording','3.3.2.4','查看保护动作，整理故障录波，及时传真至调度及相关部门。','保护、录波、及时传真及对象完整。')
f(c,'maintenance','3.3.2.5','申请调度将故障极（设备）转检修，通知检修人员处理。','申请主体、故障极对象、转检修和检修人员完整。')
f(c,'ground_attention','3.3.2.6','关注接地极电流，做好运行方式切换准备。','关注接地极电流和准备两要求完整，不仅关注传输功率。')
f(c,'master_emergency','cp44192_44290','第三分册规定通常不使用过负荷能力；单极或单换流器闭锁后若出现直流过负荷，主控站值班员应不待调度指令，立即将直流输送功率控制到当前电压水平下最大允许功率，并立即向值班调度员汇报。该授权对象是主控站值班员。','正常不用过负荷、两类闭锁、过负荷前提、主控站角色、不待指令、当前电压最大允许功率和立即事后报告完整。')
f(c,'ground_limit','cp47550_47813','第三分册接地极电流安全限值为3000A。一极闭锁且另一极过负荷时，应在2小时内把入地电流控制到安全限值以下，或通过转金属回线/恢复双极消除入地电流；后述条件触发的“立即”动作不能被概括的2小时时限替代。','3000A、单极闭锁另极过负荷、2小时以内控制安全限值以下或消除入地电流，两种消除路径完整。')
f(c,'nonline_branch','cp47550_47813','因非线路故障闭锁时，核实运行极具备金属回线转换条件后，立即进行金属回线转换；不具备条件或转换失败时，立即把入地电流控制到安全限值或以下。','非线路故障、运行极、已核实具备条件、立即转换、无条件或失败立即限流完整。')
f(c,'line_branch','cp47550_47813','因线路故障闭锁时，核实闭锁极具备带线路OLT试验条件后立即进行试验，试验成功后恢复闭锁极；条件不具备或试验不成功时，立即把入地电流控制到安全限值或以下。本段未给出完整OLT试验操作票。','线路故障、闭锁极、具备带线路OLT条件、立即试验、成功恢复、不具备/失败立即限流完整。')
c['branches']=['主控站闭锁后过负荷按专项不待指令控功率并立即汇报，不能只等待方式申请','入地电流非线路故障/线路故障以及条件成立/不成立或失败分别处理']
whole(c,'单极闭锁后，怎样检查功率转移、保护和故障设备，并处理另一极过负荷及入地电流？','3.3全条与第三分册主控站紧急功率控制、入地电流总要求及两故障原因分支共同核对，未把2小时作为立即分支的等待授权。')
q(c,'transfer_is_safe','false_premise','功率已转移至另一极，是否说明系统安全，可以不查另一极过负荷和接地极电流？',['transfer','power_filters','overload_mode','ground_attention'],'功率转移不保证无过负荷，不能删掉直流功率、滤波器及接地极电流检查。','不能据此省略检查。')
q(c,'master_waits','conditional_branch','本站已确认是主控站，单极闭锁后直流过负荷，是否只能等调度方式调整命令再控制功率？',['master_emergency','overload_mode'],'主控站明确权限与立即功率控制、立即汇报同时保留，方式申请不能延迟该专项动作。','不应只等待；主控站此状态有明确的不待指令处理要求。')
q(c,'unknown_master','preconditions','本站当前主控身份尚未确认，能否只因是姑苏站就套用主控站不待指令控制功率条款？',['master_emergency'],'授权主体必须核实，主控轮换及故障临时转换上下文不能按站名直接认定；不声称非主控一律不能按国调命令操作。','不能仅凭站名认定适用。','第三分册另有主控轮换、故障时国调可直接向非主控站下令及经许可临时转换的规定；这些不等于非主控站可自行套用主控站授权。')
q(c,'two_hours_not_wait','false_premise','非线路故障使一极闭锁，运行极不具备金属回线转换条件，是否还有2小时可以先等待而不限流？',['ground_limit','nonline_branch'],'核实当前非线路故障且不具备转换条件，选择立即控制到安全限值或以下；总时限不覆盖更具体立即要求。','不能把2小时理解为该分支可以等待的时间。')
q(c,'line_fault_olt','conditional_branch','线路故障导致闭锁极停运，带线路OLT条件不具备或试验失败，还应当直接恢复闭锁极吗？',['line_branch','ground_limit'],'OLT成功恢复与不具备/失败立即限流分支分开；没有给失败后继续试验次数。','不应直接走试验成功后的恢复分支。')
q(c,'changed_failure_cause','multiturn','后来核实是线路故障，且闭锁极不具备带线路OLT条件，刚才的金属回线建议还能原样执行吗？',['nonline_branch','line_branch'],'保留合成历史中的非线路故障，最新信息改变故障原因和条件；不能不核条件就沿用旧方案。','需要按最新故障原因与条件重新选择分支。',history=[{'role':'user','content':'一极因非线路故障闭锁，运行极已核实具备金属回线转换条件。'},{'role':'assistant','content':'第三分册该分支要求立即进行金属回线转换；若条件不具备或转换失败，立即将入地电流控制到安全限值或以下。'}],standalone='一极闭锁先被认定非线路故障且具备转换条件，后来核实为线路故障且闭锁极无带线路OLT条件，应否沿用原建议？')
q(c,'maximum_power_number','source_gap','主控站要控制到当前电压水平的最大允许功率，仅凭本次这些条款能给出统一MW值和完整OLT操作票吗？',['master_emergency','line_branch'],'按电压当前限额而非固定功率，原文只有OLT条件引用，不能补参数表或顺控票。','这些条款不能提供统一MW值或完整OLT操作票。')

c=chain('single_converter_block','单换流器闭锁：本极和另一极过负荷与故障范围','3.4，关联第三分册过负荷规定',['3.4'],context_keys=section('3.2','3.3'),context_blocks=['cp41379_41474','cp41578_41671','cp49000_49096'])
protection_intro(c,'3.4')
f(c,'transfer','3.4.1.2','故障换流器闭锁，功率转移至仍运行的换流器。','对象为单个换流器，不改成故障极全部闭锁。')
f(c,'ac_locked','3.4.1.3','故障换流器交流侧开关跳闸并锁定。','锁定范围限故障换流器交流侧。')
f(c,'isolated','3.4.1.4','故障换流器退至隔离状态。','隔离对象是故障换流器，非全部极。')
f(c,'power_filters','3.4.2.1','检查直流功率变化及滤波器退出情况，及时汇报调度和领导。','功率、滤波器退出及及时汇报完整。')
f(c,'overload_scope','3.4.2.2','故障换流器闭锁导致本极另一台换流器或另一极过负荷时，申请调度调整运行方式；不能只关注另一极而漏本极仍运行的换流器。','本极另一台与另一极两个过负荷对象完整。')
f(c,'primary_report','3.4.2.3','检查一次设备状态变化、保护范围内设备是否正常，及时向调度和领导详细汇报。','完整提取跨页条款，保留设备检查及报告，不把页码当步骤。')
f(c,'recording','3.4.2.4','查看保护装置动作，整理故障录波并及时传真至调度和相关部门。','保护、录波、时限与接收对象齐全。')
f(c,'maintenance','3.4.2.5','申请调度将故障换流器（设备）转检修，通知检修人员处理。','故障换流器对象、调度申请、检修齐全，不扩大为全部直流必须检修。')
f(c,'master_emergency','cp44192_44290','第三分册通常不使用直流过负荷能力；单极或单换流器闭锁后出现直流过负荷，主控站值班员应不待调度指令立即控制直流输送功率到当前电压最大允许功率，并立即向值班调度员汇报。','同一紧急条款明确包括单换流器闭锁，主控站角色和事后立即汇报完整。')
c['branches']=['本极另一台或另一极过负荷申请调整；主控站已出现直流过负荷按明确紧急功率条款处理']
whole(c,'单换流器闭锁后，应如何辨认故障范围、核查功率和滤波器，并处理过负荷与检修？','核对3.4全条，限定单换流器状态，不套3.2全部交流滤波器退出；第三分册主控站紧急功率条款共同适用。')
q(c,'only_other_pole','false_premise','单换流器闭锁后另一极没有过负荷，是否说明不需要再查本极运行换流器的负荷？',['transfer','overload_scope'],'不能只因另一极正常省略本极另一台换流器对象。','还要考虑本极仍运行的换流器。')
q(c,'all_filters_out','false_premise','一个换流器闭锁，能否照抄双极闭锁预案，认定所有换流器交流开关和全部滤波器都已退出？',['ac_locked','isolated','power_filters'],'故障对象单个，3.4只要求查滤波器退出情况，未说全部退出；用3.2完整上下文对照。','不能把双极状态外推到单换流器故障。')
q(c,'master_overload','conditional_branch','主控站发现单换流器闭锁后直流已过负荷，除了申请方式调整，还能否等待才控制功率？',['master_emergency','overload_scope'],'第三分册明确单换流器闭锁触发同一紧急授权，保留当前电压限额与立即汇报。','不应延迟专项要求的立即功率控制。')
q(c,'maintenance_scope','preconditions','转检修时，3.4要求申请的是故障换流器，还是自动把双极和所有换流器全转检修？',['maintenance','isolated'],'申请范围按具体故障换流器，没有扩张其他设备。','本节规定的是故障换流器（设备）。')
q(c,'restore_after_isolation','source_gap','故障换流器已经隔离转检修，能否仅凭3.4安排完整的在线重新投入操作？',['maintenance'],'本节到检修处理止，不含故障换流器复役完整票；在线投入条件与验收要另查。','仅凭本预案不能给出完整重新投入流程。')

filter_available=['cp75721_75736','cp75737_75755','cp75756_75797','cp75798_75821']
c=chain('dc_power_rundown','直流功率回降：原因分支、滤波器可用性和恢复权限','3.5，关联6.15与第三分册滤波器投切规定',['3.5'],context_keys=section('6.15','9.15'),context_blocks=['cp47442_47549','cp47550_47813','cp50255_50335','cp50731_51094'])
f(c,'rundown_signal','3.5.1.1','OWS可发保护启动功率回降报警，所列来源包括内水冷出阀温度高、绝对最小滤波器、接地极线路过负荷保护和安控装置，并启动故障录波；不能由一个回降报警直接认定唯一故障原因。','四类触发来源和录波启动齐全，未把列举改成全部同时存在。')
f(c,'current_power_down','3.5.1.2','直流电流（功率）下降。','现象电流功率下降完整。')
f(c,'reactive_filters','3.5.1.3','交流滤波器是否自动退出取决于无功情况，不能把功率下降一律等同滤波器全部退出。','视无功情况而定的条件完整。')
f(c,'report','3.5.2.1','汇报调度和领导。','两个报告对象完整。')
f(c,'overload_mode','3.5.2.2','其他换流器或另一极过负荷时，申请调度调整运行方式。','两类设备对象、过负荷前提及申请完整。')
f(c,'temperature_check','3.5.2.3','检查故障换流器阀结温度及换流器进出水温度是否正常。','结温、进水、出水检查对象完整。')
f(c,'cooling_branch','3.5.2.4','换流器结温或进出水温度过高时，检查阀水冷系统；水冷系统运行存在异常时，执行对应设备事故处理预案。','温度高才查水冷，水冷异常才转对应预案，不由报警直接确定故障泵。')
f(c,'filter_branches','3.5.2.5','交流滤波器跳闸引起回降时，有备用滤波器则先手动投入备用，再向调度汇报申请恢复输送功率；备用不可用则向调度申请降低功率，直至报警复归。投入和功率恢复是两步，不能先恢复功率后补滤波器。','跳闸原因、可用/不可用两分支、先投再申请恢复、降低至报警复归全部完整。')
f(c,'ground_overload','3.5.2.6','接地极线路过负荷引起功率回降时，汇报调度调整运行方式。','接地极线路过负荷原因和调度方式调整完整，不统一套滤波器更换。')
f(c,'bus_available','cp75721_75736','第三分册滤波器可用条件包括交流滤波器大组母线运行。','第一个可用条件大组母线运行。')
f(c,'hot_standby','cp75737_75755','对应交流滤波器小组应处于热备用状态。','第二个可用条件小组热备用。')
f(c,'switch_available','cp75756_75797','小组滤波器开关须在远方控制、储能正常、SF6压力正常且无开关锁定信号。','第三组可用条件四项齐全。')
f(c,'available_time','cp75798_75821','可用条件还要求该小组退出时间超过10min；仅看到“热备用”或刚好10分钟不能证明全部可用条件满足。','第四个条件超过10min的严格表述保留，不抹成至少10min。')
f(c,'manual_forbidden_during_ramp','cp76488_76520','功率升降或系统运行方式转换期间，不允许手动投切交流滤波器。因此即使已有备用，也不能在上述期间直接套用手动投入步骤。','功率升降及方式转换两类状态、手动投切禁令完整；适用到3.5手动投入需确认不在该状态。')
f(c,'manual_sequence_limits','cp76521_76641','手动投切交流滤波器或并联电容器遵循先投后切；操作前检查备用数量充足，并考虑切除后绝对最小组数、Umax和Qmax仍满足要求。不满足时可申请调度预先调整直流功率。','先投后切、两设备、足够备用、切后ABS/Umax/Qmax三约束及预先调功率申请齐全。')
f(c,'discharge_time','cp76642_76702','滤波器或并联电容器退出后，手动再次投入须满足放电时间；姑苏站500kV交流滤波器放电时间为10分钟，不能因急于恢复功率免除。','退出后手动再投、足够放电、本站500kV交流滤波器10分钟条件完整。')
f(c,'small_group_request','6.15.2.2','若属于小组保护动作，还应检查备用滤波器是否正常投入；滤波器需求不满足时，按6.15申请手动投入备用。这个投入申请与3.5的恢复功率申请对象不同。','小组动作检查正常投入、需求不满足申请手动投入保留，两个申请对象不混。')
f(c,'notify_master','cp74857_74904','本站为非主控站时，交流滤波器故障可能引起N-1故障导致直流回降，应及时通知主控站。','非主控身份、滤波器故障可能造成N-1回降、及时通知主控站完整。')
c['branches']=['温度高且水冷异常转对应预案','滤波器跳闸且备用真正可用、没有投切禁令时先投后申请恢复；不可用申请降功率至复归','接地极线路过负荷汇报方式调整；其他设备过负荷申请调度调整']
whole(c,'功率回降后，怎样区分温度、滤波器和接地极原因，并核对备用投入条件与功率恢复？','3.5全条及第三分册可用、投切禁令、放电时间、6.15小组投入申请共同核对，不把“有备用”当作立即投入许可。')
q(c,'recover_before_filter','false_premise','滤波器跳闸导致回降，备用已确认可用且不在功率升降/方式转换期间，可以先恢复功率，再补投入备用吗？',['filter_branches','small_group_request'],'题设已经确认可用和时段；仍按先投入后汇报申请恢复功率，小组保护需求不足的投入申请另保留。','不符合规定顺序。')
q(c,'hot_standby_five_min','preconditions','备用滤波器热备用，但刚退出5分钟，能否因为回降急需恢复就立即手动投入？',['bus_available','hot_standby','switch_available','available_time','discharge_time'],'题设5min小于放电及退出时间条件，全部可用条件列明，不编造安全裕量。','尚不满足所列可用与再次投入条件。')
q(c,'still_ramping','false_premise','自动回降尚在进行，即使备用滤波器已可用，能否立即手动投入？',['manual_forbidden_during_ramp','filter_branches'],'手动投切禁令覆盖功率升降阶段，3.5先投后恢复不取消该状态限制。','不能在功率升降期间直接实施手动投切。')
q(c,'no_available_backup','conditional_branch','滤波器跳闸引起回降，但当前没有可用备用，应继续申请把功率恢复到原值吗？',['filter_branches'],'无可用备用走申请降低至报警复归分支，不沿用可用时的恢复分支。','应选择备用不可用的分支。')
q(c,'water_not_confirmed','conditional_branch','回降后发现结温和进出水温度偏高，是否就能直接判定主泵故障并照主泵隔离流程做？',['temperature_check','cooling_branch'],'高温先查水冷，异常再相应预案；不能跳过诊断指定主泵。','还不能直接确定主泵故障。')
q(c,'ground_not_filter','conditional_branch','已确认接地极线路过负荷导致回降，是否一律按投入备用交流滤波器来恢复？',['ground_overload','filter_branches'],'原因不同分支不同，不能用滤波器投入掩盖接地极过负荷。','不能一律套用滤波器原因的恢复分支。')
q(c,'backup_failed_followup','multiturn','现场又发现备用开关有锁定信号，仍能沿用刚才先投备用再申请恢复功率的建议吗？',['switch_available','filter_branches'],'合成历史先给可用前提，最新锁定信号撤销可用性；改用不可用分支，不忽略最新设备状态。','不能继续把这组备用当作可用。',history=[{'role':'user','content':'滤波器跳闸引起回降，备用已确认可用，功率已稳定且未进行方式转换。'},{'role':'assistant','content':'按适用申请要求投入备用滤波器后，再向调度汇报申请恢复输送功率。'}],standalone='滤波器跳闸回降后原认备用可用，后来发现其开关锁定，应否沿用先投备用恢复功率建议？')
q(c,'nonmaster_reports','evidence_submission','本站不是主控站，滤波器故障可能导致N-1功率回降，除了调度和领导还需通知谁？',['report','notify_master'],'非主控条件及及时通知主控站完整，没有用汇报领导代替站间通知。')

c=chain('minimum_filter_and_rpc','最小滤波器不足：Min/ABS区别、RPC模式及备用处理','6.11，关联第三分册无功控制说明',['6.11'],context_keys=section('3.5'),context_blocks=['cp74794_74856','cp74857_74904','cp207206_207263','cp75710_75720']+filter_available+['cp76488_76520','cp76521_76641','cp76642_76702'])
f(c,'alarm','6.11.1.1','事件记录发最小滤波器不满足报警，不能仅凭这个报警名称认定为绝对最小滤波器不满足。','最小滤波器报警对象准确，与ABS保留术语区别。')
f(c,'monitor_report','6.11.2.1','汇报领导，密切监视直流系统运行状态。','报告领导与密切监视两个要求。')
f(c,'rpc_check','6.11.2.2','检查无功功率控制方式是否正常。','检查无功模式的要求保留，不跳过模式诊断。')
f(c,'switch_check','6.11.2.3','检查可用滤波器开关控制方式和弹簧储能是否正常。','可用滤波器开关方式及弹簧储能两项检查保留。')
f(c,'normal_checks','6.11.2.4','上述检查均正常时，加强系统监视，必要时申请调度投入备用交流滤波器。','以上均正常、加强监视、必要时申请投入，未改成检查正常就可自行投入。')
f(c,'no_backup','6.11.2.5','暂时没有可用备用时，加快现场滤波器检修工作，尽快恢复滤波器备用。','暂时无可用的前提、加快检修及恢复备用完整；不补无据的固定功率或停极动作。')
f(c,'rpc_manual','cp75597_75709','RPC手动时，满足最小滤波器和Q control/U control要求的滤波器组投切由运行人员手动完成；交流过电压、绝对最小滤波器、Umax和Qmax仍由无功控制自动完成。','人工投切两低优先级及四类仍自动策略完整，不改成手动模式关闭全部保护。')
f(c,'rpc_priorities','cp76794_76995','无功手动模式仅高优先级控制策略有效，包括交流过电压、绝对最小滤波器、交流母线Umax/Umin和Qmax；Min filter及Q/U控制模块无效。控制模块无效与相关投切需人工承担不是同一意思。','手动模式高优先级四类、低优先级两类失效，保留Umin补充；人工接管关系与前段共同核对。')
f(c,'abs_behavior','cp206303_206482','第三分册附录J将绝对最小滤波器ABS定义为防止其余在运滤波器谐波过负荷所需的最少组数；不满足时降低直流输送功率，降至最后一级仍不满足则经预设延时停运直流系统。配置表另见附件1，本段未列具体延时或完整配置数值。','ABS目的、降功率、最低级仍不足预设延时停运、配置表引用全部完整，未编造秒数。')
f(c,'min_behavior','cp206781_206953','Min filter按直流功率、整流/逆变模式、解锁极个数及直流电压确定满足谐波要求的最少组数；不满足时可发投入指令直到满足。它不能直接发切除指令，只能保证较低优先级模块可以发切除指令。','Min所依四状态参数、补投至满足、不能直接切除及较低优先级关系完整。')
f(c,'replacement_times','cp207082_207205','第三分册交流滤波器替换原则：保护跳闸滤波器按ABS或Min需求由另一组替代；属于ABS时在1s内投入另一组，属于Min时在5s内投入另一组。两类时限不能互换，也不是故障滤波器可以立即重新合闸。','保护跳闸、替代组、ABS1s/Min5s齐全；再投原故障组没有授权。')
c['branches']=['检查均正常必要时申请备用；暂时无可用备用加快检修','RPC手动区分人工承担的Min/Q-U投切与仍有效高优先级ABS等；ABS不满足与Min不满足后果不同']
whole(c,'最小滤波器不足时，怎样检查模式和备用，并区分Min、ABS控制后果、手动模式与替换时限？','6.11全条及第三分册两个模式段、ABS/Min定义和替换原则逐项对照，不合并两个最小概念。')
q(c,'min_equals_abs','false_premise','“最小滤波器不满足”是否就等于ABS不满足，可以直接认定系统必然回降或停运？',['alarm','min_behavior','abs_behavior'],'Min与ABS目的条件后果不同，不由近似报警名推唯一停运结论。','不能这样等同或认定。')
q(c,'manual_no_protection','false_premise','RPC已经手动，是否说明所有无功控制都停用了，ABS和过电压也不动作？',['rpc_manual','rpc_priorities'],'仍自动高优先级与人工承担低优先级明确区分，不把模块失效当人工操作无限授权。','不是；高优先级控制仍有效。')
q(c,'all_checks_normal','preconditions','无功模式和开关控制、弹簧储能都正常，是不是可以不申请就立即投备用？',['rpc_check','switch_check','normal_checks'],'均正常条件和必要时调度申请保留，另外投切禁令和可用时间在上下文限制，不取消。','不能把检查正常当作免申请许可。')
q(c,'no_backup','conditional_branch','暂时没有可用备用滤波器，6.11规定的下一步是什么，是否给出了统一降到多少MW？',['monitor_report','no_backup'],'无备用加快检修恢复备用、监视报告完整，没有固定MW；不把3.5特定回降原因分支当作6.11统一动作。','本节要求监视并加快检修恢复备用，未给统一MW值。')
q(c,'one_or_five_seconds','clarification','保护跳闸后替换滤波器的投入时限是一律1秒，还是一律5秒？',['replacement_times'],'按ABS/Min两需求分支1s和5s，另一组替代不等于原故障组再送。','需要按需求类型区分。')
q(c,'fixed_groups_delay','source_gap','只凭这些段落，能否给任意运行状态一个固定最小组数，并给出ABS末级不足后停运的固定秒数？',['min_behavior','abs_behavior'],'Min取决四参数，ABS配置表另见附件且未解释；预设延时未给秒数，不猜表格数字。','不能。组数需结合运行状态和配置表；该段未给停运延时秒数。')
q(c,'manual_changed','multiturn','现在查到RPC为手动，还能继续说Min模块会自动补投直到报警消失吗？',['min_behavior','rpc_manual','rpc_priorities'],'最新模式限制覆盖原来未给模式的通用说明；人工承担与仍有效ABS不混。','应按当前RPC手动模式修正判断。',history=[{'role':'user','content':'Min filter谐波要求不满足时，这个模块一般做什么？'},{'role':'assistant','content':'第三分册说明它可发出投入交流滤波器组指令，直到满足；它不能直接发切除指令。但实际要结合当前无功控制模式。'}],standalone='Min滤波要求不满足，后来确认RPC手动，应否继续期待Min自动补投，应怎样解释模式限制？')

c=chain('dc_line_restart_boundary','直流线路保护动作：再启动核查、次数与跨册降压疑点','9.6，关联第三分册再启动和电压方式说明',['9.6'],context_keys=section('7.8','9.7'),context_blocks=['cp107235_107244','cp107131_107234','cp241239_241289','cp176245_176280','cp176302_176367'])
f(c,'alarm','9.6.1.1','事件记录发直流线路保护动作报警信号。','直流线路保护动作报警完整，未自动等同Y闭锁。')
f(c,'recording_start','9.6.1.2','故障录波启动。','录波启动现象完整。')
f(c,'immediate_report','9.6.2.1','立即汇报调度及领导。','立即首报两对象完整。')
f(c,'restart_overload','9.6.2.2','检查线路再启动动作、直流电压、电流和过负荷；发生过负荷时汇报调度和领导，申请调度调整直流功率。','再启动、电压电流、过负荷核查及条件报告申请完整。')
f(c,'protection_distance','9.6.2.3','检查保护和安控动作，查看录波核对线路再启动动作，查看故障定位装置测距。','保护、安控、录波、再启动及故障测距齐全。')
f(c,'pole_bus','9.6.2.4','检查极母线设备运行情况。','极母线设备对象完整。')
f(c,'flashover_ref','9.6.2.5','设备存在闪络时，按直流场设备放电闪络专项预案处理；7.8涉及天气、影像、进一步处置及恢复条件，不能由此一句编造无条件恢复。','闪络条件和7.8专项引用保留，其未闭合电压方式边界另外列明。')
f(c,'abnormal_device','9.6.2.6','设备有明显异常时，经领导批准申请调度停运该极；必要时申请调度调整直流功率，隔离故障点并通知检修人员处理。','明显异常、领导批准、申请停极、必要时功率调整、隔离及检修全部完整。')
f(c,'fifth_reduced_voltage','9.6.2.7','第五分册9.6.2.7写明：短时间内频繁全压再启动成功时，建议调度将故障极降压运行。这是该分册建议，是否适用于建苏当前电压方式须与第三分册另核，不能直接转换为现场降压操作。','频繁、短时间内、全压再启动成功三条件与向调度建议完整；降压适用性保留真实跨册疑点。')
f(c,'restart_attempts','cp107245_107341','第三分册正文载明：线路故障重启功能已投入且保护检测故障，由整流站启动；姑苏站原压再启动2次，通信故障或融冰模式期间原压重启1次，降压启动0次；故障仍在则闭锁该极。','投入前提、整流站启动、正常2次、通信故障或融冰1次、降压0次及故障仍在闭锁均保留；段落自身编号不用于修复全册编号。')
f(c,'restart_permission','cp107342_107368','阀组自动重启功能和直流再启动功能均属于国调许可操作。','两项功能和国调许可完整。')
f(c,'disable_at_rectifier','cp107369_107391','需要停用直流线路再启动功能时，应由整流站操作。','停用与整流站操作主体保留，不推成本站可自行停用。')
f(c,'live_work_disable','cp107392_107436','线路有带电作业时，重启非故障阀组reboot功能和直流线路再启动功能都应分别退出；只退出一项不满足列出的两项要求。','带电作业前提、reboot与线路再启动两个功能、分别退出完整。')
f(c,'distance_peer','cp123914_123965','直流线路再启动、交流线路故障跳闸或重合成功时，应及时查看故障测距，与对站核对测距结果并向调度汇报。','三种触发情形、及时测距、对站核对及调度报告完整。')
f(c,'no_reduced_voltage','cp176281_176301','第三分册附录D.3写明“建苏直流无降压运行方式”。与第五分册频繁再启动成功建议降压、7.8恢复全压的表述存在未核实的适用边界，本批不提供确定降压操作方案。','附录D.3无降压表述完整；保存跨册不同表述，不自行按日期选一条覆盖另一条。')
c['branches']=['保护动作但可能再启动成功、设备闪络/明显异常分别核查','原压正常2次/通信故障或融冰1次/降压再启动0次由明确模式决定','第五降压建议与第三无降压方式尚未对接，回答限定为文档边界而非确定操作方案']
whole(c,'线路保护动作后，怎样核对再启动、过负荷、测距与设备异常，并判断次数、许可和降压建议的适用边界？','9.6全条及第三分册再启动次数/角色/带电作业、对站测距及D.3共读；回答指出真实适用疑点，不给确定降压步骤。','本题列明文档要求与适用边界；两册降压表述尚未核实，不据此生成降压操作方案。')
q(c,'restart_success_is_end','false_premise','线路再启动成功了，是否可以认为故障处理已结束，不用查录波、过负荷和极母线？',['immediate_report','restart_overload','protection_distance','pole_bus'],'再启动成功不免除预案核查及报告，避免成功样本被当完整故障终点。','不能据再启动成功省略后续核查。')
q(c,'communication_attempts','conditional_branch','线路故障重启功能已投入，但站间通信故障，是否仍按正常状态原压再启动2次，并再做降压启动？',['restart_attempts'],'通信故障条件选择原压1次、降压0次，故障仍在闭锁，不把正常2次用于异常模式。','应按通信故障对应次数，不能套用正常次数或增加降压启动。')
q(c,'live_work_one_function','false_premise','直流线路带电作业，只退出线路再启动功能而保留reboot，是否满足该段要求？',['live_work_disable','restart_permission','disable_at_rectifier'],'两项分别退出，国调许可和整流站停用主体完整；不把带电作业条件当作免许可。','不满足两项分别退出要求。')
q(c,'local_disable','preconditions','需要停用直流线路再启动功能，本站可否自行决定投退并操作？',['restart_permission','disable_at_rectifier'],'国调许可、由整流站停用两个不同角色限制都保留。','不能据这些条款认定本站可自行决定并操作。')
q(c,'obvious_equipment','conditional_branch','现场极母线设备明显异常，是否可以只说“再启动成功继续观察”，跳过停运申请与隔离？',['pole_bus','abnormal_device'],'明确异常选择领导批准后申请停极、必要时调整功率、隔离检修；不是只成功监視。','应按明显设备异常分支处理。')
q(c,'reduced_voltage_conflict','source_gap','第五分册建议频繁再启动成功时降压，而第三分册D.3说无降压方式，是否可以直接给出降压运行步骤？',['fifth_reduced_voltage','no_reduced_voltage','restart_attempts'],'明确两处实际文字与降压重启0次是三个不同问题，未擅自确认现行版本或设备能力。','不能仅凭这些未对接的表述给出确定降压操作步骤。')
q(c,'measurement_peer','evidence_submission','只有本站故障定位测距结果，是否已满足再启动后的测距核对与汇报要求？',['protection_distance','distance_peer'],'本站保护/安控录波与测距和对站核对、调度报告完整。','还需与对站核对并向调度汇报。')
q(c,'frequency_numeric','source_gap','“短时间内频繁”能否仅凭9.6给出几秒内几次的固定判据，并自动启用降压？',['fifth_reduced_voltage','no_reduced_voltage'],'本节无频繁时间次数判据，跨册降压边界未解决，两种缺口分别指出。','本节没有固定秒数/次数判据，也不能把建议当作自动启用许可。')

c=chain('dc_line_protection_trip','直流线路保护跳闸：Y闭锁、隔离核实与另一极检查','9.7，关联第三分册线路故障闭锁后的入地电流要求',['9.7'],context_keys=section('3.3','9.6'),context_blocks=master_context+['cp44192_44290'])
f(c,'y_block','9.7.1.1','换流器Y闭锁。','Y闭锁原始名称保留，不擅自解释为所有类型闭锁的同一种动作。')
f(c,'ac_locked','9.7.1.2','交流开关跳闸并锁定。','跳闸和锁定两状态完整，不补自动解除锁定。')
f(c,'pole_isolated','9.7.1.3','极隔离。','现象極隔离保留，后续现场核实仍需要。')
f(c,'immediate_report','9.7.2.1','立即向调度和领导汇报。','立即两对象报告完整。')
f(c,'healthy_pole','9.7.2.2','检查另一极系统运行和直流过负荷情况，必要时申请调度调整直流系统运行方式。','另一极、直流过负荷、必要时调度申请完整。')
f(c,'onsite_isolation_filters','9.7.2.3','现场检查故障极隔离情况及交流滤波器投退情况，不能仅凭监控的隔离现象替代现场检查。','现场两个对象完整，未只靠信号确认。')
f(c,'records','9.7.2.4','整理故障录波和事件记录，传真至相关调度和部门。','录波、事件、传真及接收对象完整。')
f(c,'repair','9.7.2.5','通知检修人员检查。','通知检修要求保留，不补无条件复役动作。')
f(c,'line_fault_ground','cp47550_47813','第三分册对线路故障闭锁要求：核实闭锁极具备带线路OLT试验条件后立即试验，成功后恢复闭锁极；条件不具备或失败时，立即将入地电流控制到3000A安全限值或以下。此处只列条件分支，未给完整试验操作票。','只抽线路故障适用子分支；该完整段落的其余命题由单极业务单元三项事实覆盖，不单独冒充全段覆盖。')
c['branches']=['检查另一极过负荷、现场隔离和滤波器实际状态','线路故障闭锁极带线路OLT条件成立且成功/不具备或失败分别处理，不凭Y闭锁直接恢复']
whole(c,'直流线路保护跳闸并Y闭锁、极隔离后，需要哪些核查、报告和检修；线路故障恢复还依赖什么条件？','9.7全部现象与处理及第三分册线路故障入地电流分支共读，没有由监控隔离或再启动成功规则给出立即恢复。')
q(c,'signal_isolation_enough','false_premise','监控显示极隔离，是否就可以省略现场故障极隔离和滤波器投退检查？',['pole_isolated','onsite_isolation_filters'],'现象与现场核实有分别编号，两个检查对象保留。','不能省略规定的现场核查。')
q(c,'restart_success_branch','source_scope','当前已出现Y闭锁、交流开关跳闸锁定和极隔离，还能直接沿用“再启动成功继续运行”的状态判断吗？',['y_block','ac_locked','pole_isolated','healthy_pole','repair'],'当前9.7已跳闸隔离，不按9.6可能成功情形抹掉故障状态。','应按当前跳闸和隔离状态处理。')
q(c,'other_pole_check','preconditions','故障极已经停运，是否可以不再关注另一极，只处理故障设备？',['healthy_pole'],'另一极运行及过负荷、必要时方式申请完整。','仍须检查另一极及直流过负荷。')
q(c,'olt_failure','conditional_branch','线路故障闭锁后，带线路OLT失败了，是否还可以直接恢复闭锁极，或等2小时再处理入地电流？',['line_fault_ground'],'失败立即限流3000A或以下，不走成功恢复；不将2小时泛化为可等待授权。','不能直接恢复或把失败后的立即要求延后。')
q(c,'return_to_service_ticket','source_gap','只看9.7这几条，能否写完整OLT试验、锁定解除和故障极复役操作票？',['repair','line_fault_ground'],'预案没有完整票或解除锁定流程，资料缺口限定当前条款，不断言所有rawdoc均没有。','仅凭这些条款不能补齐完整试验与复役操作票。')
q(c,'submission','evidence_submission','线路保护跳闸后，只报“极已隔离”够不够，还应做什么记录提交和检查通知？',['immediate_report','onsite_isolation_filters','records','repair'],'初报、现场隔离及滤波器、录波事件传真、检修检查通知齐全。','只报告隔离状态还不够。')

seed={'version':'20261008-quality-batch05-v1','construction':'source_inventory_first_authored_fact_and_question_plans','chains':chains}
(ROOT/'scripts/qa_operations/quality_seed_batch05.json').write_text(json.dumps(seed,ensure_ascii=False,indent=2)+'\n')
(ROOT/'scripts/qa_operations/source_review_notes_batch05.json').write_text(json.dumps({'reviewer':'Codex（AI来源核对，非业务专家）','scope':'逐题完整原文及关联限制复核；须绑定实际候选和真实模型响应后使用','notes':notes},ensure_ascii=False,indent=2)+'\n')
(ROOT/'scripts/qa_operations/coverage_review_notes_batch05.json').write_text(json.dumps({'reviewer':'Codex（AI整条提取文本覆盖核对，非业务专家）','notes':coverage_notes},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'business_units':len(chains),'qa':sum(len(c['questions']) for c in chains),'fact_entries':sum(len(c['facts']) for c in chains),'per_unit':{c['id']:len(c['questions']) for c in chains}},ensure_ascii=False))
