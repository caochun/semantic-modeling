"""Explicit problem-local organization; references resolve to shared knowledge IDs."""
from pydantic import BaseModel, Field


def inline_schema_refs(schema):
    """Expose explicit object types to compatible tool APIs that ignore $defs."""
    definitions = schema.get('$defs', {})

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if '$ref' in value:
            target = value['$ref'].removeprefix('#/$defs/')
            return expand({**definitions[target], **{k: v for k, v in value.items() if k != '$ref'}})
        return {key: expand(item) for key, item in value.items() if key != '$defs'}

    return expand(schema)


class KnowledgeUse(BaseModel):
    ref: str = Field(description="已检索知识的 k_ ID，或本轮 knowledge_updates 的 new:0、new:1 等零基序号")
    role: str = Field(min_length=2, max_length=400, description="该条知识在本次判断中承担什么作用；不能只填写相关")


class LinkEvidence(BaseModel):
    passage_id: str
    quote: str = Field(min_length=8, max_length=1200, description="从已读原文逐字复制的连续摘录；不要改写表格为句子，不添加标点、冒号或省略号；两段内容分成两条证据")


class KnowledgeLink(BaseModel):
    source_ref: str
    target_ref: str
    relation: str = Field(min_length=2, max_length=100, description="有方向的业务联系，例如解释状态术语、限定适用条件；不能仅因共同被引用就连接")
    explanation: str = Field(min_length=8, max_length=800, description="两条知识如何联系，不能从必要条件推导充分结论")
    scope: str = Field(min_length=2, max_length=600, description="这条联系本身的适用范围；复核旧联系时保留其原始范围表述")
    conditions: list[str] = Field(default_factory=list, max_length=10)
    evidence: list[LinkEvidence] = Field(min_length=1, max_length=6)


class ProblemModel(BaseModel):
    objective: str = Field(min_length=5, max_length=1000, description="本轮具体要支持的业务判断")
    scope: str = Field(min_length=2, max_length=600)
    knowledge_uses: list[KnowledgeUse] = Field(default_factory=list, max_length=28)
    links: list[KnowledgeLink] = Field(default_factory=list, max_length=12)
    assumptions: list[str] = Field(default_factory=list, max_length=10, description="尚未证实的假设，不作为已知事实")
    required_inputs: list[str] = Field(default_factory=list, max_length=12, description="应用这个局部模型还需要哪些现场或事件输入")
    boundaries: list[str] = Field(min_length=1, max_length=12, description="即使这些知识成立，仍然不能直接得出哪些结论")


def validate_model_refs(model, candidates, reused_ids):
    expected = {f"new:{i}" for i in range(len(candidates))} | set(reused_ids)
    refs = [item["ref"] for item in model["knowledge_uses"]]
    errors = []
    if len(refs) != len(set(refs)):
        errors.append("同一知识只能列一次用途")
    if set(refs) != expected:
        errors.append("knowledge_uses 必须恰好覆盖全部新增和声明复用的知识；不能引用其他条目")
    seen_links = set()
    for link in model["links"]:
        pair = (link["source_ref"], link["target_ref"], link["relation"])
        if link["source_ref"] not in expected or link["target_ref"] not in expected:
            errors.append("知识联系引用了本次模型之外的条目")
        if link["source_ref"] == link["target_ref"]:
            errors.append("知识联系不能连接自身")
        if pair in seen_links:
            errors.append("知识联系重复")
        seen_links.add(pair)
    return errors
