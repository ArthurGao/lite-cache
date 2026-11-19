# lite-cache 设计文档

**状态：S0–S2 完成（L1 v0.1.0 已发布，lite-rag S18 已接 L1）；v0.2.0（2026-11-20）：S3 代码 + lite-model 0.3.0，L2 仍关；S3a（L2 机制）完成；S3b 评测完成（2026-11-20）：bge-m3 + guard 过不了 gate 的可用阈值，L2 保持关闭（`thresholds.json` 为空，§0、§8）。S3c（verifier，2026-11-20）：qwen3:8b verifier 也过不了 gate（PAWS 在 0.99 时假命中仍 4–8%），L2 保持关闭（§13.5）。** 2026-11-19 重排阶段：L1 接 lite-rag 建索引和 `ask()` 两条路径，L2 先用问题对评测校准、不等回放（§0、§11）。
与代码冲突时，在同一次改动里更新本文，并在 commit 里说明原因。

## 0. 决策记录

决策的理由只写在这里，其它章节引用（§0）。

| 日期 | 决策 | 原因 |
| --- | --- | --- |
| 2026-11-15 | 形态：library；HTTP 服务和代理按需做（§7） | 与 lite-rag / lite-mem 一致；现有接入方全是 Python |
| 2026-11-15 | 第一个接入方：lite-rag `ask()`（2026-11-19 被取代：建索引和 `ask()` 两条路径，见下） | 重复问题最可能出现在问答 |
| 2026-11-15 | 语义匹配只在应用层做，透明代理只做 L1 | 代理分不清用户问题和注入的 RAG 上下文 |
| 2026-11-15 | provider prompt caching 不在范围内 | 它属于 client / gateway 配置；KB 实测它只在多轮长上下文里生效（`KB/global/llm-evals/evals/2026-10-05-tenderted-prompt-caching.md`） |
| 2026-11-15 | 不挂在 llm-steward 上 | steward 自己的 `kb.*` / `memory.*` role 会调 LLM，但它不替其它应用转发 LLM 调用（没有 completions 路由），也不记 token |
| 2026-11-15 | 不重复造轮子（"运行时不 import 兄弟项目"一句已被 2026-11-19 的"依赖 lite-model"取代）：canonical JSON 照搬 lite-eval `canonical.py`；migration 运行器逐字 vendor lite-rag `db.py`（e42fc93）；schema 校验照搬 lite-mem（标识符约束加保留名拒绝，§5）；Embedder 由调用方注入，lite-cache 不做 embedding 缓存 | 运行时不 import 兄弟项目：lite-rag 在 S4 要依赖 lite-cache，反过来就成了循环依赖；为了 30 行 canonical JSON 把 lite-eval（带 Viewer 的 wheel）拉进运行时依赖不划算。lite-eval 只作为 `eval` extra 和 dev 依赖用于评测（S2 起），与 lite-rag、lite-mem 的用法相同。vendor 的文件头注明来源和 commit，并用 test vector 和来源比对 |
| 2026-11-15 | 先做 L1 和 eval 基线（S1、S2）；L2 等回放数据证明值得再做（S3）（2026-11-19 被取代：L2 先用问题对评测校准，见下） | 重复率低的流量里，L1 可能就够了 |
| 2026-11-15 | 缓存不解读内容：payload 不透明，校验由调用方的 `call()` 负责（2026-11-19 起由 lite-model 负责：截断、空回复、不合 schema 都是异常），写入规则是可替换的谓词 | lite-mem / llm-steward 返回结构化对象或 tool 输入，`finish_reason` 和带日期的模型快照名因 provider 而异 |
| 2026-11-15 | 已付费调用之后的写入失败不让请求失败；读失败才抛错 | 不能丢掉已经付过钱的答案；错误通过返回值和日志暴露 |
| 2026-11-15 | S1 只做 PostgreSQL | 同级项目默认用 PG，L2 需要 pgvector；先不维护两个后端 |
| 2026-11-15 | 项目 KB 放在仓库内 `kb/` | 用户指定 |
| 2026-11-15 | 存可选的用户问题原文 `question`，不存完整请求，`question` 不进 key | 排查和管理界面要看得到答案对应的问题，S3 的 L2 直接复用这一列；完整请求会让表膨胀 10–50 倍，隐私暴露面也更大（用户选 A） |
| 2026-11-19 | 接入方式：实现 lite-model 的 `Hook`（`before` 查、`after` 写），不再包住调用方的 `call()`，不提供 `get_or_call` | 所有接入方（lite-rag、lite-mem、llm-steward）都经 lite-model 调模型（2026-11-19 迁移完成），hook 是唯一的必经点；`Call.request` 是实际发出请求的只读深拷贝，key 描述的就是发出去的请求；截断、空回复、不合 schema 在 lite-model 里已经是异常，不会到缓存这里 |
| 2026-11-19 | 运行时依赖 lite-model | hook 协议、`Call`、回复和 `Usage` 类型在那里；lite-model 不依赖 lite-cache，没有循环。canonical JSON 仍 vendor 自 lite-eval（lite-model 只在 `schema_hash` 里内联了同样的规则，没有导出） |
| 2026-11-19 | 存储仍是 PostgreSQL；不用 Redis，不用 LiteLLM Proxy 的 cache | LiteLLM cache：默认调用不经过 Proxy（lite-model 规则 2，Proxy 可选）；命中时 lite-model 标不出 `cached`、把省下的 token 记成花掉的；没有必填 scope，只能按 key 删除；semantic 模式对整段 prompt 做 embedding，RAG 上下文占大头（§0 2026-11-15）。Redis：一次模型调用 1–20 s，PG 本地查询约 1 ms，快没有意义；所有 lite 模块已有 PG + pgvector，Redis 是新的基础设施；scope 前缀失效、upsert 计数、L2 向量检索在 SQL 里都是现成的。`Store` 是 Protocol，以后真有多实例共享热缓存的需要再加 |
| 2026-11-19 | 不做自己的 HTTP 代理（§7） | LiteLLM Proxy 已是可选的 OpenAI 兼容入口；非 Python 的接入方用它（不带本缓存） |
| 2026-11-19 | 失败的调用不计数，`skip:<reason>` 只来自 `accept` 谓词 | hook 看不到失败的调用（lite-model 不加 `failed()` hook，见它的 §15）；失败调用的用量在 lite-model 的异常上 |
| 2026-11-19 | 评测绕过缓存（不传 policy 或 `refresh=True`） | 多次 trial 必须各自调用模型，缓存会让第 2 次复用第 1 次的答案，看不到波动（KB 方法论 §4） |
| 2026-11-19 | L1 的接入方是 lite-rag 的两条路径：建索引的 `contextual` / `graph` role 和 `ask()` 的 `answer` role（S2）；L2 只给 `ask()`（S4） | 建索引是固定流程：同一批 chunk 重新入库、换一个与切块无关的配置重建、eval 多次跑同样的切块，发出的请求完全相同，L1 命中率高且不会答错；`ask()` 是用户问答，L1 只在问题和检索结果都相同时命中，L2 的价值在这里（用户选"两个都接"） |
| 2026-11-19 | 需要 L2；L2 不等回放数据，先用问题对评测校准阈值和 guard，过 gate 即可上线（默认关）；回放评测改为按需，有真实日志时再估算收益 | 误命中率（安全性）能用标注好的问题对测出来；真实收益取决于真实流量里的重复分布，模拟数据给不出来，只能等日志（用户要求 L2） |
| 2026-11-19 | 建索引的 role 只用 L1；lite-rag 的两条路径 scope 都是 `literag/<namespace>`（同日改：原为 `literag/<namespace>/<kb 名>`） | 索引身份要求可复现，相似不等于相同。scope 到 kb 时建索引几乎不命中：同一 kb 重新入库未变的文档是 no-op，换配置重建（`reindex --into` 新 kb）和 eval 的新 kb 都是新 scope；到 namespace 为止，同一租户内换 embedding 或与切块无关的配置重建都能复用 contextual / graph。namespace 是 lite-rag 的租户边界（`engine.py` 文档头），不同 namespace 永不共享。代价：不能按 kb 单独清除，只能按 namespace 或 role（用户 2026-11-19 选 A） |
| 2026-11-19 | L2 必须显式打开：`Policy(semantic=True)`，并且 context 里同时有 `question` 和 `partition`；只给 `question` 不触发 L2 | `question` 在 L1 里也会传（排查、管理界面），不能因为传了原文就承担误命中的风险 |
| 2026-11-19 | 阈值按 embedder 校准：一个阈值只对校准它的 embedder（`model_id`）有效，配置里没有该 embedder 的阈值时，`semantic=True` 在启动时报错 | 不同 embedding 模型的相似度分布不同，一个模型的阈值套在另一个上没有意义；失败要在启动时可见 |
| 2026-11-19 | 分区内精确检索（余弦），不建 ANN 索引 | 分区由参与回答的文档版本算出，一个分区里的条目很少；按 `(scope, partition, embedder)` 的 btree 过滤后逐行算相似度足够，也省掉了不同维度的 HNSW 索引；分区变大时再加 |
| 2026-11-19 | `ask()` 的 L2 partition 是"送进模型的上下文完全相同"：检索结果**按顺序**的 `chunk_id@content_hash` 列表的 sha256；同样的 chunk 换了顺序也不命中（方案 A）。问题日志和 `stats` 显示"同样的 chunk、不同顺序"多时，再改为方案 B（同一组 chunk、命中时按当前顺序重排引用编号） | `ask()` 的回答带 `[n]` 引用，编号指向本次检索的 chunk 列表（`answer.py` `check_citations`）；L2 命中返回的是另一个问题的答案，只有上下文完全相同，引用和依据才都对。按 kb 版本分区（方案 C）会让答案引用这次没检索到的 chunk，否决。代价是命中少，所以 L1、L2 命中率分开统计，日志记下 partition，真实使用中看得到 L2 有没有用（用户 2026-11-19 选 A） |
| 2026-11-19 | lite-rag 以 release wheel 依赖 lite-cache（`cache` extra） | 与 lite-model、lite-eval 的依赖方式一致（workspace `AGENTS.md` "How pins work"） |
| 2026-11-19 | 接入 lite-rag 不等 lite-model 0.3.0 | 建索引经过 `Bounded`：命中也占一个槽位，但立刻返回，吞吐几乎不变；`ask()` 不经过 `Bounded`。0.3.0 只是 llm-steward（async）接入的前置 |
| 2026-11-19 | L2 的公开问题对数据：PAWS（英文，约 8000 对测试）和 PAWS-X 中文（约 2000 对测试），加按 lite-rag 语料生成的领域集；不用 LCQMC、Quora QQP | PAWS 系列的许可写明"可用于任何目的"（google-research-datasets/paws 的 LICENSE），负例是专为难倒模型做的词序和词替换对抗样本；LCQMC、QQP 更接近真实提问，但查不到明确的数据许可（设计 §8 "许可不允许的不用"）。问题形式和数字 / 否定 / 实体类难负例由领域集补（用户：选最好的，中文不是最重要） |
| 2026-11-19 | wheel 的元数据里 lite-model 写成直接 URL（`lite-model @ https://…/v0.2.1/…whl`），不用 `[tool.uv.sources]` | S1.5 的冒烟测试发现：sources 不进 wheel 的元数据，宿主单独装 lite-cache 的 wheel 时解析不到 lite-model（它不在 PyPI）。直接 URL 让 wheel 自己能装；升级 lite-model 要发新版 lite-cache，与 workspace 里"同一个 lite-model 版本"的规则一致 |
| 2026-11-20 | L2 不上线：bge-m3 校准未得到可用阈值，`thresholds.json` 保持为空；0.2.0 不发 | 生成集在自身 dev 阈值 0.985 下 test 假命中 5/334（gate 允许约 1），0.95 时 PAWS 英文假命中 80%；阈值放到 1.000 时三份都能过 gate，但命中率 0–3%，等于 L1；现在阈值上限 0.99（`kb/eval-results.md`）。只改一个词就让答案变的问题对，余弦仍在 0.98–0.99，阈值分不开 |
| 2026-11-20 | L2 检索还按 role 和 kind（text / structured）过滤；零向量是 `EmbedderError`，NaN 相似度不算命中 | 终审发现：只按 (scope, partition, embedder) 时，同一问题和分区下一个 role 会拿到另一个 role 的回答，structured 调用可能校验通过一份别的 schema 的 payload；零向量与任何向量的余弦是 NaN，PostgreSQL 降序把 NaN 排第一、Python 里 `NaN < t` 为假，会把一条答案发给所有问题。prompt 或 schema 的其余差别仍要由调用方编进 partition |
| 2026-11-20 | 发 0.2.0：S3 的代码（L2 机制，仍关）、依赖 lite-model 0.3.0；Proxy 自己缓存的回复（`reply.cached`）照样写入，但不计为花费 | 宿主升到 lite-model 0.3.0 时，lite-cache 的 wheel 必须指向同一个 lite-model URL，否则 uv 解析冲突；S3 加了公开 API（`Embedder`、`EmbedderError`、`Policy.semantic`），按 semver 是 minor。上面"0.2.0 不发"指的是带阈值、开着 L2 的发布，这一版不是 |
| 2026-11-20 | L2 命中前加 verifier：本地 LLM 判定"两问是否同一个答案"，先用 qwen3:8b 校准，过了 gate 再看 qwen3:1.7b 能不能也过（§13） | bge-m3 校准失败的原因是只改一个词的问题对余弦仍在 0.98–0.99（§8 结果）；这类差别要读懂句子才分得开。8b 过不了时 1.7b 基本也过不了，先量 8b 省一次白跑；verifier 只在少数候选上调用，8b 的延迟可以接受 |

## 1. 概述

LLM 调用的响应缓存：同一个问题再次出现时，直接返回上次的答案，不再消耗 token。足够相似的问题
从 S3 起也能命中。

- **L1 精确缓存**：同一请求只调用一次模型。请求相同指 scope、role、model、messages、params 都相同。
- **L2 语义缓存（S3）**：调用方显式打开（`Policy(semantic=True)`）并给出问题和分区，在分区内按问题向量
  的相似度命中，再过 guard（数字、否定、实体）。阈值按 embedder 由 lite-eval 在标注好的问题对上校准，用
  gate 守住误命中率（§8）。
- **可度量**：按天、scope、role 汇总命中、未命中和省下的 token。
- **可失效**：TTL、按 scope 或 role 清除、按 key 驱逐（例如用户点踩）、refresh。

非目标：provider prompt caching（§0）；在透明代理里做语义匹配（§0）；缓存依赖对话历史的追问
（调用方只对自足的问题使用 L2）；single-flight（以后再做，见 §11）；自己的 HTTP 代理（§0、§7）。

## 2. 数据流

```
宿主 ── with call_context(scope=…, cache=Policy(…)): model.text / structured(…) ──▶ lite-model
   lite-model 构造 Call（role、Target、messages、request、schema、context）
   CacheHook.before(call)
     1. context 里没有 scope 或没有 cache → None：不缓存，照常调用
     2. key = sha256(canonical(…))（§3.1）
     3. refresh 为假时 store.get(key, now)：条目存在且未过期 → 计 hit，返回当初的回复
        （lite-model 标 cached=True，来源仍是当初回答的模型）
     4. [S3] L1 未命中、policy.semantic 为真、context 带了 question 和 partition →
        embed(question) → 在 (scope, partition, embedder, role, kind) 内逐行算余弦 → 最高者 ≥ 该 embedder 的阈值
        → guard 通过 → 计 hit_semantic，返回那条的回复；否则把向量留给 after 写入时用（不 embed 第二次）
   lite-model 发请求；截断、空回复、不合 schema、调用失败都抛异常：不经过 after，不写、不计数
   CacheHook.after(call, reply)
     5. policy.accept(reply)：None → 写入并计 stored（同一个事务）；否则计 skip:<reason>
```

## 3. 接口

```python
Policy(ttl_s=None, accept=None, refresh=False,   # accept(reply) -> str | None；None = 全部写入
       semantic=False)                            # S3：True 才走 L2（§0）
CacheHook(store, clock=…, embedder=None)          # lite_model.hooks.Hook，name "lite-cache"；
                                                  # S3：embedder 由调用方注入（Embedder Protocol）
model.hooks.register(CacheHook(open_store()))     # model = lite_model.build(…)（LiteModel）
with call_context(scope="acme/u42", cache=Policy(ttl_s=86_400),
                  question="手冲咖啡的粉水比？",     # 可选，存进 entries.question，不进 key
                  partition=None):                # S3：L2 的分区，由调用方算出
    reply = model.text("answer", messages)        # 命中时 reply.cached 为 True
hook.invalidate(scope=?, role=?, key=?) -> int
hook.stats(scope=?, since=?) -> list[RoleStats]
hook.store_errors -> int                          # 写或计数失败的次数（§3.5）
open_store(settings=None) -> Store
```

- **`cache` 的取值**：`Policy`，或 `True`（等于 `Policy()`，lite-model 的文档和示例用的是 `cache=True`）；其它
  类型（包括 `False` 以外的真值）抛 `TypeError`，`False` / 不传等于不缓存。
- **缓存与否由调用点决定**：`call_context` 里没有 `cache`（一个 `Policy`）就不走缓存，没有 `scope` 也
  不走（以防跨租户，lite-model §15）。role 只是 key 的一部分，也是统计维度，不查注册表。
- **`question`** 是可选的用户问题原文，通常几十个字，不是整个请求。它存进 `entries.question`，用于排查、
  管理界面和点踩时显示"这条答案回答的是什么"；S3 的 L2 也用这一列做向量检索。它**不进 key**。涉及敏感
  内容的调用方可以不传。
- **存什么**：回复的值（`text()` 的文本，`structured()` 的值按 `model_dump(mode="json")`）、`Provenance`
  和 `Usage`。缓存不解读它。`structured()` 命中时用 `call.schema` 还原；还原失败（schema 的 validator 变了而
  JSON schema 没变）当作未命中，记 warning，照常调用并覆盖。
- **出处**：命中时返回的回复带当初的 `Provenance`（实际回答的模型）和 `Usage`（这次省下的，lite-model §9），
  `cached=True`；`entries.created_at` 是写入时间。UI 必须标注"缓存答案"。
- **L2（S3）**：`semantic=True` 但缺 `question` 或 `partition` 时只走 L1，不报错（与"没有 policy 不缓存"同理，
  由调用点决定）；`semantic=True` 而 hook 没有 embedder，或配置里没有这个 embedder 的阈值，在注册 / 第一次调用
  前就报 `ConfigError`（§0）。L2 命中返回的是另一个（相似）问题的答案：`cached=True`、来源和 usage 是那条的，
  统计里记为 `hit_semantic`，与 L1 的 `hit` 分开（§3.4）。被命中的那条的 `question` 写进 warning 级以下的日志，
  便于排查误命中。
- **Embedder Protocol（S3）**：`model_id: str`、`dim: int`、`embed_query(text) -> list[float]`。`model_id` 必须
  涵盖一切会改变向量的东西：provider、模型、**query 前缀**（lite-rag 把 prefixes 算进索引身份）；阈值按它
  查（§5）。lite-rag 的 `Embedder` 没有 `model_id`（有 `provider`、`model`、`dim`、`prefixes`，
  `embedding.py:36-54`），所以由 lite-rag 在 S4 提供一个包住 `CachingEmbedder` 的 adapter，把
  `provider/model` 和 query 前缀的 hash 拼成 `model_id`。lite-cache 不缓存 embedding。
- **宿主要暴露注册入口**：lite-rag 的 `build_model` 返回类型是 `Model`（不含 `.hooks`），lite-mem、
  llm-steward 包在自己的 adapter 里；接入时各自开一个注册 hook 的口子（§9）。

### 3.1 Key

key 是 canonical JSON 的 sha256，规则与 lite-eval `canonical.py` 相同：所有字符串（包括 dict 的 key）
做 NFC，`sort_keys`，紧凑分隔符，`ensure_ascii=False`，`allow_nan=False`（遇到 NaN 抛错），UTF-8 编码。
NFC 只合并 Unicode 意义上等价的写法，不改变语义。内容为：

`{"v": 2, scope, role, backend, model, messages, params}`

- **scope 必填**，格式是一段或多段、用 `/` 分隔，每段为 `[A-Za-z0-9_.-]+`，从粗到细（项目、租户 / namespace、
  kb、用户……；lite-rag 的 namespace 本身含 `/`，所以段数不固定）。不同 scope 永远不共享
  答案，以防跨租户泄露。注入了 lite-mem 记忆的个性化回答，scope 必须细到 user。
- **backend、model** 取自 `Call.target`：配置里的 backend 名字和**请求的**模型（lite-model 统一为
  `backend:model`；两个 OpenAI 兼容厂商的同名模型不会撞）。role 换了模型后，旧条目自然不再命中。
- **messages、params** 就是 `Call.messages` 和 `Call.params`：lite-model 实际发出的请求，只去掉 model、
  messages 和传输层的 `extra_headers`。system prompt、动态上下文、tools、`response_format` / schema、
  `reasoning_effort` 都在里面，所以 key 覆盖了决定答案的全部输入；hook 拿到的是只读深拷贝，改不了发出去的
  请求（lite-model §10）。除 NFC 之外不做归一化。
- **可路由的 role**（模型是 LiteLLM 的路由别名）：key 里是别名，命中时返回当初实际回答的模型（来源不丢）。
  同一个问题以后不再重新路由，这是缓存的本意。
- **请求之外的变化不在 key 里**：世界知识过时（"今天的汇率"）、tool 的实现换了版本。这类场景要么用
  `Policy.ttl_s`，要么不传 policy（不缓存）。由调用点决定（§0）。
- **不需要上下文指纹。** RAG 检索到的 chunk 原文就在 messages 里，文档一改，key 跟着变。
- 修改 canonical 规则或 key 的内容必须升 `v`，`v` 一变，所有旧 key 都失效（2026-11-19：1 → 2，`model_id`
  拆成 backend 和 model）。

### 3.2 写入规则

`Policy.accept(reply) -> str | None`，返回 None 表示写入，否则返回 skip 原因。默认（`accept=None`）全部写入：
截断（`Truncated`）、空回复（`EmptyReply`）、不合 schema（`BadStructuredReply`）在 lite-model 里都是异常，
不会到 `after`。调用方可以传自己的谓词（例如拒绝某些业务上无效的值）。

temperature 进 key，但不阻止写入：采样得到的答案仍然有效，能不能复用由调用点决定；不能复用就不要传 policy。

### 3.3 失效

- **TTL**：`Policy.ttl_s`。过期的条目读不到。`put` 时顺手清理，每小时最多一次，每次最多删
  1000 行；`litecache purge` 可以手动清理全部。
- **`invalidate(scope=, role=, key=)`**：至少给一个条件，多个条件取交集。scope 按路径前缀匹配：
  `a` 匹配 `a` 和 `a/…`，不匹配 `ab`。SQL 写成 `scope = :p OR (scope >= :p || '/' AND scope < :p || '0')`，
  `'0'` 在字节序里紧跟 `/` 之后。这样能用上 btree 索引，也绕开了 `LIKE` 把 `_` 当通配符的问题。
  **前提是按字节序比较**：PG 的文本比较跟随 collation，所以 `scope` 列声明为 `COLLATE "C"`，索引和
  比较都用它（§4）。测试库的默认 collation 必须不是 C，这样才能证明列级 collation 生效了。
- **`Policy.refresh=True`**：跳过读，照常写，用于强制重算。

### 3.4 计数

不记录逐条事件，而是在 `stats(day, scope, role, outcome)` 上做 upsert 累加：
`n`、`saved_input_tokens`、`saved_output_tokens`、`spent_input_tokens`、`spent_output_tokens`。
upsert 用 `INSERT … ON CONFLICT DO UPDATE SET n = stats.n + 1 …`，并发调用不会丢失计数。

**每次带 policy 的调用只计一行，outcome 互斥：**

| outcome | 含义 | 累加 | 事务 |
| --- | --- | --- | --- |
| `hit` | L1 从缓存返回 | saved_* += 条目原始 usage | 只有计数这一次写 |
| `hit_semantic` | L2 从缓存返回（S3） | saved_* += 那条的原始 usage | 只有计数这一次写 |
| `stored` | 调了模型并写入了缓存（含 refresh） | spent_* += 本次 usage | 写条目和计数在同一个事务 |
| `skip:<reason>` | 调了模型，`accept` 拒绝 | spent_* += 本次 usage | 只有计数这一次写 |

- 调用失败（lite-model 抛异常）时不计数：hook 看不到失败的调用；它的用量在异常的 `usage` 上（lite-model §9）。
- `hit_rate = (hit + hit_semantic) / (hit + hit_semantic + stored + skip:*)`，L1、L2 各自的命中率也分开给出；`stats()` 按 role 汇总，同时给出各 skip 原因的次数。
- 表的大小由天数、scope 数、role 数、outcome 数决定，不会随请求量无界增长。

S2 的 replay 评测需要的是查询日志，那是调用方的日志，不由本表提供。

### 3.5 故障

- **读失败**（`before` 里的 `get`）：抛 `StoreError`。lite-model 把 hook 的异常原样抛给调用方，不发请求
  （lite-model §10），不静默直连（KB：故障必须可见）。要不要降级为直连，由调用方决定。
- **写或计数失败**（`after` 里）：不抛错——`after` 抛错时 lite-model 会把这次已经付费的回复丢掉。记一条
  warning 日志，`hook.store_errors` 加一，回复照常返回。不重试。
  - `stored` 的事务失败：条目没写入，这次也不计数，下一次同样的请求会再调一次模型。
  - `hit` 的计数失败（在 `before` 里）：同样只记 warning 和 `store_errors`，照常返回命中的回复。
  - 回复上没有 `store_error` 字段：回复是 lite-model 的类型，缓存的状态看日志和 `store_errors`。

## 4. 存储

`Store` Protocol 只含 hook 用到的：`get(key, now)`、`put_and_count(entry, now, day, spent=)`（`stored` 的写入和计数
同一事务）、`count(...)`、`invalidate(...)`、`stats(...)`。`PgStore` 另有 `put(entry, now)` 和 `purge_expired(now, limit)`
（CLI 和工具用）。写入后顺手 purge，purge 失败只记 warning，不让已提交的写入报失败。

PostgreSQL 实现：
- schema 默认 `lite_cache`；migrations 是编号的 SQL 文件；执行时 `search_path = <schema>, public`。连接池里每个连接
  建立时设一次 `search_path`（pool 的 `configure`），不在每个事务里 `SET LOCAL`：每次查缓存少一次往返（S1）。
  连接 autocommit，单条语句一次往返；连接池最多 8 个连接，取连接最多等 5 s（数据库在启动后断开时很快报
  `StoreError`，而不是每次调用都等 30 s）。
  migration 运行器是逐字 vendor 过来的 lite-rag `db.py`（§0）。
- `open_store()` 先不加锁地查已应用的最新版本，落后时才拿 advisory lock 执行 migrate。每个进程只
  open 一次。
- 表：
  - `entries(key PK, scope text COLLATE "C", role, backend, model, question text NULL, kind text（text |
    structured）, payload jsonb, provenance jsonb, input_tokens, output_tokens, reasoning_tokens, cost_usd NULL,
    created_at, expires_at)`。在 `(scope)` 和 `(role)`
    上建索引，scope 索引同样用 C collation（§3.3）；在 `expires_at` 上建 partial index。
  - `stats(day, scope, role, outcome, n, saved_*_tokens, spent_*_tokens)`，主键为
    `(day, scope, role, outcome)`，口径见 §3.4。
- 不存完整请求：key 已经是请求的 hash，再存一份 RAG 上下文只会让表膨胀。只存调用方给出的
  `question`（§3）。
- S3 在 `entries` 上加列（新的 migration）：`partition text NULL`、`embedder text NULL`（`model_id`）、
  `question_embedding vector NULL`（不定维度，pgvector 允许；不建 HNSW，§0）；加 btree 索引
  `(scope, partition, embedder)`，scope 同样用 C collation。只有 `semantic=True` 且三者齐全的写入才填这三列。

SQLite 后端以后按需再加，复用同一套 contract 测试。

## 5. 配置

环境变量前缀 `LITECACHE_`。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `LITECACHE_DATABASE_URL` | — | 必填 |
| `LITECACHE_SCHEMA` | `lite_cache` | 两条校验都要有：标识符正则 `^[a-z_][a-z0-9_]{0,62}$`；不能是 `public`、`information_schema` 或 `pg_*`。后一条照搬 lite-mem `own_schema`，前一条在 lite-mem 里是另一个 pydantic 约束，也一起照搬。字段名写成 `schema_name`（以免遮蔽 pydantic 的同名属性），只从 `LITECACHE_SCHEMA` 读（`validation_alias`）。不用 lite-rag 的 `alias="schema"`：在顶层 `BaseSettings` 上，pydantic-settings 会从不带前缀的 `SCHEMA` 读它、反而读不到 `LITECACHE_SCHEMA`（S0 实测） |
| `LITECACHE_TEST_DATABASE_URL` | — | 只给测试用，测试只使用 schema `lite_cache_test` |

| `LITECACHE_SEMANTIC_THRESHOLDS` | 空 | S3：JSON 对象 `{embedder model_id: threshold}`；值只能来自 §8 的校准运行，旁边在 `kb/eval-results.md` 记着 run id。仓库里的默认值文件 `src/lite_cache/thresholds.json` 收已校准的条目，环境变量覆盖它 |

policy 不放进环境变量，它们是代码里的决定（§3）。

## 6. CLI

`litecache migrate | stats [--scope] [--since] | invalidate (--scope | --role | --key) | purge | config`

`config` 打印最终组装出的配置，database URL 中的密码会脱敏。

## 7. HTTP 服务和代理

2026-11-19 起不做自己的代理（§0）：非 Python 的接入方用 LiteLLM Proxy（lite-model 的 `litellm` 后端同一个
Proxy），不带本缓存。**Proxy 的 cache 保持关闭**（§0；lite-model §7.2）。

HTTP 服务（查询和写入的 HTTP 版）只在出现非 Python、又必须共享本缓存的接入方时再设计；现在没有。

## 8. 评测（lite-eval）

按 lite-eval rule 9，target adapter 写在本仓库的 `eval/lite-eval/`。其它项目的模型评测运行时不传 policy
（或 `refresh=True`），以免 trial 之间共享答案（§0）。

- **pairs**（S3，L2 上线的前提）：回答"L2 安全不安全、阈值和 guard 怎么定"。数据集是
  `(q1, q2, same_answer: bool)` 三元组，含难负例：增和删、数字不同、否定、实体不同。被评测的是
  "embedder 余弦 ≥ 阈值且 guard 通过"这一判定本身，不调用 LLM。
  - 数据来源（两份，分开评测，都要过 gate）：
    1. **公开的人工标注集**：PAWS（英文，`paws_wiki_labeled_final` 的 test）和 PAWS-X 中文（`x-final` 的 zh test），
       两份分开判定（validation 作 dev，test 作 test）。许可已核实（§0 2026-11-19）；LCQMC、QQP 因许可不明不用。
       数据从 Hugging Face 的 google-research-datasets 下载（原 storage.googleapis.com 的 tar 包已 403），
       不提交进仓库，放在 `eval/pairs/data/`（gitignore），每个文件的 sha256 记在 `data/public/sources.json`。
    2. **按 lite-rag 语料生成的领域集**：对 lite-rag `eval/corpus*` 的文档，用 LLM 生成同义改写（正例）和
       改数字、加否定、换实体的版本（难负例），再人工抽查至少 10%。lite-eval 会标 `synthetic_dataset`，
       结果照实写"合成数据"。生成用本地模型（Ollama），不用订阅通道。
  - 指标：
    - `false_hit_rate` = 被判为命中的负例对数 / 负例对总数（主指标）；
    - `hit_rate` = 被判为命中的正例对数 / 正例对总数。
  - 数据切分：阈值和 guard 规则只在 dev 集上定，gate 只在 held-out test 集上判定，两个集合不重叠。
  - gate（每份 test 集各自判定）：负例至少 300 对，难负例占一半以上；`false_hit_rate ≤ 0.01`，且 95% Wilson
    上界 `≤ 0.02`。上界、样本数、embedder 的 `model_id`、run id 都记进 `kb/eval-results.md`；过了 gate 的
    阈值写进 `thresholds.json`（§5）——但过 gate 不等于可用：阈值高到几乎不命中时也会过，所以写入要 owner 看过命中率后
    用 `calibrate.py --write-threshold` 明确决定。
  - 结果（2026-11-20，bge-m3:567m）：FAIL。两份 PAWS 在 [0.80, 0.99] 里没有 dev 阈值，生成集按自身 dev 阈值 0.985
    test 假命中 5/334；L2 保持关闭（§0）。数字和分析见 `kb/eval-results.md`。
  - 评测工具：`eval/pairs/{fetch,generate,calibrate}.py`，suite `eval/lite-eval/suites/pairs-{public,generated}.yaml`，
    target `lite_cache.eval_target:pairs`（只输出余弦，只取决于 embedder；guard 在 `calibrate.py` 里按当前代码套用，
    改 guard 不用重跑）；纯逻辑（切分、Wilson、选阈值、gate）在 `lite_cache.pairs`，有单元测试。阈值只在
    [0.80, 0.99] 里选，更高的阈值几乎不命中，算"没有阈值"。
- **replay**（按需，有真实日志时）：一份真实查询日志按时间回放。指标是 L1 的命中率、L1+L2 的命中率、省下的
  token。用来估算真实收益，不再是 L2 的前提（§0）。日志来自 lite-rag `ask()` 的可选提问日志（§9）。

## 9. 与其它项目的关系

| 项目 | 接入方式 | 阶段 |
| --- | --- | --- |
| lite-rag | 以 `cache` extra 提供（release wheel，默认关）。前置：`build_model` 暴露 `LiteModel`（或一个注册 hook 的入口）；`Completion` / `Answer` 带上 `cached`（现在 `Completion` 只有 text、model_id、usage，`llm.py:44-49`），否则 S2 的"回答标注缓存"无从做起。**S2，L1 两条路径**：① 建索引的 `contextual` / `graph` role，`call_context(scope="literag/<namespace>", cache=Policy())` 包住这两个 role 的调用（不传 `question`）；② `ask()` 的 `answer` role，scope 同样是 `literag/<namespace>`（key 里有检索到的 chunk 原文，同一租户两个 kb 检索到相同 chunk 时答案相同），传 `question`。scope 不到用户：`ask()` 只依据 kb 文档回答，没有个人化内容。`Bounded.map` 的工作线程不继承 contextvar（lite-model 0.2.1），lite-rag 的 `BoundedLLM.map` 自己按项 `copy_context()`，lite-model 0.3.0 修根上。`ask()` 加可选的提问日志（JSONL：时间、scope、question；S4 起加 partition），默认关，给以后的 replay 用。**S4，L2 只给 `ask()`**：`Policy(semantic=True)`，partition 是送进模型的检索结果按顺序的 `chunk_id@content_hash` 列表的 sha256（§0 方案 A，顺序不同也不命中）；前置：`Hit` 带上所属文档的 `content_hash`（`chunk_id` 在文档改动后不变，现在还没有）。截断已由 lite-model 报错，不再需要 `finish_reason`。每个阶段开始前，按 lite-rag 的实际流程写出 adapter 契约（scope、question、partition 从哪来） | S2、S4 |
| lite-eval | pairs 评测（S3）；replay 评测（按需） | S3、按需 |
| llm-steward | `kb.risk` / `kb.worthiness` 用 L1。steward 是 async 的：需要 lite-model 的 `AsyncHook`（lite-model §10，计划 0.3.0），同步 hook 里的 psycopg 查询会阻塞 event loop | 以后 |
| lite-mem | extract / resolve 只用 L1，让重试不重复花钱；不用 L2；评测时不传 policy | 以后 |

## 10. 项目结构

```
src/lite_cache/
  types.py     Policy / RoleStats
  canonical.py 从 lite-eval vendor 过来
  keys.py      cache_key(call)
  hook.py      CacheHook（lite-model Hook；async 版本等 lite-model 0.3.0，llm-steward 接入时）
  semantic.py  S3：Embedder Protocol、分区内检索、guard
  thresholds.json  S3：按 embedder 已校准的阈值（§5）
  config.py    Settings（LITECACHE_）
  db.py        PG 连接和 migrations
  store.py     Store Protocol 和 PgStore
  migrations/  NNNN_*.sql
  cli.py
tests/unit/  tests/contract/
eval/lite-eval/  pairs（S3）和 replay（按需）的 target 与 suite
kb/          项目 KB（发现、评测数字、教训）
```

## 11. 阶段与验收

每个阶段收尾都按 AGENTS.md Workflow 第 4 步执行：`/simplify`，然后 Codex 只读 review，然后修复，
最后重跑全部检查。

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| S0 | 骨架、CI（PG 服务）、migration 框架；依赖 lite-model（git tag 的 wheel） | ruff、mypy、pytest 全绿 |
| S1 | L1：key、`CacheHook`、写入规则、TTL、invalidate、计数与 stats、PgStore、CLI | 用 lite-model 的 `FakeModel` 包在 `LiteModel` 外（或 `build` 加假 transport）：同一请求第二次不发请求，回复 `cached=True`、来源和 usage 是第一次的；scope、role、backend、model、messages、params（包括 tools、response_format、reasoning_effort）任何一项不同都不命中；没有 scope 或没有 policy 时不读不写；`structured()` 命中还原成同一个 schema 的值；`accept` 拒绝时不写入，记为 `skip:<reason>`；lite-model 抛 `Truncated` / `EmptyReply` 时不写、不计数；过期不命中；在默认 collation 不是 C 的库上，scope 前缀失效不会误删 `ab`，也不会漏删 `a/x.y`；每次调用只计一行，outcome 正确；并发 upsert 不丢计数；写失败或计数失败时仍返回回复，`store_errors` 加一，不抛错；读失败抛 `StoreError` 且不发请求；schema 为 `public` 或非法标识符时拒绝启动；canonical JSON 的 test vector 与 lite-eval 一致；`question` 不同不影响命中，传入的原文存进 `entries.question` |
| S1.5 | 发布 lite-cache v0.1.0 release wheel（README、版本号、CI 产出 wheel） | 宿主能从 wheel 安装；lite-rag 的 `cache` extra 能解析它 |
| S2 | lite-rag 接 L1（在 lite-rag 仓库里做，按它的 stage 流程）：建索引的 `contextual` / `graph` role 与 `ask()`；`ask()` 的可选提问日志（§9） | 关闭时行为与之前等价（equivalence 测试，index 身份不变）；开启时：同一份文档第二次入库，`contextual` / `graph` 不发请求，产出的 chunk 上下文和图与第一次逐字相同；同一问题第二次 ask 不调用 LLM，回答标注缓存；换 embedding 模型后 `reindex --into` 新 kb，`contextual` / `graph` 不发请求；两个 namespace 互不命中；按 namespace invalidate 只清掉那个 namespace；`contextual` 并发大于 1 时同样命中（`call_context` 传进 `Bounded.map` 的线程）；提问日志关闭时不写文件 |
| S3（S3a 机制 + S3b 评测） | S3a：Embedder Protocol、`Policy.semantic`、新 migration（partition / embedder / 问题向量列）、分区内余弦检索、guard、`hit_semantic` 计数、按 embedder 的阈值配置（空表时 `semantic=True` 报 `ConfigError`）。S3b：pairs 评测（PAWS 英文、PAWS-X 中文、领域生成集，§8），扫阈值、Wilson gate，过 gate 的阈值写进 `thresholds.json` | 两份 test 集各自过 gate（§8），数字记入 `kb/eval-results.md`；lookup 算出的向量直接用于写入，不再 embed 第二次；`semantic=False` 或缺 question / partition 时与 S1 行为相同；没有该 embedder 的阈值时 `ConfigError`；不同 partition、不同 scope、不同 embedder 的条目互不命中；guard 拦下数字不同 / 否定 / 实体不同的近似问题（每条规则各有触发和不触发的测试）。**结果（2026-11-20）**：S3a 全部达成；S3b 评测跑完，没有可用阈值（两份 PAWS 在 0.99 以内无阈值；1.000 能过 gate 但命中率 0–3%），L2 保持关闭，不发 0.2.0（§0）；实体不同的近似问题 guard 挡不住（§12） |
| S3c | L2 verifier（§13）：`Verifier` Protocol 和基于 lite-model 的 `ModelVerifier`；命中路径加一步判定；pairs 评测加 verifier 结论，按 (embedder, verifier) 校准地板阈值 | 每份 test 集各自过 §8 的 gate，且命中率由 owner 看过后才写阈值；verifier 只在过了阈值和 guard 的候选上调用；verifier 自己的调用不进缓存、不触发 L2；verifier 失败是读失败（不发请求）；没有该组合的阈值时 `ConfigError` |
| S4 | lite-rag `ask()` 接 L2（在 lite-rag 仓库里做）：`Hit` 带 `content_hash`，partition 计算，`Policy(semantic=True)` 的配置开关（默认关）；提问日志加 partition | 文档内容改了，partition 跟着变，旧答案不再被 L2 命中；同样的 chunk 换了顺序不命中；L2 命中的回答里每个 `[n]` 指向的 chunk 与当初生成时相同（引用测试）；同义问题第二次 ask 不调用 LLM，统计记为 `hit_semantic`；关闭时与 S2 行为相同 |
| 按需 | replay 评测（有真实日志时，§8）；HTTP 服务（§7）、SQLite / Redis 后端（§0）、single-flight、verifier（cross-encoder 或 `cache.verify` role）、llm-steward（要 lite-model 0.3.0 的 `AsyncHook`）、lite-mem、Banyan（先迁到 lite-model）接入 | — |

## 12. 待确认

- ~~**L1 是否作为 llm-gateway 的中间件**~~：2026-11-19 结案，不做。lite 模块的调用都经过 lite-model，hook 就是
  必经点；gateway 只承载订阅通道的一部分流量。
- **L2 的 guard 规则集**：2026-11-20 实测：数字和否定词（含连写的 "cannot"）保留；dev 上试过的"纯增量"规则（一句的词全在另一句里且多出 ≥2 个）能挡掉大部分加条件的负例，但生成集 test 仍 5/334，没采用。剩下的是单词替换（反义词、实体），guard 规则挡不住；若做 L2，下一步是 verifier（§0 2026-11-20）。
- **guard 的已知局限**（2026-11-20 记下，不改）：数字只认阿拉伯数字串，按排序后的集合比较。
  - "3" 对 "三"：一边有数字一边没有，guard 拦下，方向安全（少一次命中）。
  - "three" 对 "four"、"三" 对 "四"：两边都没有数字串，guard 不管，全靠阈值。
  - 欧式小数 "1,5" 会被当成 "15"（千分位逗号同样去掉）。
  - 只比集合，不比顺序："3 到 5" 和 "5 到 3" 视为相同。
  - 阈值必须 < 1：数据库里向量是 float4，相同问题的相似度可能略低于 1；校准最多选 0.99。
- **guard 的否定词计数**（2026-11-20，S3c dev 上发现）：`分别` 含 `别`，"分别是哪些" 和 "分别不是哪些" 两边都"有"否定，
  guard 放行；比较否定词个数（1 对 2）就能拦下。没在 S3c 采用（不改变结果），待下次改 guard 时在 dev 上量。
- ~~**公开问题对数据集的许可**~~：2026-11-19 结案：PAWS / PAWS-X 可用，LCQMC、QQP 许可不明不用（§0）。
- ~~**lite-rag 默认的 embedder**~~：2026-11-19 确认：`ollama` 的 `bge-m3:567m`，1024 维，没有 query / document 前缀
  （lite-rag `model_profiles.py`）。S3 校准的第一个 embedder 就是它。

## 13. L2 verifier（S3c，2026-11-20 设计）

**为什么**：S3b 证明只靠余弦 + guard 分不开"改一个词就换了答案"的问题对（quickly/slowly、北/南半球、主任/副主任，
余弦 0.98–0.99）。verifier 在命中之前让一个本地 LLM 读两个问题，判断它们是否同一个答案；余弦阈值退成召回的地板，
真正的拒绝交给 verifier。

**目标**：在 §8 的同一个 gate 下（每份 test 集负例 ≥ 300、难负例过半、假命中率 ≤ 0.01、Wilson 95% 上界 ≤ 0.02）
得到可用的 L2：命中率明显高于 L1（具体多少由 owner 看数字定，不预设门槛）。
**不做**：top-k 候选（只判最近的一条）；verifier 结果缓存；把 verifier 用在 L1 上。
**反目标**：为了过 gate 放宽 gate；verifier 失败时静默放行；verifier 的调用自己又被缓存或走 L2（递归）。

### 13.1 接口

```python
class Verifier(Protocol):
    model_id: str                      # 决定判定的一切：backend:model、prompt 版本
    def same_answer(self, question: str, candidate: str) -> bool: ...
```

- `CacheHook(store, clock, *, embedder=None, verifier=None, thresholds=None)`。给了 verifier 时，阈值表的 key 是
  `"<embedder model_id>+<verifier model_id>"`（没有 verifier 时仍是 embedder 的 `model_id`，即 S3 的地板阈值不能
  直接拿来用）；构造时查不到这个组合的阈值 → `ConfigError`（同 §3、S3a）。
- 命中路径（§2 第 4 步）：L1 未命中 → embed → `nearest`（同 scope、partition、embedder、role、kind）→ 相似度 ≥
  地板阈值 → guard 通过 → **`verifier.same_answer(question, entry.question)` 为真** → 计 `hit_semantic`，返回。
  verifier 判"不同"时是 miss，调用照常发出、照常计 `stored` / `skip:*`（§3.4 每次调用只计一行）；被挡下的次数
  记在内存计数 `hook.verifier_rejects`（同 `store_errors`）和一条 debug 日志里，不进 stats 表。verifier 自己那次
  调用的 token 不计入 lite-cache 的 stats（那次调用没有 policy），要看由宿主看 lite-model 的 usage。
- `ModelVerifier(model, role, *, prompt_version="v1")`：lite-cache 自带的实现，用宿主的 lite-model `Model` 和
  宿主声明的 role（宿主在 RoleSpec 里加一个不可路由的 role，比如 `cache_verifier`，配 qwen3:8b、
  `reasoning_effort: none`、temperature 0）。调 `model.structured(role, messages, SameAnswer)`，`SameAnswer` 只有
  `same: bool`。`model_id` = `"<type>:<model>#<8 hex>"`：`type` 和 `model` 取自 `model.target(role)`（`type` 是
  backend 的类型如 `ollama`，不是宿主给 backend 起的名字，所以评测和宿主算出同一个 key）；8 hex 是 prompt 文本加上
  该 role 决定输出的参数（temperature、`reasoning_effort`、输出上限）的 canonical JSON 的 sha256 前 8 位——改模型、
  prompt 或这些参数都会换 key，旧阈值不再适用。
  - 递归防护：调用包在 `call_context(cache=None, question=None, partition=None)` 里——外层调用的 policy 会被
    覆盖掉，verifier 的这次调用不缓存、不走 L2。lite-model 0.3.0 起 hook 里经同一个 `Bounded` 调模型不会死锁。
- 故障（§3.5 的读失败）：verifier 抛错、超时、回复不合 schema → `VerifierError(StoreError)`，调用失败、不发请求，
  和 embedder 失败一样；由调用方决定要不要关掉 L2 重试。不静默当成 miss，也不当成命中。

### 13.2 prompt（v1）

system：说明任务是判断"对问题 B 的正确回答，是否一定也是问题 A 的正确回答，反过来也是"；数字、否定、对象、
范围、时间任一不同即为不同；只看问题本身，不猜答案。user：两个问题原文。输出 `{"same": true|false}`。
英文写，适用中英文问题；prompt 文本放在 `lite_cache/verifier_prompt_v1.txt`，它的 sha256 进 `model_id`。

### 13.3 校准（同 §8，加 verifier）

- eval target：新的 `VerifiedPairsTarget`（S3b 的 `PairsTarget` 不动，它的缓存照样能用）。它用和宿主同一段代码
  ——`lite_model.build` 出一个本地 Ollama 模型，`ModelVerifier` 包住它——每对问题输出余弦，**余弦 ≥ 0.80**（地板
  网格的下限）时再输出 `same_answer`；低于 0.80 的对不调（任何地板阈值下都不会成为候选）。guard 照 S3b 在校准时
  套用。verifier 的 `model_id` 在 `config()` 里，换 verifier 就重跑。
- 抽样（缩短本地运行时间）：沿用 S3b 的 dev / test 切分，在每份集合的每个切分里各随机抽 1000 对（PAWS 英文、
  PAWS-X 中文），种子固定、记进 `kb/eval-results.md`；生成集全量（897 对，S3b 按种子问题切好的 dev / test 不变）。
  每份 test 集的负例仍 ≥ 300（PAWS 约 55% 是负例）。
- 选阈值：dev 上，地板 `t ∈ [0.80, 0.99]` 中最低的那个，使"余弦 ≥ t 且 guard 通过且 verifier 判相同"的负例比例
  ≤ 0.005；多份集合取最大的 `t`。test 上按 §8 判 gate，同时报每份集合的命中率、verifier 挡下的负例数、verifier
  误挡的正例数。写阈值仍要 `--write-threshold`（owner 看过命中率后决定）。
- 先量 qwen3:8b；三份都过 gate 时再在同样的抽样上量 qwen3:1.7b，过了就由 owner 选哪个上线（1.7b 更快）。
- 8b 过不了 gate 时（owner 2026-11-20 定）：代码和测试照样合并，不写阈值，L2 照旧不能开，结果记进 KB；
  lite-cache 的新版本（新公开 API，0.3.0）等有宿主要用时再发。
- 估计：只有余弦 ≥ 0.80 的对要判，8b 每次约 0.3–1 s，合计 1–2 小时；Ollama 用 `brew services run`，跑完停。
- qwen3 的 thinking：lite-model 的 `reasoning_effort: none` 经 Ollama 的 OpenAI 兼容接口关掉 thinking，已有实证
  （lite-rag S17 的 qwen3:1.7b；S3b 生成数据时 qwen3:8b 的 190 多次结构化调用全部成功），不另做 spike。

### 13.4 测试

- 单元 / contract（假 verifier、真 PG）：verifier 只在过了阈值和 guard 的候选上调用（低于阈值、guard 拒绝时不调）；
  判"不同"是 miss 并计 `miss_verifier`；判"相同"计 `hit_semantic`；verifier 抛错 → `VerifierError`，不发请求；
  阈值按组合查，缺组合 → 构造时 `ConfigError`；`ModelVerifier` 的调用不缓存、不走 L2（在一个真 `LiteModel` 上，
  外层 `Policy(semantic=True)`，断言 verifier 的那次调用 hook 看到的 policy 为空）；`model_id` 随 prompt 变。
- `-m ollama`（opt-in）：`ModelVerifier` 对几对典型问题（同义改写、改数字、反义词、换实体）的判定。
- 评测脚本的纯逻辑（选地板阈值要同时满足三个条件）在 `lite_cache.pairs`，单元测试。

### 13.5 结果（2026-11-20，qwen3:8b）

FAIL，L2 保持关闭（`thresholds.json` 仍为空），按 §13.3 的约定代码照样合并、不发版。
- 生成集：自身 dev 地板 0.975，test 假命中 2/334（Wilson 上界 0.0216，差一点），命中率 22%（无 verifier 时 9%）。
  verifier 放过的负例多数是生成集的标签噪声（不改答案的"范围"、近义的"实体"），少数是真错。
- PAWS / PAWS-X：在 [0.80, 0.99] 里没有 dev 地板；0.99 时 dev 假命中仍 7.7%（英）/ 4.2%（中）。PAWS 是换词造出的
  长陈述句，不是问题，qwen3:8b 当成改写放过。qwen3:1.7b 没有跑（8b 没过）。
- 数字和例子：`kb/eval-results.md`。若要再试，可选方向（都要 owner 定）：更强的判定模型；只用问题对、且人工标注过的
  评测集代替 PAWS（改 §8 的 gate 数据，不放宽阈值）。

