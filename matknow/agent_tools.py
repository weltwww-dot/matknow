"""Read-only research tools bound to one profile and the user's selection."""
import json
import re
import os
from pathlib import Path,PurePosixPath
from collections import Counter
from dataclasses import replace,asdict

from matknow.experiments.compare import compare_samples, rank_measurements
from matknow.experiments.store import ExperimentStore
from matknow.models import Evidence
from matknow.local_files import LocalFiles

MAX_SOURCES = 48
TOOLS = {
    'generate_experiment_report':{'description':'生成指定实验的完整报告并返回预览导出入口，包含模型撰写、复核和一次修订。使用真实实验ID；多批次需明确一批，完整报告需包含该实验全部样品。','arguments':{'experiment_id':'当前方向及选择范围内的真实实验UUID，来自list_samples，必填'}},
    'prepare_experiment_draft':{'description':'从当前范围资料生成待核对的实验方案、记录或结果草稿。先读取并确认真实document_id；不会写入实验记录，用户点击核对保存。长文档先定位方法章节，再传source_ids选择实际依据。','arguments':{'document_id':'当前范围内真实资料UUID，必填','source_ids':'可选该文档1–48个真实片段ID；省略使用全文','kind':'plan方案、record记录、results测试结果，默认plan','experiment_id':'可选当前范围已有实验UUID；省略默认新建，不自动覆盖'}},
    'read_file':{'description':'读取用户明确指定的本地文件或文件夹，自动用资料库模型整理类别/摘要/标签并导入当前方向。文件夹递归读取支持的文档，保留子目录归属。只读原件，不写入原文件或解压到磁盘。ZIP和文件夹按has_more/next_page分批，末页停止。只处理正文，图片不解读。','arguments':{'path':'file_paths中真实完整文件或文件夹路径，或授权文件夹内的文件路径，必填','member':'可选ZIP/文件夹内相对文件名；省略批量整理导入','offset':'可选文档片段/批次起点，默认0','limit':'每页1–20个片段；ZIP/文件夹每批最多10份文档，默认10'}},
    'list_documents':{'description':'列出当前方向和所选范围内的资料，AI分类和摘要仅是待核对候选。每页20条，按has_more翻页。','arguments':{'query':'可选名称、目录编号或主题关键词；空格分词同时匹配，多个P编号匹配任一目录','offset':'可选分页起点，默认0'}},
    'read_documents':{'description':'按真实document_id读取原文片段，可逐页读取。按has_more和next_offset翻页；has_more=false到末页。','arguments':{'document_ids':'1–10个真实资料UUID，来自目录或sources.document_id，不能用片段id','offset':'可选片段起点，默认0','limit':'可选每页片段数1–20，默认12'}},
    'search_documents': {'description': '检索所选文献片段，可改写关键词补充检索；不能据片段统计全库。', 'arguments': {'query': '关键词，必填'}},
    'search_experiments': {'description': '检索当前方向、所选样品的实验条件和结果片段。图片与未解析附件仅知道存在。', 'arguments': {'query': '关键词，必填'}},
    'list_samples': {'description': '查找样品编号及批次。最多返回20个，complete=false表示不是完整范围。', 'arguments': {'query': '批次/样品关键词，可空'}},
    'read_samples': {'description': '读取明确样品的全部已记录条件与测量。', 'arguments': {'sample_ids': '1–20个样品UUID'}},
    'compare_samples': {'description': '完整比较2–20个明确样品。按单位、方法、测试条件分组，程序排序全部重复测量；不推断最佳性能或因果。',
                        'arguments': {'sample_ids': '2–20个样品UUID', 'metric': '可选指标名称', 'descending': '可选布尔值，默认true', 'reference_result_id': '可选参照测量UUID，取sources.result_id或对比返回的result_id，不能用引用id；计算精确差值'}},
}


class ResearchTools:
    def __init__(self, knowledge, *, document_ids=None, sample_ids=None,file_paths=None,model_client=None,progress=None):
        self.knowledge = knowledge
        self.store = ExperimentStore(knowledge)
        self.document_ids = list(document_ids) if document_ids is not None else None
        self.sample_ids = sample_ids
        if sample_ids is not None:self.store.samples(sample_ids)
        self.sources = []
        self.revisions = {}
        self.files=LocalFiles(knowledge,file_paths or [])
        self.model_client,self.progress=model_client,progress
        self.file_documents={};self.imported_document_ids=[]
        self.classification_failed=False
        self.unorganized_documents=set();self.archive_progress={};self.failed_files=set()
        self.archive_wrappers={}
        self.prepared_draft_ids=[]
        self.generated_reports=[]
        self.document_catalog={}
        self.catalog_pages={}

    def context(self):
        metadata = {}
        ids = [s.id for s in self.sources if s.id.startswith('exp:')]
        if ids:
            with self.knowledge.connection() as db:
                rows = db.execute('SELECT id,sample_id,result_id FROM experiment_evidence WHERE id IN ('+','.join('?' for _ in ids)+')', ids)
                metadata = {row['id']: {'sample_id': row['sample_id'], 'result_id': row['result_id']} for row in rows}
        return [{'number': s.citation_number, 'id': s.id, 'document_id':s.document_id, 'filename': s.filename, 'locator': s.locator, 'text': s.text,'source_role':'experiment_record' if s.id.startswith('exp:') else 'reference_document',
                 **metadata.get(s.id, {})} for s in self.sources]

    def _add(self, sources):
        seen = {s.id for s in self.sources}
        for source in sources:
            if source.id not in seen:
                self.sources.append(replace(source, citation_number=len(self.sources)+1))
                seen.add(source.id)
        if len(self.sources) > MAX_SOURCES:
            raise ValueError('证据数量超过本轮限制，请缩小资料或样品范围。')

    def _ids(self, ids, minimum=1):
        if not isinstance(ids, list) or not minimum <= len(ids) <= 20 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError(f'需要 {minimum}–20 个不同的样品编号。')
        if self.sample_ids is not None and not set(ids).issubset(self.sample_ids):
            raise ValueError('工具不能读取所选范围以外的样品。')
        return ids

    def _records(self, ids, metric=None):
        pairs = self.store.samples(ids)
        for snapshot, _ in pairs:
            if self.revisions.get(snapshot.id, snapshot.revision) != snapshot.revision:
                raise ValueError('实验记录已更新，请重新提问。')
            self.revisions[snapshot.id] = snapshot.revision
        rows = self.store.evidence_rows(ids)
        if any(row['revision'] != self.revisions[row['experiment_id']] for row in rows):
            raise ValueError('实验记录已更新，请重新提问。')
        if metric is not None:
            wanted = {r.id for _, sample in pairs for r in sample.results if r.metric == metric}
            rows = [row for row in rows if row['result_id'] is None or row['result_id'] in wanted]
        self._add([Evidence(row['id'], row['experiment_id'], row['filename'], row['locator'], row['text']) for row in rows])
        return pairs, rows

    def call(self, tool, arguments):
        from matknow.tasks import check_cancelled
        check_cancelled(self.model_client)
        if not isinstance(tool, str) or tool not in TOOLS or not isinstance(arguments, dict):
            raise ValueError('仅允许已列出的本地科研工具。')
        if set(arguments)-set(TOOLS[tool]['arguments']):
            raise ValueError('工具参数不受支持；不能覆盖资料或样品范围。')
        old_sources, old_revisions = list(self.sources), dict(self.revisions)
        try:
            result = getattr(self, '_'+tool)(**arguments)
            if len(json.dumps(result, ensure_ascii=False)) > 14000:
                raise ValueError('完整工具结果过大，请减少样品或指定单一指标。')
            return result
        except TypeError:
            self.sources, self.revisions = old_sources, old_revisions
            raise ValueError('工具缺少必填参数或参数类型无效。') from None
        except Exception:
            self.sources, self.revisions = old_sources, old_revisions
            raise

    @staticmethod
    def _query(query, allow_empty=False):
        if not isinstance(query, str) or len(query) > 2000 or (not allow_empty and not query.strip()):
            raise ValueError('检索关键词需要1–2000个字符。')
        return query.strip()

    def _generate_experiment_report(self,experiment_id):
        from .experiments.files import ExperimentArchive
        from .experiments.report_writer import generate_report
        from .experiments.report import report_payload
        snapshot=self.store.get(experiment_id)
        ids=[s.id for s in snapshot.draft.samples]
        if self.sample_ids is not None and not set(ids).issubset(self.sample_ids):
            raise ValueError('完整报告需要选中该实验的全部样品，请调整选择后重试。')
        if self.revisions.get(snapshot.id,snapshot.revision)!=snapshot.revision:
            raise ValueError('实验记录已更新，请重新提问。')
        self.revisions[snapshot.id]=snapshot.revision
        rows=self.store.evidence_rows(ids)
        if any(row['revision']!=snapshot.revision for row in rows):
            raise ValueError('实验记录已更新，请重新提问。')
        # The full report reads its archive snapshot; the chat needs only a receipt citation.
        self._add([Evidence(row['id'],row['experiment_id'],row['filename'],row['locator'],row['text']) for row in rows[:1]])
        report=generate_report(ExperimentArchive(self.store),snapshot.id,revision=snapshot.revision,profile_name='',client=self.model_client,progress=self.progress)
        self.ensure_current()
        self.generated_reports.append({'experiment_id':snapshot.id,'revision':snapshot.revision,'report':report_payload(report)})
        return {'experiment_id':snapshot.id,'revision':snapshot.revision,'reviewed':report.summary.startswith('AI'),'message':'实验报告已准备，可预览并导出。' if report.summary.startswith('AI') else '报告复核未完成，已保留原始记录整理稿，可预览并查看待处理问题。',
                'source_ids':[s.id for s in self.sources if s.document_id==snapshot.id][:1]}

    def _prepare_experiment_draft(self,document_id,kind='plan',experiment_id=None,source_ids=None):
        if kind not in ('plan','record','results'):raise ValueError('请选择方案、记录或结果草稿。')
        if self.document_ids is not None and document_id not in self.document_ids:raise ValueError('不能读取所选范围以外的资料。')
        sources=self.knowledge.document_sources(document_id)
        total_sources=len(sources)
        if source_ids is not None:
            if not isinstance(source_ids,list) or not 1<=len(source_ids)<=48 or any(not isinstance(s,str) for s in source_ids) or len(set(source_ids))!=len(source_ids) or not set(source_ids).issubset({s.id for s in sources}):raise ValueError('草稿来源必须是该文档内1–48个不同的真实片段。')
            sources=[s for s in sources if s.id in source_ids]
        if self.sample_ids is not None:
            targets={snapshot.id:snapshot for snapshot,_ in self.store.samples(self.sample_ids)}
        else:
            targets={row.id:self.store.get(row.id) for row in self.store.list_experiments(limit=20)}
            if experiment_id is not None:targets[experiment_id]=self.store.get(experiment_id)
        if experiment_id is not None and experiment_id not in targets:raise ValueError('目标实验不在当前范围内。')
        from .experiments.readers import ReadDocument,Block
        from .experiments.import_agent import organize_record
        from .experiments.models import ExperimentDraft,SampleDraft
        from datetime import date
        import hashlib
        text='来源文字快照，非原始二进制文件。\n\n'+'\n\n'.join(s.locator+'\n'+s.text for s in sources)
        document=ReadDocument(document_id,sources[0].filename,hashlib.sha256(text.encode('utf8')).hexdigest(),[Block(s.text,s.locator) for s in sources],[],['保存的是资料原文的文字快照，图片与未解析附件不在快照中。'])
        base=ExperimentDraft(Path(document.filename).stem,date.today().isoformat(),samples=[SampleDraft('待确认样品')])
        candidate=organize_record(document,[s.locator for s in sources],base,self.model_client,intent=kind)
        if len(sources)<total_sources:candidate.notes+=f'\n\n读取范围：本草稿仅基于所选{len(sources)}/{total_sources}个原文片段，不代表整份文档。'
        identifier=self.knowledge.save_experiment_draft(document_id,{'draft':asdict(candidate),'source_ids':[s.id for s in sources],'source_text':text,'filename':Path(document.filename).name+'.来源文字.txt','target_id':experiment_id,'target_revision':targets[experiment_id].revision if experiment_id else None,'allowed_targets':list(targets)})
        self.prepared_draft_ids.append(identifier);self._add(sources[:1])
        return {'draft_id':identifier,'title':candidate.title,'samples':[s.code for s in candidate.samples],'results':sum(len(s.results) for s in candidate.samples),'status':candidate.chemistry.status,'needs_confirmation':True,'source_ids':[sources[0].id],'message':'草稿已准备，用户核对后才能保存到实验记录。'}

    def _import_file(self,path,member=None):
        from matknow.tasks import check_cancelled
        check_cancelled(self.model_client)
        key=(path,member)
        if key not in self.file_documents:
            filename,chunks,warnings=self.files.read(path,member)
            original_key=os.path.normcase(os.path.abspath(path))
            source_hash=self.files.directory_digests.get(original_key) or self.files.digests[original_key]
            parts=PurePosixPath((member or '').replace('\\','/')).parts
            if member and path not in self.archive_wrappers:
                names=[PurePosixPath(e['name'].replace('\\','/')).parts for e in self.files.entries(path) if e['readable']]
                roots={p[0] for p in names if p}
                root=next(iter(roots)) if len(roots)==1 else ''
                paper_root=bool(re.fullmatch(r'P\d+',root,re.I))
                wrapper=root.casefold() in {'inbox','documents','archive','collection','data','资料','文献'} or bool(names) and all(len(p)>1 and re.fullmatch(r'P\d+',p[1],re.I) for p in names)
                self.archive_wrappers[path]=root if root and wrapper and not paper_root and all(len(p)>=3 for p in names) else None
            if len(parts)>2 and parts[0]==self.archive_wrappers.get(path):parts=parts[1:]
            group=parts[0] if len(parts)>1 else ''
            role='补充材料' if member and ('supplement' in member.lower() or '补充' in member) else ('方法摘录' if 'methods' in filename.lower() else '正文')
            document=self.knowledge.import_origin(filename,chunks,source_key=original_key,source_hash=source_hash,member=member or '',group=group,role=role,content_hash=self.files.member_digests[path,member])
            if document.id not in self.imported_document_ids:self.imported_document_ids.append(document.id)
            if not document.summary:self.unorganized_documents.add(document.id)
            metadata=None
            if document.summary:
                pass
            elif self.model_client is not None and not self.classification_failed:
                from matknow.library_agent import organize_sources
                from matknow.llm import ModelError
                from matknow.model_tasks import TaskContextError,TaskValidationError
                if self.progress:self.progress('资料库智能体正在整理：'+filename)
                try:metadata=organize_sources(filename,self.knowledge.document_sources(document.id),self.model_client,knowledge=self.knowledge,document_id=document.id,progress=self.progress)
                except ModelError as exc:
                    self.classification_failed=not isinstance(exc,(TaskContextError,TaskValidationError))
                    warnings.append('原文已导入，AI分类、摘要和标签未完成。')
                except ValueError:warnings.append('原文已导入，AI分类、摘要和标签未完成。')
            else:warnings.append('本次未完成模型整理，仅导入可读取原文。')
            if metadata is None and not document.summary:self.unorganized_documents.add(document.id)
            if metadata:
                self.knowledge.save_document_metadata(document.id,metadata)
                document=replace(document,category=metadata['category'],summary=metadata['summary'],tags=tuple(metadata['tags']))
            if document.summary:self.unorganized_documents.discard(document.id)
            self.file_documents[key]=(document,warnings)
            if self.document_ids is not None and document.id not in self.document_ids:self.document_ids.append(document.id)
        document,warnings=self.file_documents[key]
        return document,warnings,self.knowledge.document_sources(document.id)

    def _read_file(self,path,member=None,offset=0,limit=10):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=20:raise ValueError('文件读取分页范围无效。')
        directory=self.files.is_directory(path)
        if (directory or path.lower().endswith('.zip')) and member is None:
            entries=self.files.entries(path);readable=[entry for entry in entries if entry['readable']]
            state=self.archive_progress.setdefault(path,{'total':len(readable),'handled':set(),'skipped':sum(not e['readable'] for e in entries)})
            page=readable[offset:offset+min(limit,10,MAX_SOURCES-len(self.sources))]
            if not page and offset<len(readable):raise ValueError('本轮来源数量已达上限，请另起问题读取剩余文件。')
            results=[]
            for entry in page:
                if self.progress:self.progress(f'整理资料 {offset+len(results)+1}/{len(readable)}：'+entry['name'])
                try:
                    document,warnings,sources=self._import_file(path,entry['name'])
                    preview=sources[:1] if len(self.sources)<3 else []
                    self._add(preview)
                    results.append({'name':entry['name'],'document_id':document.id,'status':'已导入','category_candidate':document.category,
                                    'organized':bool(document.summary),
                                    'warnings':warnings,'source_ids':[s.id for s in preview]})
                except ValueError as exc:
                    self.failed_files.add((path,entry['name']));results.append({'name':entry['name'],'status':'未导入','reason':str(exc)})
                state['handled'].add(entry['name'])
            skipped=[entry for entry in entries if not entry['readable']]
            return {'kind':'directory' if directory else 'archive','files':results,'total_documents':len(readable),'skipped_count':len(skipped),
                    'skipped_files':[e['name'][:200] for e in skipped[:5]],'complete':offset==0 and len(page)==len(readable),
                    'has_more':offset+len(page)<len(readable),'processed':len(state['handled']),
                    'next_offset':offset+len(page) if offset+len(page)<len(readable) else None,
                    'next_page':{'path':path,'offset':offset+len(page),'limit':limit} if offset+len(page)<len(readable) else None,
                    'import_status':self.import_summary()[0],
                    'coverage':'导入回执与少量原文预览；原文全部保存在资料库。分析全文请用read_documents；has_more=false表示没有下一批。'}
        document,warnings,sources=self._import_file(path,member);page=sources[offset:offset+limit];self._add(page)
        return {'document_id':document.id,'filename':document.filename,'source_ids':[s.id for s in page],'warnings':warnings,
                'total_chunks':len(sources),'complete':offset==0 and len(page)==len(sources),
                'has_more':offset+len(page)<len(sources),
                'next_offset':offset+len(page) if offset+len(page)<len(sources) else None,'status':'原文已导入当前方向'}

    def import_summary(self):
        if not self.imported_document_ids and not self.archive_progress:return '',False
        pending=sum(max(0,state['total']-len(state['handled'])) for state in self.archive_progress.values())
        skipped=sum(state['skipped'] for state in self.archive_progress.values())
        details=[f'资料入库：{len(self.imported_document_ids)}份原文已导入']
        for count,label in ((len(self.unorganized_documents),'份AI整理未完成'),(len(self.failed_files),'份读取失败'),(skipped,'份格式不支持已跳过'),(pending,'份尚未处理')):
            if count:details.append(str(count)+label)
        return '；'.join(details)+'。',bool(self.unorganized_documents or self.failed_files or pending or skipped)

    def _search_documents(self, query):
        sources = self.knowledge.search(self._query(query), self.document_ids)
        self._add(sources)
        return {'source_ids': [s.id for s in sources], 'coverage': '命中片段，非全库统计'}

    def _list_documents(self,query='',offset=0):
        query=self._query(query,allow_empty=True)
        if type(offset) is not int or offset<0:raise ValueError('资料分页起点无效。')
        documents=[d for d in self.knowledge.list_documents() if self.document_ids is None or d.id in self.document_ids]
        if query:
            terms=query.casefold().split()
            groups=[t for t in terms if re.fullmatch(r'p\d+',t)]
            terms=[t for t in terms if t not in groups]
            documents=[d for d in documents if (not groups or d.group.casefold() in groups or any(re.search(r'(?<![a-z0-9])'+re.escape(g)+r'(?![a-z0-9])',d.filename+' '+d.member,re.I) for g in groups)) and all(t in (d.filename+' '+d.group+' '+d.member+' '+d.category+' '+d.summary+' '+' '.join(d.tags)).casefold() for t in terms)]
        page=documents[offset:offset+20]
        for d in page:
            self.document_catalog[d.id]={'document_id':d.id,'filename':d.filename,'group':d.group,'member':d.member,'role':d.role}
        next_page={'query':query,'offset':offset+len(page)} if offset+len(page)<len(documents) else None
        self.catalog_pages[query]={'query':query,'page_start':offset,'page_end':offset+len(page),'total':len(documents),'next_page':next_page}
        return {'documents':[{'document_id':d.id,'filename':d.filename,'group':d.group,'member':d.member,'role':d.role,'chunks':d.chunk_count,'category_candidate':d.category,'summary_candidate':d.summary[:200],'tags_candidate':d.tags} for d in page],
                'total':len(documents),'complete':offset==0 and len(documents)<=20,'has_more':offset+len(page)<len(documents),'page_start':offset,'page_end':offset+len(page),'next_offset':offset+len(page) if offset+len(page)<len(documents) else None,'next_page':next_page}

    def _read_documents(self,document_ids,offset=0,limit=12):
        if not isinstance(document_ids,list) or not 1<=len(document_ids)<=10 or any(not isinstance(i,str) for i in document_ids) or len(set(document_ids))!=len(document_ids):raise ValueError('需要1–10个不同的资料编号。')
        if self.document_ids is not None and not set(document_ids).issubset(self.document_ids):raise ValueError('不能读取所选范围之外的资料。')
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=20:raise ValueError('资料读取范围无效。')
        sources=[s for identifier in document_ids for s in self.knowledge.document_sources(identifier)]
        page=sources[offset:offset+limit];self._add(page)
        return {'source_ids':[s.id for s in page],'total_chunks':len(sources),'complete':offset==0 and len(page)==len(sources),
                'has_more':offset+len(page)<len(sources),'page_start':offset,'page_end':offset+len(page),
                'next_offset':offset+len(page) if offset+len(page)<len(sources) else None}

    def _search_experiments(self, query):
        sources = self.store.search(self._query(query), self.sample_ids)
        with self.knowledge.connection() as db:
            for source in sources:
                row = db.execute('SELECT experiment_id,revision FROM experiment_evidence WHERE id=?', (source.id,)).fetchone()
                if self.revisions.get(row['experiment_id'], row['revision']) != row['revision']:
                    raise ValueError('实验记录已更新，请重新提问。')
                self.revisions[row['experiment_id']] = row['revision']
        self._add(sources)
        return {'source_ids': [s.id for s in sources], 'coverage': '实验命中片段，非完整数量或排名'}

    def _list_samples(self, query=''):
        query = self._query(query, allow_empty=True)
        if self.sample_ids == []:return {'samples': [], 'complete': True, 'query': query}
        sql = 'SELECT x.* FROM experiment_evidence x JOIN experiments e ON e.id=x.experiment_id AND e.revision=x.revision WHERE x.result_id IS NULL AND (x.filename LIKE ? OR x.text LIKE ?)'
        parameters = ['%'+query+'%', '%'+query+'%']
        if self.sample_ids is not None:
            sql += ' AND x.sample_id IN ('+','.join('?' for _ in self.sample_ids)+')'
            parameters += self.sample_ids
        with self.knowledge.connection() as db:
            rows = db.execute(sql+' ORDER BY e.date DESC,x.rowid LIMIT 21', parameters).fetchall()
        ids = [row['sample_id'] for row in rows[:20]]
        pairs = self.store.samples(ids)
        versions = {row['experiment_id']: row['revision'] for row in rows[:20]}
        for snapshot, _ in pairs:
            if snapshot.revision != versions[snapshot.id] or self.revisions.get(snapshot.id, snapshot.revision) != snapshot.revision:
                raise ValueError('实验记录已更新，请重新提问。')
            self.revisions[snapshot.id] = snapshot.revision
        return {'samples': [{'sample_id': sample.id, 'code': sample.code, 'batch': snap.draft.title, 'date': snap.draft.date,
                             'revision': snap.revision, 'metrics': sorted({r.metric for r in sample.results})} for snap, sample in pairs],
                'complete': len(rows) <= 20, 'query': query, 'coverage': '所选范围' if self.sample_ids is not None else '当前方向匹配范围'}

    def _read_samples(self, sample_ids):
        pairs, rows = self._records(self._ids(sample_ids))
        return {'sample_ids': list(sample_ids), 'sample_count': len(pairs), 'measurement_count': sum(len(s.results) for _, s in pairs),
                'measurement_counts': dict(Counter(r.metric for _, s in pairs for r in s.results)),
                'source_ids': [row['id'] for row in rows], 'coverage': '所选样品全部已记录条件与测量'}

    def _compare_samples(self, sample_ids, metric=None, descending=True, reference_result_id=None):
        ids = self._ids(sample_ids, 2)
        if not isinstance(descending, bool) or (metric is not None and (not isinstance(metric, str) or not metric.strip())):
            raise ValueError('指标或排序方向无效。')
        _, evidence = self._records(ids, metric)
        comparison = compare_samples(self.store, ids)
        groups = [g for g in comparison.groups if metric is None or g['metric'] == metric]
        if not groups:raise ValueError('所选样品没有该指标，请选择已有指标。')
        if reference_result_id is not None and not any(reference_result_id == r['result_id'] for g in groups for r in g['measurements']):
            raise ValueError('参照测量必须属于本次比较指标。')
        result = {'sample_ids': list(ids), 'source_ids': [row['id'] for row in evidence],
                  'reference_result_id':reference_result_id,'calculation_method':'Decimal精确计算，difference为相对参照测量的差值；null表示该组没有有效参照。',
                  'metric_filter': metric,
                  'sample_count': len(ids), 'measurement_count': sum(len(g['measurements']) for g in groups),
                  'coverage': comparison.scope, 'warnings': comparison.warnings, 'conditions': [], 'groups': []}
        for condition in comparison.conditions:
            result['conditions'].append({'name': condition['condition_name'], 'status': condition['status'],
                'values': {sid: None if value is None else {'value': value.text, 'unit': value.unit} for sid, value in condition['values'].items()}})
        for group in groups:
            rows = group['measurements']
            ranking = []
            if group['status'] == 'comparable' and all(row['kind'] == 'number' for row in rows):
                reference = reference_result_id if any(r['result_id'] == reference_result_id for r in rows) else None
                ranked = rank_measurements(comparison, result_ids=[r['result_id'] for r in rows], descending=descending, reference_result_id=reference)
                ranking = [{'result_id': r['result_id'], 'sample_id': r['sample_id'], 'source_id': r['source_id'], 'value': r['value'].text,
                            'unit': r['value'].unit, 'difference': None if r['difference'] is None else str(r['difference'])} for r in ranked]
            result['groups'].append({'metric': group['metric'], 'unit': group['unit'], 'method': group['method'],
                                    'test_conditions': group['test_conditions'], 'status': group['status'], 'rankings': ranking,
                                    'measurement_count': len(rows)})
        self.ensure_current()
        return result

    def validate_calculation(self, question, cited_ids, observations):
        ranking = bool(re.search(r'最好|最佳|最高|最低|最大|最小|排名|排序|差值|相差|更高|更低|rank|best', question, re.I))
        counting = bool(re.search(r'总共|一共|几次|次数|几个|几项|多少.*(?:样品|测量|记录)|(?:样品|测量|记录).*多少|count', question, re.I))
        if not ranking and not counting:return
        expected = set(self.sample_ids) if self.sample_ids is not None else None
        if expected is None:
            complete = [o['result'] for o in observations if o.get('tool') == 'list_samples' and o.get('status') == 'ok'
                        and o['result'].get('complete') and not o['result'].get('query')]
            if complete:expected = {s['sample_id'] for s in complete[-1]['samples']}
        if not expected:raise ValueError('数量和排名需要明确完整样品范围，请先选择样品或列出完整范围。')
        calculations = [o['result'] for o in observations if o.get('status') == 'ok' and o.get('tool') in ('read_samples', 'compare_samples')
                        and set(o['result'].get('sample_ids', [])) == expected]
        available_metrics = {r.metric for _, sample in self.store.samples(list(expected)) for r in sample.results}
        requested = {metric for metric in available_metrics if metric.casefold() in question.casefold()}
        if ranking:
            supported = set()
            for calculation in calculations:
                group_counts = Counter(group['metric'] for group in calculation.get('groups', []))
                for group in calculation.get('groups', []):
                    rows = group['rankings']
                    if group_counts[group['metric']] != 1 or len(rows) != group['measurement_count'] or {row['sample_id'] for row in rows} != expected:
                        continue
                    if re.search(r'差值|相差', question):rows = [row for row in rows if row['difference'] is not None]
                    if rows and set(cited_ids).intersection(row['source_id'] for row in rows):supported.add(group['metric'])
            if not supported or (requested and not requested.issubset(supported)):
                raise ValueError('排名或差值需要覆盖全部所选样品的同一已确认测试组，并引用对应测量；不同单位、方法、条件或缺测不能混成全范围排名，请缩小可比范围或先人工核对。')
        else:
            supported = set()
            complete_count = False
            for calculation in calculations:
                if not set(cited_ids).intersection(calculation.get('source_ids', [])):continue
                metric_filter = calculation.get('metric_filter')
                if metric_filter is None:complete_count = True
                else:supported.add(metric_filter)
            if not complete_count and (not requested or not requested.issubset(supported)):
                raise ValueError('数量结论需要完整所选样品和对应指标的记录；单项指标不能充当全部测量次数。')

    def ensure_current(self):
        self.files.ensure_current()
        live = self.knowledge.get_evidence_many([s.id for s in self.sources])
        if any(s.id not in live for s in self.sources):raise ValueError('引用来源已删除，请重新提问。')
        if any(self.store.get(identifier).revision != revision for identifier, revision in self.revisions.items()):
            raise ValueError('实验记录已更新，请重新提问。')
