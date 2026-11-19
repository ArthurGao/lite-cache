# lite-cache 设计文档

**状态：设计完成，S0 未开始（2026-11-15）；2026-11-19 按 lite-model 改写接入方式（§0）。** 这是唯一记录项目状态的地方。本文是唯一的设计来源；
与代码冲突时，在同一次改动里更新本文，并在 commit 里说明原因。

## 0. 决策记录

决策的理由只写在这里，其它章节引用（§0）。

| 日期 | 决策 | 原因 |
| --- | --- | --- |
| 2026-11-15 | 形态：library；HTTP 服务和代理按需做（§7） | 与 lite-rag / lite-mem 一致；现有接入方全是 Python |
| 2026-11-15 | 第一个接入方：lite-rag `ask()` | 重复问题最可能出现在问答 |
| 2026-11-15 | 语义匹配只在应用层做，透明代理只做 L1 | 代理分不清用户问题和注入的 RAG 上下文 |
| 2026-11-15 | provider prompt caching 不在范围内 | 它属于 client / gateway 配置；KB 实测它只在多轮长上下文里生效（`KB/global/llm-evals/evals/2026-10-05-tenderted-prompt-caching.md`） |
| 2026-11-15 | 不挂在 llm-steward 上 | steward 自己的 `kb.*` / `memory.*` role 会调 LLM，但它不替其它应用转发 LLM 调用（没有 completions 路由），也不记 token |
| 2026-11-15 | 不重复造轮子（"运行时不 import 兄弟项目"一句已被 2026-11-19 的"依赖 lite-model"取代）：canonical JSON 照搬 lite-eval `canonical.py`；migration 运行器逐字 vendor lite-rag `db.py`（e42fc93）；schema 校验照搬 lite-mem（标识符约束加保留名拒绝，§5）；Embedder 由调用方注入，lite-cache 不做 embedding 缓存 | 运行时不 import 兄弟项目：lite-rag 在 S4 要依赖 lite-cache，反过来就成了循环依赖；为了 30 行 canonical JSON 把 lite-eval（带 Viewer 的 wheel）拉进运行时依赖不划算。lite-eval 只作为 `eval` extra 和 dev 依赖用于评测（S2 起），与 lite-rag、lite-mem 的用法相同。vendor 的文件头注明来源和 commit，并用 test vector 和来源比对 |
| 2026-11-15 | 先做 L1 和 eval 基线（S1、S2）；L2 等回放数据证明值得再做（S3） | 重复率低的流量里，L1 可能就够了 |
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

## 1. 概述

LLM 调用的响应缓存：同一个问题再次出现时，直接返回上次的答案，不再消耗 token。足够相似的问题
从 S3 起也能命中。

- **L1 精确缓存**：同一请求只调用一次模型。请求相同指 scope、role、model、messages、params 都相同。
- **L2 语义缓存（S3）**：调用方明确给出问题和分区，在分区内按问题向量的相似度命中。阈值由
  lite-eval 在标注好的问题对上校准，用 gate 守住误命中率。
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
     4. [S3] L1 未命中且 context 带了 question 和 partition → 在分区内做 ANN → 阈值 → guard → hit
   lite-model 发请求；截断、空回复、不合 schema、调用失败都抛异常：不经过 after，不写、不计数
   CacheHook.after(call, reply)
     5. policy.accept(reply)：None → 写入并计 stored（同一个事务）；否则计 skip:<reason>
```

## 3. 接口

```python
Policy(ttl_s=None, accept=None, refresh=False)   # accept(reply) -> str | None；None = 全部写入
CacheHook(store, clock=…)                         # lite_model.hooks.Hook，name "lite-cache"
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
- **宿主要暴露注册入口**：lite-rag 的 `build_model` 返回类型是 `Model`（不含 `.hooks`），lite-mem、
  llm-steward 包在自己的 adapter 里；接入时各自开一个注册 hook 的口子（§9）。

### 3.1 Key

key 是 canonical JSON 的 sha256，规则与 lite-eval `canonical.py` 相同：所有字符串（包括 dict 的 key）
做 NFC，`sort_keys`，紧凑分隔符，`ensure_ascii=False`，`allow_nan=False`（遇到 NaN 抛错），UTF-8 编码。
NFC 只合并 Unicode 意义上等价的写法，不改变语义。内容为：

`{"v": 2, scope, role, backend, model, messages, params}`

- **scope 必填**，格式 `project[/tenant[/user]]`，每段为 `[A-Za-z0-9_.-]+`。不同 scope 永远不共享
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
| `hit` | 从缓存返回 | saved_* += 条目原始 usage | 只有计数这一次写 |
| `stored` | 调了模型并写入了缓存（含 refresh） | spent_* += 本次 usage | 写条目和计数在同一个事务 |
| `skip:<reason>` | 调了模型，`accept` 拒绝 | spent_* += 本次 usage | 只有计数这一次写 |

- 调用失败（lite-model 抛异常）时不计数：hook 看不到失败的调用；它的用量在异常的 `usage` 上（lite-model §9）。
- `hit_rate = hit / (hit + stored + skip:*)`；`stats()` 按 role 汇总，同时给出各 skip 原因的次数。
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

`Store` Protocol：`get(key, now)`、`put(entry, now)`、`invalidate(...)`、`purge_expired(now, limit)`、
`count(...)`、`stats(...)`。

PostgreSQL 实现：
- schema 默认 `lite_cache`；migrations 是编号的 SQL 文件；执行时 `search_path = <schema>, public`。
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
  `question`（§3）。L2 需要的 `partition` 和问题向量在 S3 加。

SQLite 后端以后按需再加，复用同一套 contract 测试。

## 5. 配置

环境变量前缀 `LITECACHE_`。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `LITECACHE_DATABASE_URL` | — | 必填 |
| `LITECACHE_SCHEMA` | `lite_cache` | 两条校验都要有：标识符正则 `^[a-z_][a-z0-9_]{0,62}$`；不能是 `public`、`information_schema` 或 `pg_*`。后一条照搬 lite-mem `own_schema`，前一条在 lite-mem 里是另一个 pydantic 约束，也一起照搬。字段名写成 `schema_name`，alias 为 `schema`（同 lite-rag），以免遮蔽 pydantic 的同名属性 |
| `LITECACHE_TEST_DATABASE_URL` | — | 只给测试用，测试只使用 schema `lite_cache_test` |

policy 不放进环境变量，它们是代码里的决定（§3）。

## 6. CLI

`litecache migrate | stats [--scope] [--since] | invalidate (--scope | --role | --key) | purge | config`

`config` 打印最终组装出的配置，database URL 中的密码会脱敏。

## 7. HTTP 服务和代理

2026-11-19 起不做自己的代理（§0）：非 Python 的接入方用 LiteLLM Proxy（lite-model 的 `litellm` 后端同一个
Proxy），不带本缓存。**Proxy 的 cache 保持关闭**（§0；lite-model §7.2）。

HTTP 服务（查询和写入的 HTTP 版）只在出现非 Python、又必须共享本缓存的接入方时再设计；现在没有。

## 8. 评测（S2，lite-eval）

按 lite-eval rule 9，target adapter 写在本仓库的 `eval/lite-eval/`。其它项目的模型评测运行时不传 policy
（或 `refresh=True`），以免 trial 之间共享答案（§0）。

- **replay**：一份真实查询日志按时间回放。指标是 L1 的命中率、L1+L2 的命中率、省下的 token。
  先用它判断 L2 值不值得做。
- **pairs**（S3 前）：数据集是 `(q1, q2, same_answer: bool)` 三元组，含难负例：增和删、数字不同、
  否定、实体不同。
  - 指标：
    - `false_hit_rate` = 被判为命中的负例对数 / 负例对总数（主指标）；
    - `hit_rate` = 被判为命中的正例对数 / 正例对总数。
  - 数据切分：阈值在 dev 集上扫描选定，gate 只在 held-out test 集上判定，两个集合不重叠。
  - gate：test 集上负例至少 300 对，难负例占一半以上；`false_hit_rate ≤ 0.01`，且 95% Wilson 上界
    `≤ 0.02`。上界和样本数都记进 `kb/eval-results.md`。

## 9. 与其它项目的关系

| 项目 | 接入方式 | 阶段 |
| --- | --- | --- |
| lite-rag | `ask()` 在生成时用 L1（answer role），`call_context(scope=…, cache=…)` 包住生成；L2 的 partition 由 lite-rag 根据检索到的 `doc_id@content_hash` 计算；以 `cache` extra 提供，默认关。前置条件：`build_model` 暴露 `LiteModel`（或一个注册 hook 的入口）；`Hit` 带上文档的 `content_hash`（`chunk_id` 在文档改动后不变，现在还没有）。截断已由 lite-model 报错，不再需要 `finish_reason`。S4 开始前，按 `ask()` 的实际流程写出 adapter 契约：scope 和 question 从哪来，partition 怎样由所有参与生成的文档算出 | S4 |
| lite-eval | replay 和 pairs 两个评测 | S2、S3 |
| llm-steward | `kb.risk` / `kb.worthiness` 用 L1。steward 是 async 的：需要 lite-model 的 `AsyncHook`（lite-model §10，计划 0.3.0），同步 hook 里的 psycopg 查询会阻塞 event loop | 以后 |
| lite-mem | extract / resolve 只用 L1，让重试不重复花钱；不用 L2；评测时不传 policy | 以后 |

## 10. 项目结构

```
src/lite_cache/
  types.py     Policy / RoleStats
  canonical.py 从 lite-eval vendor 过来
  keys.py      cache_key(call)
  hook.py      CacheHook（lite-model Hook；S3 起 async 版本）
  config.py    Settings（LITECACHE_）
  db.py        PG 连接和 migrations
  store.py     Store Protocol 和 PgStore
  migrations/  NNNN_*.sql
  cli.py
tests/unit/  tests/contract/
kb/          项目 KB（发现、评测数字、教训）
```

## 11. 阶段与验收

每个阶段收尾都按 AGENTS.md Workflow 第 4 步执行：`/simplify`，然后 Codex 只读 review，然后修复，
最后重跑全部检查。

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| S0 | 骨架、CI（PG 服务）、migration 框架；依赖 lite-model（git tag 的 wheel） | ruff、mypy、pytest 全绿 |
| S1 | L1：key、`CacheHook`、写入规则、TTL、invalidate、计数与 stats、PgStore、CLI | 用 lite-model 的 `FakeModel` 包在 `LiteModel` 外（或 `build` 加假 transport）：同一请求第二次不发请求，回复 `cached=True`、来源和 usage 是第一次的；scope、role、backend、model、messages、params（包括 tools、response_format、reasoning_effort）任何一项不同都不命中；没有 scope 或没有 policy 时不读不写；`structured()` 命中还原成同一个 schema 的值；`accept` 拒绝时不写入，记为 `skip:<reason>`；lite-model 抛 `Truncated` / `EmptyReply` 时不写、不计数；过期不命中；在默认 collation 不是 C 的库上，scope 前缀失效不会误删 `ab`，也不会漏删 `a/x.y`；每次调用只计一行，outcome 正确；并发 upsert 不丢计数；写失败或计数失败时仍返回回复，`store_errors` 加一，不抛错；读失败抛 `StoreError` 且不发请求；schema 为 `public` 或非法标识符时拒绝启动；canonical JSON 的 test vector 与 lite-eval 一致；`question` 不同不影响命中，传入的原文存进 `entries.question` |
| S2 | lite-eval replay：日志格式、target、基线（无缓存 / L1） | `lite-eval run` 出基线，数字记入 `kb/eval-results.md` |
| S3 | L2：最小 Embedder Protocol（`model_id`、`dim`、`embed_query`，形状同 lite-rag，由调用方注入；lite-rag 传入它的 `CachingEmbedder`）、分区 ANN、guard、pairs 评测 | pairs gate 通过（§8）；lookup 算出的向量直接用于写入，不再 embed 第二次 |
| S4 | lite-rag `ask()` 接入（extra，默认关）；前置：lite-model 把 `Bounded` 的槽位移进执行器（计划 0.3.0），否则命中也要排队 | 关闭时行为与之前等价（equivalence 测试）；开启时第二次 ask 不调用 LLM，回答标注缓存 |
| 按需 | HTTP 服务（§7）、SQLite / Redis 后端（§0）、single-flight、verifier（cross-encoder 或 `cache.verify` role）、llm-steward（要 `AsyncHook`）和 lite-mem 接入 | — |

## 12. 待确认

- ~~**L1 是否作为 llm-gateway 的中间件**~~：2026-11-19 结案，不做。lite 模块的调用都经过 lite-model，hook 就是
  必经点；gateway 只承载订阅通道的一部分流量。
- **L2 的 guard 规则集**：数字、否定词、实体等规则，S3 用 pairs 里的难负例实测后再定。
