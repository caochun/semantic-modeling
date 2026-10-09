"""One entry point for QA tools; imports only the selected command."""
import argparse
import importlib
import sys

COMMANDS = {
    'build': ('build', '从提取审计构建历史候选'),
    'generate': ('quality_pilot', '按事实与问题规划生成新的冻结批次'),
    'review': ('review_quality', '调用配置的模型辅助审阅新批次'),
    'verify': ('verify_quality', '核对运行批次及完整审核响应'),
    'coverage': ('coverage_ledger', '建立独立来源覆盖台账'),
    'assemble': ('reviewed_corpus', '汇总已记录来源复核的批次'),
    'print': ('print_reviewed_qa', '导出保留对话历史的QA全文'),
    'baseline': ('baseline', '运行答案盲模型基线'),
    'prepare-review': ('prepare_review', '准备历史候选的复答抽样'),
    'verify-legacy': ('verify_release', '校验历史候选导出'),
    'verify-snapshot': ('snapshots', '离线核对版本化QA快照和原始绑定'),
}


def main():
    parser = argparse.ArgumentParser(description='文档QA构建与质量记录工具')
    parser.add_argument('command', choices=COMMANDS,
                        help='；'.join(f'{k}: {v[1]}' for k, v in COMMANDS.items()))
    args = parser.parse_args(sys.argv[1:2])
    sys.argv = [f'qa-pipeline {args.command}', *sys.argv[2:]]
    importlib.import_module(f'qa_pipeline.{COMMANDS[args.command][0]}').main()


if __name__ == '__main__':
    main()
