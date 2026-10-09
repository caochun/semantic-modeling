"""Print every reviewed question, answer and dialogue without changing the corpus."""
import argparse
import html
import json
from pathlib import Path

from .quality_pilot import read_rows, save, sha
from .verify_quality import verify_source_reviews


def build(dataset, output):
    manifest = json.loads((dataset / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        if sha(dataset / name) != expected:
            raise ValueError('Corpus changed: ' + name)
    rows = read_rows(dataset / 'qa.jsonl')
    reviews = json.loads((dataset / 'source_reviews.json').read_text())
    for name, expected in reviews['bound_files'].items():
        if sha(dataset / name) != expected:
            raise ValueError('Bound review changed: ' + name)
    verify_source_reviews(rows, reviews)
    chains = json.loads((dataset / 'chains.json').read_text())
    if (output / 'manifest.json').exists():
        raise ValueError('Printed snapshot already exists')
    output.mkdir(parents=True, exist_ok=True)
    intro = (f'{len(rows)}条问答，{len(chains)}个业务单元。逐题AI文档忠实性复核完成；'
             '来源现行性与业务专家审核未认证。按原始问答打印，保留合成对话历史。')
    md = ['# 已整理 QA 全文', '', intro, '']
    plain = ['已整理 QA 全文', intro, '']
    cards = []
    number = 0
    for chain in chains:
        md += ['## ' + chain['title'], '', '来源范围：' + chain['scope'], '']
        plain += [chain['title'], '来源范围：' + chain['scope'], '']
        cards.append('<h2>' + html.escape(chain['title']) + '</h2><p class="scope">' + html.escape(chain['scope']) + '</p>')
        for row in rows:
            if row['business_unit_id'] != chain['id']:
                continue
            number += 1
            md += [f'### QA {number}', '', '题目ID：`' + row['id'] + '`', '']
            plain += [f'QA {number} — ' + row['id']]
            history = []
            if row.get('history'):
                md += ['此前对话：', '']
                plain += ['此前对话：']
                for message in row['history']:
                    role = '用户' if message['role'] == 'user' else '助手'
                    md += ['**' + role + '：** ' + message['content'], '']
                    plain += [role + '：' + message['content']]
                    history.append('<p><b>' + role + '：</b>' + html.escape(message['content']) + '</p>')
            md += ['**问：** ' + row['question'], '', '**答：**', '', row['answer'], '']
            plain += ['问：' + row['question'], '答：', row['answer'], '']
            cards.append('<article><p class="qid">QA ' + str(number) + ' · ' + html.escape(row['id']) + '</p>' +
                         ''.join(history) + '<h3>问：' + html.escape(row['question']) + '</h3><pre>答：\n' +
                         html.escape(row['answer']) + '</pre></article>')
    assert number == len(rows)
    (output / 'qa.md').write_text('\n'.join(md) + '\n')
    (output / 'qa.txt').write_text('\n'.join(plain) + '\n')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>已整理 QA 全文</title>
<style>body{max-width:1000px;margin:30px auto;font:16px/1.7 system-ui;padding:0 20px;color:#172334}article{border-bottom:1px solid #ccd4df;padding:12px 0}h2{margin-top:36px}h3{font-size:17px}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}.scope,.qid{color:#536577;font-size:13px}button{padding:8px 18px;font:inherit}@page{size:A4;margin:18mm}@media print{body{max-width:none;margin:0;padding:0;font-size:11pt}button{display:none}h2,h3{break-after:avoid}.qid{break-after:avoid}article{break-inside:auto}pre{orphans:3;widows:3}}</style>
<h1>已整理 QA 全文</h1><p>INTRO</p><button onclick="window.print()">打印全文 / 保存为 PDF</button>'''
    (output / 'index.html').write_text(page.replace('INTRO', html.escape(intro)) + ''.join(cards) + '</html>')
    save(output / 'manifest.json', {'qa': len(rows), 'business_units': len(chains),
        'source_manifest_sha256': sha(dataset / 'manifest.json'),
        'source_qa_sha256': sha(dataset / 'qa.jsonl'), 'builder_sha256': sha(Path(__file__)),
        'files': {name: sha(output / name) for name in ['qa.md', 'qa.txt', 'index.html']},
        'all_original_questions_answers_and_history_preserved': True,
        'training_export_created': False})
    print(json.dumps({'qa': len(rows), 'output': str(output), 'formats': ['Markdown', 'text', 'printable HTML']}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.dataset.resolve(), args.output.resolve())


if __name__ == '__main__':
    main()
