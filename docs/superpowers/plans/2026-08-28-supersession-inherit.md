# 框架换版 → 解读继承（一期）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 条款被新版取代时，让旧条款上已经写好的解读能一键流到新条款名下——人看得见「谁能继承谁」，点一下继承，签字归零重新确认，全程留痕。

**Architecture:** 纯代码复制，**一期零模型调用**。官方 supersession 边已在内容库（`control_supersession` 表，333 条，OSCAL 来源），查询层补一个批量方法，Web 层加一页对照 + 一个继承动作。解读走 `UserInterpretationStore` 原有读写，留痕复用 `identity.log()`。模型只在二期（用户框架 v1→v2 无官方边的对齐）登场，本计划不碰。

**Tech Stack:** Python 3.12、FastAPI、SQLite、pytest。**无新增依赖。**

**Spec:** 本文件即规格（功能小，不另出 spec）。来源讨论：会话 2026-08-28「新功能盘点」。

## Global Constraints

- **一期不调模型。** 不新增出网点；`tests/test_no_network_in_tests.py` 的 `HTTPX_ALLOWED` 白名单一个字都不改。
- **签字永不继承。** `confirmed_by` / `confirmed_at` / `signed_digest` 不复制；继承产物的 `state` 一律 `draft`。构建闸门（只收 confirmed 且 digest 未变）因此天然不受影响。
- **继承是复制不是搬移。** 旧条款的解读原样保留——无损、可重复继承；「旧解读怎么处置」留给用户以后自己决定。
- **访谈原文不跟随。** `interview`（RawAnswer 等）是作者对着**旧条款**说的话，语义绑死旧条款，带着会变成新条款页上的无关问答。字段的 `value` 是抽出来的字面文本，可以带。
- **新条款已有解读时拒绝继承。** 一期不做覆盖与合并，动作直接 409——覆盖误伤的代价远大于多点一次删除。
- **内置框架开放继承，但要写明理由。** `app.py` 里「内置框架的解读不从浏览器起草」的约束，防的是**从浏览器花钱**（draft_start 的 `_charge`）。继承零模型调用、不绕签字闸门，与该约束不冲突；禁止它反而把官方映射的价值锁死在 CLI。
- 注释与用户可见文案一律中文。CSS 注释会发给浏览器，里面不许出现 `**`。
- **权限：读对照页 `content:read`，继承动作 `interpretation:draft`**——与「起草同门槛」对齐：都是往用户库里产生一条待确认解读的动作。

## 命名约定（先钉死）

- 动作叫**继承（inherit）**，路由 `POST /c/{control_id}/inherit`，form 字段 `target`（新条款 control_id）。
- 溯源字段叫 `inherited_from`（新条款记旧条款 id），挂在 `InterpretationProvenance` 上。
- 审计事件名 `interpretation.inherit`，`detail` 固定格式 `"{old} -> {new}"`。
- 对照页叫**换版对照**，路由 `GET /f/{framework_id}/supersession`。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/framework_reader/query/api.py` | 改：加 `supersessions_in(framework_id)`，批量返回框架内全部 supersession 边及两侧解读状态 |
| `src/framework_reader/interpret/model.py` | 改：`InterpretationProvenance` 加 `inherited_from: str \| None = None` |
| `src/framework_reader/userframework/inherit.py` | **新**：`inherit(old_id, new_id, store, api) -> Interpretation`，纯函数式复制逻辑，校验与复制分离 |
| `src/framework_reader/web/app.py` | 改：对照页路由、继承路由、条款页两处显示 |
| `src/framework_reader/web/views.py` | 改：对照页模板、条款页的取代提示与继承来源标记 |
| `tests/query/test_supersessions_in.py` | **新** |
| `tests/userframework/test_inherit.py` | **新** |
| `tests/web/test_supersession_page.py` | **新** |
| `tests/web/test_inherit_route.py` | **新** |

---

### Task 1: 查询层——`supersessions_in(framework_id)`

**Files:** `src/framework_reader/query/api.py`、`tests/query/test_supersessions_in.py`

- [x] **Step 1: 写失败测试**

用 `pack.db.create_schema` + `insert_frameworks` 建一个含 `control_supersession` 边的库（参照 `tests/ingest/test_supersession.py` 的造数方式）。断言：返回的每条边带 `old_id / new_id / relation / old_label / new_label / old_state / new_state`；只含指定框架内的边；无边的框架返回空列表。

- [x] **Step 2: 跑，确认它红**

```bash
.venv/bin/python -m pytest tests/query/test_supersessions_in.py -q
```

- [x] **Step 3: 实现**

一条 JOIN：`control_supersession` 旧端 `JOIN all_control`（限 `framework_id`）取旧条款名，再分别 JOIN 旧、新条款的解读状态。解读状态从用户库读，不在内容库——`api` 构造时已有 `_user_db` 的访问模式（见 `interpretation_state()`），复用它。

- [x] **Step 4: 跑，确认绿**

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/query/api.py tests/query/test_supersessions_in.py
git commit -m "feat(query): 按框架列出 supersession 边，两侧带解读状态"
```

---

### Task 2: 复制逻辑——`inherit()`

**Files:** `src/framework_reader/userframework/inherit.py`、`src/framework_reader/interpret/model.py`、`tests/userframework/test_inherit.py`

- [x] **Step 1: 写失败测试**

四组用例：
1. 正常继承——七字段 `value`/`basis` 逐项相同，`state == draft`，`provenance.inherited_from == old_id`
2. 签字段清空——`confirmed_by / confirmed_at / signed_digest` 全 `None`
3. 访谈记录清空——`interview == InterviewRecord()`（默认空）
4. 拒绝分支——新条款已有解读、旧条款无解读、边上不存在这对（旧, 新）组合，各自抛带中文信息的异常

- [x] **Step 2: 跑，确认它红**

- [x] **Step 3: 实现**

`InterpretationProvenance` 加 `inherited_from: str | None = None`。`inherit()` 校验（边存在用 `api.superseded_by(old_id)` 的返回核对 `new_id` 在列；新旧解读有无用 `store.exists`）→ 深拷贝字段 → 清签字与访谈 → `store.save()`。落库前的校验和落库本身分两个函数，路由层先校验再落。

- [x] **Step 4: 跑，确认绿**

- [x] **Step 5: 提交**

```bash
git add src/framework_reader/userframework/inherit.py src/framework_reader/interpret/model.py tests/userframework/test_inherit.py
git commit -m "feat(inherit): 解读继承的复制与校验——签字与访谈原文不跟随"
```

---

### Task 3: 对照页——`GET /f/{framework_id}/supersession`

**Files:** `src/framework_reader/web/app.py`、`src/framework_reader/web/views.py`、`tests/web/test_supersession_page.py`

- [x] **Step 1: 写失败测试**

覆盖：有边的框架出表格（旧行含编号/标题/解读状态，新列同）；每行动作列按状态渲染——「继承」表单（旧有解读、新无解读）、「新条款已有解读」纯文本、「旧条款无解读」纯文本；无边框架显示「这个框架没有取代关系」的空态；框架不存在 404；未登录跳登录。

- [x] **Step 2: 跑，确认它红**

- [x] **Step 3: 实现路由**

`@app.get("/f/{framework_id}/supersession")` + `@needs(perm.CONTENT_READ)`，调 `api().supersessions_in(framework_id)`，页面标题「换版对照」，面包屑回框架页。页顶放一句说明：「继承把旧条款的解读复制到新条款名下；签字不带过去，新条款要重新确认。」

- [x] **Step 4: 实现视图**

每行一个继承表单：`action="/c/{old_id}/inherit"`，隐藏字段 `target={new_id}`。禁止把无动作行的按钮渲出来——后端校验是底线，前端不渲染是体面。

- [x] **Step 5: 跑，确认绿**

- [x] **Step 6: 提交**

```bash
git add src/framework_reader/web/app.py src/framework_reader/web/views.py tests/web/test_supersession_page.py
git commit -m "feat(web): 换版对照页——谁能继承谁，一眼看完"
```

---

### Task 4: 继承路由 + 条款页显示

**Files:** `src/framework_reader/web/app.py`、`src/framework_reader/web/views.py`、`tests/web/test_inherit_route.py`

- [x] **Step 1: 写失败测试**

覆盖：POST 成功 303 到新条款页，新条款 `load()` 出继承产物；各拒绝分支 409/400 与中文提示页；未登录 401；无 `interpretation:draft` 权限 403；审计日志里能查到 `interpretation.inherit` 一条、detail 为 `"{old} -> {new}"`；继承后旧条款解读原封不动（复制不搬移）。

- [x] **Step 2: 跑，确认它红**

- [x] **Step 3: 实现路由**

`@app.post("/c/{control_id}/inherit")` + `@needs(perm.INTERPRETATION_DRAFT)`。成功后 `identity.log("interpretation.inherit", actor=_who(request), detail=f"{old} -> {new}")`。

- [x] **Step 4: 实现条款页显示**

两处（都在 `/c/{control_id}`）：
1. `api.superseded_by(control_id)` 非空时，右栏显示「本条款已被取代」块，列出每条 `new_id + label + relation`，附去对照页的链接
2. 解读 `provenance.inherited_from` 非空时，正文前显示与 AI 初稿同款的醒目标记：「继承自 `{inherited_from}`，尚未按新条款重新确认」，样式复用 `state=draft` 的那条（不新增 CSS 类就不碰 CSS 注释规则）

- [x] **Step 5: 跑，确认绿**

- [x] **Step 6: 提交**

```bash
git add src/framework_reader/web/app.py src/framework_reader/web/views.py tests/web/test_inherit_route.py
git commit -m "feat(web): 继承动作落库留痕，条款页标出取代与继承来源"
```

---

### Task 5: 收尾

**Files:** `README.md`

- [x] **Step 1: 全量回归**

```bash
make test
```

- [x] **Step 2: README 命令清单提一句**

「开发」小节加一行：换版对照在网页 `/f/{框架}/supersession`，继承只对写了解读的条款开放、签字不带。

- [x] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs: README 提一句换版对照的入口与边界"
```

---

### Task 6（二期备忘，本期不做）: 用户导入框架的 v1→v2

用户框架之间没有官方边，靠编号/标题匹配 + 模型辅助对齐（复用 `table_ai` 的 `GuardedClient` 模式，模型只回 `{old, new, confidence}`，永不动正文），人在对照页逐条点头才生成继承边。**触发条件：内置框架一期真用顺了，且出现真实的用户框架换版场景。** 现在写下来只为不忘。

---

## 自审记录

**范围核对**：查询 1 个方法、纯函数 1 个、路由 2 个、显示 2 处、测试 4 个新文件——没有别的地方要动。
`interpret/migrate.py` 是存储搬家，与换版无关，不碰；`compare.py` 是手写/产出对比，不碰。

**明确不做**：覆盖已有解读；批量一键全继承（逐条点是安全带，跑通后如果嫌慢再加）；删除/改写旧条款解读；对继承产物做任何模型调用。

**风险**：`supersessions_in` 的 JOIN 若把「新条款属于另一框架」的跨框架边带进来会张冠李戴——Task 1 的测试必须含一条跨框架边样例，断言被排除。
