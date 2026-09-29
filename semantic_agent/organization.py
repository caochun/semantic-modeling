"""Persistence and conservative reuse of problem models and inter-knowledge links."""
import json


class OrganizationStore:
    def knowledge_item(self, kid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM knowledge WHERE id=?', (kid,)).fetchone()
            fragment = db.execute('SELECT fragment FROM knowledge_structure WHERE knowledge_id=?', (kid,)).fetchone()
        if not row:
            return None
        item = dict(row)
        for key in ('conditions', 'evidence', 'review'):
            item[key] = json.loads(item[key])
        item['model_fragment'] = json.loads(fragment['fragment']) if fragment else {}
        item['sources_current'] = all(self.source_current(e['passage_id']) for e in item['evidence'])
        return item

    def source_current(self, pid):
        passage = self.passage(pid, active_only=True)
        if not passage:
            return False
        if self.doc_dir is None:
            return True
        path = (self.doc_dir / passage['path']).resolve()
        if not path.is_relative_to(self.doc_dir.resolve()) or not path.is_file():
            return False
        try:
            stat = path.stat()
            if stat.st_size != passage['size'] or stat.st_mtime_ns != passage['mtime_ns']:
                import hashlib
                return hashlib.sha256(path.read_bytes()).hexdigest() == passage['sha']
            return True
        except OSError:
            return False

    def knowledge_usage(self, kid):
        with self.connect() as db:
            return [dict(row) for row in db.execute('''
                SELECT u.run_id,u.role,u.origin,r.question,r.created_at
                FROM model_usages u JOIN runs r ON r.id=u.run_id
                WHERE u.knowledge_id=? ORDER BY r.created_at DESC LIMIT 30
            ''', (kid,))]

    def save_problem_model(self, run_id, model, ref_map, origins, review, link_reviews):
        from .store import digest, normalize, now
        uses = []
        # A duplicate proposal can resolve to a knowledge ID already used by this
        # question. Preserve all roles, but one reference and validation per ID.
        by_id = {}
        for use in model['knowledge_uses']:
            kid = ref_map[use['ref']]
            item = self.knowledge_item(kid)
            if item is None:
                raise ValueError('局部模型引用的知识不存在')
            if kid in by_id:
                by_id[kid]['role'] += '；' + use['role']
                continue
            saved = {'knowledge_id': kid, 'role': use['role'], 'origin': origins[use['ref']],
                     'snapshot': {key: item[key] for key in ('title', 'statement', 'scope', 'status')}}
            by_id[kid] = saved
            uses.append(saved)
        links = []
        for i, link in enumerate(model['links']):
            source, target = ref_map[link['source_ref']], ref_map[link['target_ref']]
            assessment = dict(link_reviews.get(i, {'verdict': 'insufficient', 'reason': '未获得联系复核'}))
            if source == target:
                assessment = {'verdict': 'insufficient', 'reason': '两端实际引用同一条知识，不能构成独立联系'}
            data = {key: link[key] for key in ('relation', 'explanation', 'scope', 'conditions', 'evidence')}
            data.update(source_id=source, target_id=target)
            fingerprint = digest(json.dumps([source, target, normalize(link['relation']), normalize(link['scope']),
                                            sorted(normalize(c) for c in link['conditions'])], ensure_ascii=False))
            data.update(id='link_' + fingerprint, review=assessment,
                        status='reviewed' if assessment.get('verdict') == 'supported' and not assessment.get('check_errors') else 'candidate')
            links.append(data)
        all_usable = all(self.knowledge_item(u['knowledge_id'])['status'] == 'reviewed'
                         and self.knowledge_item(u['knowledge_id'])['sources_current'] for u in uses)
        checks = list(review.get('check_errors', []))
        if not all_usable:
            checks.append('包含未通过复核或来源失效的知识，整组判断暂不可复用')
        if any(link['status'] != 'reviewed' for link in links):
            checks.append('存在未通过原文校验或语义复核的知识联系')
        review = {**review, 'check_errors': checks}
        supported = (review.get('verdict') == 'supported' and not review.get('check_errors') and all_usable
                     and all(link['status'] == 'reviewed' for link in links))
        payload = {key: model[key] for key in ('objective', 'scope', 'assumptions', 'required_inputs', 'boundaries')}
        payload.update(knowledge_uses=uses, links=links, review=review,
                       status='reviewed' if supported else 'candidate', version=1)
        with self.connect() as db:
            row = db.execute('SELECT question FROM runs WHERE id=?', (run_id,)).fetchone()
            question_key = digest(normalize(row['question']))
            db.execute('INSERT OR REPLACE INTO problem_models VALUES(?,?,?)',
                       (run_id, json.dumps(payload, ensure_ascii=False), now()))
            db.execute('DELETE FROM model_usages WHERE run_id=?', (run_id,))
            db.execute('DELETE FROM link_validations WHERE run_id=?', (run_id,))
            for use in uses:
                db.execute('INSERT INTO model_usages VALUES(?,?,?,?,?)',
                           (run_id, use['knowledge_id'], use['role'], use['origin'], now()))
            for i, link in enumerate(links):
                eligible = supported and link['status'] == 'reviewed'
                db.execute('INSERT INTO link_validations VALUES(?,?,?,?,?,?)',
                           (run_id, i, link['id'], question_key, int(eligible), json.dumps(link, ensure_ascii=False)))
        return self.problem_model(run_id)

    def problem_model(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM problem_models WHERE run_id=?', (run_id,)).fetchone()
            run = db.execute('SELECT question,result FROM runs WHERE id=?', (run_id,)).fetchone()
        if not run:
            return None
        if row:
            result = json.loads(row['payload'])
        else:
            # Old runs provide provenance only. Do not manufacture semantic links
            # or retrospectively describe their combinations as reviewed.
            previous = json.loads(run['result']) if run['result'] else None
            if not previous or 'answer' not in previous:
                return None
            changes = {k['id']: k for k in previous.get('knowledge_changes', [])}
            ids = list(dict.fromkeys([*changes, *previous.get('reused_knowledge_ids', [])]))
            result = {'version': 0, 'status': 'historical', 'objective': run['question'],
                      'scope': '历史记录未单独声明局部模型范围', 'assumptions': [], 'required_inputs': [],
                      'boundaries': previous.get('unresolved_questions', []), 'links': [],
                      'review': {'verdict': 'insufficient', 'reason': '此记录早于局部模型功能，仅展示当时引用的知识'},
                      'knowledge_uses': [{'knowledge_id': kid, 'role': '历史记录未声明判断用途',
                          'origin': 'new' if kid in changes else 'reused', 'snapshot': changes.get(kid, {'title': kid})} for kid in ids]}
        available = True
        for use in result['knowledge_uses']:
            current = self.knowledge_item(use['knowledge_id'])
            use['knowledge'] = current
            use['available'] = bool(current and current['status'] == 'reviewed' and current['sources_current'])
            available = available and use['available']
        for link in result['links']:
            link['sources_current'] = all(self.source_current(e['passage_id']) for e in link['evidence'])
            available = available and link['sources_current']
        result['effective_status'] = 'unavailable' if result['status'] == 'reviewed' and not available else result['status']
        result['run_id'] = run_id
        return result

    def related_models(self, knowledge_ids, limit=4):
        if not knowledge_ids:
            return []
        with self.connect() as db:
            placeholders = ','.join('?' for _ in knowledge_ids)
            ids = [r['run_id'] for r in db.execute(
                f'SELECT DISTINCT run_id FROM model_usages WHERE knowledge_id IN ({placeholders}) ORDER BY created_at DESC',
                list(knowledge_ids))]
        models = [self.problem_model(rid) for rid in ids]
        return [m for m in models if m and m['effective_status'] == 'reviewed'][:limit]

    def shared_links(self, query='', knowledge_ids=None, reusable_only=False):
        from .store import tokens
        with self.connect() as db:
            rows = [dict(r) for r in db.execute('''
                SELECT v.*,r.question,r.status AS run_status FROM link_validations v
                JOIN runs r ON r.id=v.run_id ORDER BY r.created_at
            ''')]
        groups, knowledge_cache = {}, {}
        for row in rows:
            link = json.loads(row['payload'])
            if knowledge_ids is not None and not ({link['source_id'], link['target_id']} & set(knowledge_ids)):
                continue
            group = groups.setdefault(link['id'], {**link, 'validations': [], 'supporting_questions': set(), 'has_conflict': False})
            for key in ('source_id', 'target_id'):
                if link[key] not in knowledge_cache:
                    knowledge_cache[link[key]] = self.knowledge_item(link[key])
            usable = all(knowledge_cache[link[key]] and knowledge_cache[link[key]]['status'] == 'reviewed'
                         and knowledge_cache[link[key]]['sources_current'] for key in ('source_id', 'target_id'))
            if row['run_status'] == 'completed' and usable and link['review'].get('verdict') == 'conflict':
                group['has_conflict'] = True
            eligible = bool(row['eligible'] and row['run_status'] == 'completed' and usable
                            and all(self.source_current(e['passage_id']) for e in link['evidence']))
            # Losing any premise of the reviewed local combination invalidates
            # its contribution, not just withdrawal of the two visible endpoints.
            model = self.problem_model(row['run_id']) if eligible else None
            eligible = bool(eligible and model and model['effective_status'] == 'reviewed')
            group['validations'].append({'run_id': row['run_id'], 'question': row['question'],
                                         'eligible': eligible, 'review': link['review'], 'evidence': link['evidence']})
            if eligible:
                group['supporting_questions'].add(row['question_key'])
                group['evidence'] = link['evidence']
                group['explanation'] = link['explanation']
        results = []
        for group in groups.values():
            group['support_count'] = len(group.pop('supporting_questions'))
            group['status'] = 'conflict' if group['has_conflict'] else ('shared' if group['support_count'] >= 2 else 'candidate')
            group['source'] = knowledge_cache.get(group['source_id'])
            group['target'] = knowledge_cache.get(group['target_id'])
            searchable = json.dumps([group['relation'], group['scope'], group['source'], group['target']], ensure_ascii=False)
            if query and not any(t in searchable.lower() for t in tokens(query)):
                continue
            if not reusable_only or group['status'] == 'shared':
                results.append(group)
        return sorted(results, key=lambda x: (-x['support_count'], x['id']))
