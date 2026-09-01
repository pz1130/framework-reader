# W2：访谈管线与解读生产 设计文档

> ## ⚠ 2026-08-20 晚：本文档的核心决定 D1 已被推翻
>
> 访谈管线已建成、测试齐全、严格抽取在真实模型上验证成立（忠实度 1.00）。
> 但首次实跑显示**作者的时间不足以支撑 106 条的产出节奏**，据此改走 **B 路线**：
> 七个字段全部由 AI 撰写，`basis` 一律 `inferred`。
>
> - 实跑记录：`docs/superpowers/notes/w2-first-interview-run.md`
> - 主 spec 对应修订：§5（产品宪法）、§7.3（盲测假设）、§7.4（止损线）
>
> **本文档以下内容保留为历史记录**，描述的是 D1 成立时的设计。代码也全部保留、
> 未删除、仍在测试覆盖内——随时可对个别关键控制退回访谈路线。阅读时请注意：
> §1 D1、§2.3、§8 的判据均已不再适用。

**日期**：2026-08-20
**上游**：`docs/superpowers/specs/2026-08-19-framework-reader-design.md`（下称主 spec）
**上一期**：`docs/superpowers/plans/2026-08-19-w1-content-graph-data-layer.md`（已完成并合并 main）

---

## 0. 这份文档要解决什么

主 spec §7.2 把 W2 定为「AI 初稿管线 + 审核 TUI + 3 条黄金样例」。落到设计时暴露出一处
自相矛盾，本文档的全部内容都是围绕它展开的：

> 主 spec §3.4 说 `common_myth` / `auditor_asks` / `regional_note` 是「通用大模型答得最差的
> 地方，也是收费理由」。
> 而 W2 要建的是一条**用通用大模型生成初稿**的管线。

如果这三个字段也由 AI 起草、人工只做「快速过一遍」，那么 W3 盲测（主 spec §7.3）比的将是
**「带提示词的大模型」对「裸大模型」**，差距会小到 70% 那条通过线过不去。届时无法区分
「方法论不成立」与「管线设计错了」，而主 spec §7.4 的止损线要求这两者必须能分开。

**因此 W2 的产物不是「审核 TUI」，是「访谈 TUI」。** 主循环从「看 AI 稿 → 改」变为
「回答追问 → AI 整理 → 你签字」。

---

## 1. 核心决定

| # | 决定 | 理由 |
|---|---|---|
| D1 | 三个差异化字段**不由 AI 起草**，改由 AI 提问、作者回答 | 模型的语料不进这三个字段，人的经验进 |
| D2 | 其余四字段（`intent`/`plain_zh`/`practice`/`evidence`）仍走 AI 起草 + 人审 | 这四项模型确实做得好，且不是收费理由 |
| D3 | 每条控制**固定 3 个追问**，前 2 问固定模板，第 3 问自适应 | 见 §2.2 |
| D4 | 整理答案时**严格抽取**：只许删、切、重排，不许引入作者没说过的信息 | 见 §2.3 |
| D5 | 模型调用走独立脚本 + Anthropic/OpenAI 兼容双适配器，**多厂商预设** | 见 §3 |
| D6 | 终端用行式循环 + `$EDITOR` 确认，不做全屏 TUI | 见 §5 |

### 1.1 为什么固定 3 问而不是 5 问

W3 要在一周内完成 CSF 106 条 L-Full。全职一周 40 小时即**平均 22 分钟/条**，且必须包含
AI 生成、作者回答、整理与复核。5 问答不完。

### 1.2 为什么前 2 问固定、第 3 问自适应

- 全固定（3 问 = 3 字段）：问题问 106 遍，作者会从第 30 条起按模板作答，字段会越写越像。
  「像」正是盲测里的死因。
- 全自适应：模型在第 2 问时还没读到足够的作者输入，追问会退化成「能展开讲讲吗」。
- 折中：前两问固定保证产出结构稳定；第 3 问在作者已经说了两轮之后发出，模型手上有料，
  追问才可能扎到点上。

`regional_note` 放在自适应的第 3 问还有一个实际考虑：106 条里相当一部分**没有**真实的
地域差异，固定发问会逼作者编。自适应意味着模型在没料时改问别的，该字段诚实留空。
**留空是信号，编出来的是污染。**

### 1.3 黄金样例的新角色

D1 之后，3 条黄金样例不再是 AI 起草的 few-shot，而是：

1. 作者自己的校准尺——「答到什么程度算答完」；
2. **管线的验收标准**——访谈产出离它们有多远。

因此**必须零 AI 参与、手写，并且先于管线代码完成**。若由 AI 起草再人改，等于拿管线的
输出当管线的验收标准。

选定的 3 条（按「差异化字段有没有料」挑，不按重要性挑）：

| 控制 | 选它的理由 |
|---|---|
| `NIST-CSF-2.0:GV.SC-07` | `regional_note` 最有料：DORA/NIS2 之后欧洲审计员对第三方的松紧与美国差异明显 |
| `NIST-CSF-2.0:PR.AA-05` | `common_myth` 最有料：中文团队普遍理解成「有个权限矩阵表就行」 |
| `NIST-CSF-2.0:GV.RM-02` | 故意挑难的：务虚条款，用来暴露方法论是否在这类控制上失效 |

第三条是刻意的。黄金样例若全挑熟悉的控制，尺子就是歪的。

---

## 2. 管线形状

```
                 ┌─ 起草器(AI) ──→ intent / plain_zh / practice / evidence  [basis: inferred]
控制 (CSF 106) ──┤
                 └─ 提问器(AI) ──→ Q1 固定 · Q2 固定 · Q3 自适应
                                        ↓
                                   作者回答（终端）
                                        ↓
                                interview.raw   ← 每答完一问立刻落盘
                                        ↓
                              抽取器(AI · 严格) ──→ common_myth / auditor_asks / regional_note
                                        ↓                                    [basis: practitioner]
                                 $EDITOR 确认签字
                                        ↓
                              state: confirmed → 进构建
```

四个组件各自是纯函数（输入 → 输出），模型客户端由调用方注入。

| 组件 | 输入 | 输出 | 允许生成新信息 |
|---|---|---|---|
| 起草器 | CSF outcome 原文 + 该控制的 L1 邻居 | 4 个非差异化字段 | 允许，标 `basis: inferred` |
| 提问器 | outcome + 起草稿 +（Q3：作者前两个答案） | 3 个问题 | 允许（问题不是内容） |
| 抽取器 | 作者 raw 答案 | 3 个差异化字段 | **禁止** |
| 术语校验 | 全部字段文本 | 违规词表 | 不调模型，复用 W1 `Glossary` |

### 2.1 起草与访谈分离（吞吐关键）

`fr draft --all` 离线批量跑完全部控制的四个初稿字段，可并发。之后作者坐下访谈时：

- Q1 / Q2 是固定模板，**不调模型**；
- 交互期只剩 2 次模型调用：Q3 自适应一次、抽取一次。

22 分钟/条的预算里不应有任何一分钟花在等待起草上。

### 2.2 三个追问

- **Q1（固定）**：这条在中文团队里最常见的误解是什么？ → `common_myth`
- **Q2（固定）**：审计员会追问哪几句？ → `auditor_asks`
- **Q3（自适应）**：模型读完 Q1/Q2 的原始回答后生成。默认追地域差异（→ `regional_note`），
  若作者前两答中某处明显更值得挖，则改追该处，产出回填对应字段。

### 2.3 严格抽取（D4）

抽取器只能删、切、重排作者的原话，**不得引入作者没说过的信息**。

被否决的替代方案与理由：

- **允许润色措辞** —— 这是本设计中唯一会骗过作者的选项。中文润色是大模型最强的能力，
  它会把「审计员一般第二句就问你上次复核是谁签的字」润成「审计人员通常关注权限复核的
  执行情况及记录留存」。前者是作者，后者是任何模型都写得出的话。D1 的全部价值在于那三个
  字段听起来不像 AI，润色恰好把它抹掉。
- **允许补空**（作者没答到的点由模型从 CSF outcome 补，标 `inferred`）—— 主 spec §3.4 原话：
  「若这三个字段填不满，说明该条控制尚未吃透」。用模型补上去等于把质量卡尺的刻度磨平。

落地方式：

1. `interview.raw` 逐字留存，永不删除，进 Git；
2. `$EDITOR` 中字段值与对应原话并列（原话以注释贴在字段上方），作者签字才生效；
3. lint：字段值与对应 raw 段落的字符二元组重合度低于阈值即标红。**阈值不预设常数**，
   在 3 条黄金样例上标定：取「人工判定为忠实抽取」的最低重合度再下调一档作为阈值。
   **这是近似检查，拦得住整段编造，拦不住换近义词**——真正的防线是第 2 条。
   **不做成构建断言**，误报率会毁掉手感。

### 2.4 `basis` 增加 `practitioner`

主 spec §3.4 要求每字段附 `basis`（依据原文哪一句，或 `inferred`）。D1 之下，三个差异化
字段的依据既非原文亦非推断，而是作者的从业经验，故取值集扩为：

`quote:<原文定位>` | `inferred` | `practitioner`

盲测未通过时，这是回溯「哪些字段是人给的、哪些是模型给的」的唯一依据。

---

## 3. 厂商抽象

### 3.1 一个协议，两个适配器

```
LLMClient (Protocol)        complete(system, messages, *, model, max_tokens) -> str
   ├── AnthropicAdapter     原生 SDK；唯一支持显式 prompt caching
   └── OpenAICompatAdapter  base_url 可换，覆盖下列全部厂商
```

国内厂商普遍提供 OpenAI 兼容端点，因此覆盖成本极低。

### 3.2 预设

预设写在 `content/llm_providers.yaml`。**端点会漂**，故配 key 后跑 `fr llm check` 逐个验活，
验不通的标灰保留、不删除。

| 预设 id | 形态 | 备注 |
|---|---|---|
| `anthropic` | 原生 | 唯一支持显式 prompt caching |
| `deepseek` | OpenAI 兼容 | 自动上下文缓存 |
| `qwen`（阿里百炼） | OpenAI 兼容 | 隐式缓存 |
| `glm`（智谱） | OpenAI 兼容 | |
| `kimi`（月之暗面） | OpenAI 兼容 | 长上下文 |
| `doubao`（火山方舟） | OpenAI 兼容 | |
| `hunyuan`（腾讯） | OpenAI 兼容 | |
| `minimax` | OpenAI 兼容 | |
| `baichuan` | OpenAI 兼容 | |
| `siliconflow` | OpenAI 兼容 | 聚合口，一个 key 试多个开源模型 |
| `openai` | OpenAI 原生 | |

### 3.3 四个调用点各自指定厂商

不共用全局默认——它们的需求不同：起草器要中文表达力与安全领域知识；抽取器只要指令遵循
（越便宜越好，因为它不许创作）；提问器介于两者之间。

```yaml
drafter:    {provider: anthropic, model: claude-opus-5}
questioner: {provider: deepseek,  model: deepseek-chat}
extractor:  {provider: deepseek,  model: deepseek-chat}
```

`extractor` 单独可配还有一层考虑：它收到的是作者原话——本产品最核心的资产。发给哪家是
商业判断而非延迟与价格判断，配置需支持为它单独指定厂商，乃至将来换本地模型。

### 3.4 三处随之而来的约束

1. **prompt caching 降级为 best-effort。** 仅 Anthropic 显式，DeepSeek/Qwen 为自动或隐式，
   其余不保证。适配器接口不得含「必须命中缓存」的假设，成本估算亦不得建立其上。
2. **`model_id` 记全：`provider/model/prompt_version`。** 换厂商等于换了一批语料的生产条件；
   盲测回溯时这是区分「方法论不行」与「这家模型不行」的唯一依据。
3. **新增红线断言：Tier C/D 原文不得出圈。** W2 只跑 CSF（公共领域），但管线一旦建成，
   W4–W5 做 ISO 时极易把购买来的 ISO 原文发给某家 API。**在客户端出口处断言：payload 中
   出现 `original_text` 内容或 Tier C/D 框架原文即抛异常**，与主 spec §10.A 两条红线同级。
   现在加成本近乎为零，W4 再想起来就晚了。

### 3.5 跨厂商对比

抽象层建成后额外做一个对比模式：**3 条黄金样例 × N 家厂商**，输出 diff 表，供作者在 W3
前定下厂商组合。范围严格限定这 3 条，不碰 106 条。

---

## 4. 存储与状态机

### 4.1 布局

```
content/
  interpretations/NIST-CSF-2.0/GV.SC-07.yaml     ← 生产
  golden/NIST-CSF-2.0/GV.SC-07.yaml              ← 手写基线，同 schema，管线只读不写
  llm_providers.yaml
  glossary.zh.yaml                                ← W1 已有
```

沿用主 spec §9：内容以 YAML 存于 Git，SQLite 为构建产物。Pydantic 模型仍是 schema 唯一
真相源，YAML 是它的序列化。

### 4.2 单条文件

```yaml
control_id: NIST-CSF-2.0:GV.SC-07
locale: zh-CN
state: confirmed                    # draft → interviewed → confirmed
fields:
  intent:        {value: "…", basis: inferred}
  plain_zh:      {value: "…", basis: inferred}
  practice:      {value: {1: "…", 2: "…", 3: "…"}, basis: inferred}
  evidence:      {value: "…", basis: inferred}
  common_myth:   {value: "…", basis: practitioner}
  auditor_asks:  {value: ["…", "…"], basis: practitioner}
  regional_note: {value: null, basis: practitioner}
interview:
  questions: [{n: 1, kind: fixed, text: "…"}, {n: 3, kind: adaptive, text: "…"}]
  raw:       [{n: 1, text: "逐字原话，永不删除"}]
provenance:
  drafter:   {provider: anthropic, model: claude-opus-5, prompt_version: "2026.08-d1"}
  extractor: {provider: deepseek,  model: deepseek-chat, prompt_version: "2026.08-x1"}
  confirmed_by: jc
  confirmed_at: 2026-08-25T14:03:00+08:00
```

### 4.3 状态机

| state | 含义 |
|---|---|
| `draft` | 起草器跑完，四个非差异化字段有初稿，三个差异化字段为空 |
| `interviewed` | raw 已存且抽取器跑完，未签字 |
| `confirmed` | 作者在 `$EDITOR` 中签字 |

迁移只允许 `draft → interviewed → confirmed` 单向推进。已 `confirmed` 的文件再被编辑，
必须重新签字——签字时对内容（`control_id` + `locale` + 七个字段 + `interview`）算 sha256
存入 `provenance.signed_digest`，构建期重算比对，对不上即失败。

**不用文件 mtime。** git 恢复文件时打的是当前时间，`clone` / CI checkout / 切分支之后
mtime 一律是「刚刚」——拿它当内容有没有变的证据，会在每一次 clone 之后把全部已签字条目
误判为「签字后被改过」。摘要与文件时间戳无关，且能精确抓住签字后的内容改动。
`provenance.interview_seconds` 不进摘要，因此事后补记耗时不会让签字失效。

**只有 `confirmed` 进构建**，且构建断言 `confirmed_by` 非空。AI 不能签字——这是主 spec §5
「禁止直接落库」的物理实现。

---

## 5. 终端形态

`prompt_toolkit` 行式循环 + `$EDITOR` 确认。**不用 Textual。**

```
┌ GV.SC-07 · 3/106 ─────────────────────────────────────────────
│ The risks posed by a supplier, their products and services, and
│ other third parties are understood, recorded, prioritized,
│ assessed, responded to, and monitored over the course of the
│ relationship
│
│ 初稿 intent  供应链风险不是签合同时评一次就完，是关系存续期…
│              ^D 展开全部 4 条初稿   ^S 存盘退出   ^K 跳过本条
└───────────────────────────────────────────────────────────────
 [1/3] 中文团队对这条最常见的误解是什么？
 ▸
```

三问答完 → 抽取器跑 → YAML 直接甩进 `$EDITOR`，差异化字段上方以注释贴着原话：

```yaml
  # 你说：一般他们不看合同，直接问你上次复核供方是什么时候、谁签的字
  common_myth:
    value: "…"
```

选择理由：`$EDITOR` 中作者拥有全部编辑能力，**且所改即所发**，中间没有一层 TUI 状态在翻译。
全屏分栏 TUI 至少多花两天，而 B 阶段 Web UI 一上线它即作废（主 spec §8① 已要求 CLI 不得
直接写 SQL、业务逻辑留在 QueryAPI 一侧）。

命令面：

```
fr draft --all                       # 批量起草 CSF 2.0 全部活跃 subcategory，离线可并发
fr interview <control_id>            # 访谈单条
fr interview --next                  # 自动取下一条 draft
fr interview --resume                # 从中断处继续
fr golden diff                       # 访谈产出 vs 手写黄金样例
fr llm check                         # 逐个 ping 预设厂商，验活
```

---

## 6. 错误处理

主线只有一条：**作者说过的话，任何情况下都不能丢。**

| 情况 | 处理 |
|---|---|
| 答完一问 | 立刻 append 进 YAML 落盘，不等三问答完 |
| 模型调用失败 | 退避重试；仍失败则保留 raw，`fr interview --resume` 续 |
| 抽取器输出不合 schema | **不写盘**；原始响应存 `build/extract_failures/` 并报错。**不做自动修复**——自动修复等于让模型二次创作 |
| 严格抽取 lint 触发 | 不阻塞；`$EDITOR` 中以注释标红，由作者判断 |
| Tier C/D 原文出圈 | 抛异常。不重试、不降级、不降为 warn |
| Ctrl-C | 已答部分保存，状态回退到可续位置 |

---

## 7. 测试

沿用 W1 最有效的纪律：**公有 CI 不接触 `vendor/`、不接触 API key、零网络。**

- 全部模型调用走注入的 fake client；
- 新增 CI 断言：测试代码不得读 key 环境变量、不得发真实请求；
- 单测覆盖：YAML round-trip、状态机迁移合法性、构建断言（`state == confirmed` 且
  `confirmed_by` 非空）、抽取 lint、出口红线断言、适配器请求形状；
- 厂商适配器只测「请求形状是否正确」，**不测活连通**——活连通归 `fr llm check`，由人手工跑。

---

## 8. 验收与止损

### 8.0 黄金样例 diff 测的是什么（一处修正）

本文档初稿把「同 3 条黄金控制跑访谈、与手写版 diff」写成**管线的验收标准**。这是过度声称，
在此修正。

作者刚亲手写完那 3 条。坐下来回答同样 3 条控制的追问时，他会把刚写过的内容复述一遍——
**diff 好看不能说明管线好，只能说明作者记性好。**

管线有两处可能失败，黄金 diff 只覆盖其中一处：

| 可能失败的地方 | 黄金 diff 能否测出 |
|---|---|
| **问得出来吗**——3 个追问能否把作者脑子里的东西挖出来 | **不能。** 作者已被自己刚写的内容锚定 |
| **抽得住吗**——抽取器会不会把作者的话润成通用表述 | **能。** 有已知的正确答案可比 |

因此黄金 diff 是**单向有效**的证据：

- **diff 变坏**（作者明知答案，管线仍把它做丢）→ 强坏消息，直接触发 §8.3 止损；
- **diff 好看** → 只证明抽取器没乱改，**不证明追问设计成立**。

它仍然值得跑，但目的收窄为两条：① 用真实模型把整条链走通（registry / guard / 抽取 /
编辑器 / 签字），别到第 40 条才发现某处炸；② 给 lint 阈值标定提供**已知意图**的数据——
标定需要「人工判定为忠实抽取」的样本，而只有在知道正确答案的条目上，这个判定才可靠。

「问得出来吗」由 §8.1 第 4 条的**陌生控制**来测。那一条原本只被当作秒表，实际上它是
整个 W2 唯一一次未被污染的测试。

### 8.1 W2 完成标准

1. 3 条黄金样例手写完毕，零 AI，进 `content/golden/`；
2. 同 3 条跑完整访谈流程，`fr golden diff` 出对比（按 §8.0 单向解读）；
3. 跨厂商对比表出来，作者据此定下 W3 的厂商组合，写回 `content/llm_providers.yaml` 的 `roles`；
4. `content/lint.yaml` 的阈值用第 2 条的数据标定，不再是占位值；
5. **2–3 条作者从未写过的陌生控制跑完整访谈**，冷启动。这是判定管线的地方，见 §8.2；
6. 全部测试绿，无网络、无 API key。

陌生控制建议一硬一软，覆盖两端：`NIST-CSF-2.0:DE.CM-01`（技术域，证据形态明确）与
`NIST-CSF-2.0:GV.OV-01`（治理复核，务虚）。

### 8.2 第 5 条是 W2 真正的产出

代码只是拿到它的手段。每条陌生控制读两件事：

**① 有没有一句是通用大模型说不出来的。**

盯三个差异化字段。这正是 W3 盲测评委要回答的问题——提前两周自己问一遍。
若三个字段读起来都像「任何模型加个提示词都能写出的话」，管线就没有产出收费理由。

**② 耗时。**

`fr interview` 跑完自动打印，并记入 `provenance.interview_seconds`。

- ≈ 22 分钟/条 → W3 按 106 条走；
- ≈ 40 分钟/条 → 106 条需约 70 小时，W3 一周做不完，**在 W2 结束这天就做范围决定**
  （主 spec §7.4：砍范围，不延期），而不是 W3 周四才发现。

### 8.3 止损前置（写死）

**判据在陌生控制上，不在黄金 diff 上**（理由见 §8.0）。命中任一即触发：

- 陌生控制的三个差异化字段中，有两个塌成通用表述——即读不出任何「只有做过审计的人才说得出」
  的内容；
- 或黄金 diff 变坏：作者明知答案，管线产出仍明显比手写版空泛。

命中即：**那不是提示词的问题，是 D1 方法论本身的问题。W2 停下来重新设计访谈，不带着坏
管线冲进 W3。**

现在写死，比届时在「已经投了一周」的心态下临时判断要诚实。这条与主 spec §7.4 同性质：
给六个月后那个舍不得停手的自己留一封信。

---

## 9. W2 不包含

- CSF 106 条的实际生产（W3）
- ISO 27002 与其他框架的解读（W4–W6）
- 映射的 L4 初稿与人工确认（W4–W5）
- `QueryAPI.search()` 重做（W1 遗留：只匹配 `label`，中文关键词当前只能命中 ISO 93 条；挪至 W4）
- Web UI、用户可写层、文档解析（B/C 阶段）

---

## 10. 对主 spec 的修订项

本文档确认后需回改主 spec：

1. **§7.2**：「审核 TUI」改为「访谈 TUI」，并说明 D1（三个差异化字段不由 AI 起草）；
2. **§3.4**：`basis` 取值集增加 `practitioner`；
3. **§5**（AI 边界）：离线生产一栏的「禁止」增加一条——**三个差异化字段禁止 AI 起草**；
4. **§10.A**：红线增加第三条——**Tier C/D 原文不得进入任何模型调用的 payload**；
5. **§7.4**：止损线表格增加一行——**黄金样例 diff 显示访谈产出空泛 → W2 停下重设计访谈**。

---

## 11. W1 遗留、W2 不处理但需记账

1. **OLIR #186 文件名含 `_draft`。** 它贡献 1182 条可导出边中的 743 条并标记 `L1_OFFICIAL`。
   `content/allowed_sources.yaml` 记为「developer=NIST, Owner, Final」，但下载路径位于
   submissions 目录且文件名含 `draft`。**W6 打包发布前必须回 OLIR 目录页重新核实。**
2. **R7 的 17% 由 AI 自评。** 结论采信（失败模式为结构性：IR-4 / SA-9 / XX-1 枢纽焊成完全图），
   但 **W3 盲测不得由 AI 判定**（主 spec §7.3）。
