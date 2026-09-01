-- 用户可写层。A 阶段【不建库、不写代码】，仅定义结构。spec §6.1、§8③
--
-- 与只读内容层物理分离：升级内容包 = 替换只读文件，本层一个字节都不动。
-- 本层全部通过 control_id 引用内容层；control_id 的稳定性契约见 spec §8②。

CREATE TABLE IF NOT EXISTS user_annotation (
    control_id   TEXT NOT NULL,
    locale       TEXT NOT NULL,
    body         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (control_id, locale)
);

-- 自评：本组织每条控制的现状。主 spec §6.1、§7.3.3
--
-- 两种模式共用一张表：
--   成熟度模式（框架有 practice 三档，如 CSF）用 level：0=没做，1/2/3=档位。
--     没有 0 的话，大量「压根没做」的条款会被迫虚报成 1 档。
--   适用性声明模式（框架无解读，如 ISO Annex A）用 applicable + status。
-- note 两种模式共用：现状说明、证据在哪、谁负责。
CREATE TABLE IF NOT EXISTS assessment (
    control_id   TEXT NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'default',
    applicable   INTEGER NOT NULL DEFAULT 1,
    reason       TEXT NOT NULL DEFAULT '',   -- 判为不适用的理由，SoA 必填
    level        INTEGER,                    -- 成熟度模式；SoA 模式为 NULL
    status       TEXT NOT NULL DEFAULT '',   -- SoA 模式：未开始/进行中/已实施
    note         TEXT NOT NULL DEFAULT '',
    assessed_at  TEXT NOT NULL,
    PRIMARY KEY (control_id, scope)
);

-- 自评历史：每次「记下」都追加一行，主表永远只留最新一次。
-- 复评对比读它——没有这份流水，「上季度 1 档、这季度 2 档」无处可查。
CREATE TABLE IF NOT EXISTS assessment_history (
    control_id   TEXT NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'default',
    applicable   INTEGER NOT NULL,
    level        INTEGER,
    status       TEXT NOT NULL DEFAULT '',
    assessed_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assessment_history
    ON assessment_history(control_id, scope, assessed_at);

-- 整改台账。差距报告回答「下一步做什么」，这里记「谁、什么时候、做了没有」。
-- state 三档手工扳，不跟自评联动：改完没复评之前，「做了」只是当事人的一面之词，
-- 复评的档位才是证据。主表 upsert——一条条款同时只有一项整改。
CREATE TABLE IF NOT EXISTS remediation (
    control_id   TEXT NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'default',
    owner        TEXT NOT NULL DEFAULT '',
    due          TEXT NOT NULL DEFAULT '',   -- ISO 日期；空 = 没定期限
    state        TEXT NOT NULL DEFAULT 'todo',  -- todo | doing | done
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (control_id, scope)
);

-- 用户导入的框架。主 spec §7.3.5
--
-- 放在用户层而不是内容包里：内容包是你发布的、可随时重建的只读文件，
-- 用户导入的东西不能被一次 `make build` 抹掉，也不该被你分发出去。
-- 查询层把两层合起来看（QueryAPI 的 ATTACH + 联合视图），存储层保持分离。
CREATE TABLE IF NOT EXISTS user_framework (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    version      TEXT NOT NULL DEFAULT '',
    imported_at  TEXT NOT NULL,
    source_file  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS user_control (
    id           TEXT PRIMARY KEY,
    framework_id TEXT NOT NULL REFERENCES user_framework(id),
    label        TEXT NOT NULL,
    parent_id    TEXT,
    body         TEXT NOT NULL DEFAULT '',   -- 用户自己的条款正文，可空
    sort_key     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_user_control_framework ON user_control(framework_id);

-- 条款正文覆盖层。内置框架的正文不在内容库里（original_text 永远为空），
-- 用户给内置条款贴的正文落在这一张表上，读取时盖在骨架上；删行即恢复默认。
-- 与 all_interpretation 的逐字段覆盖同一个哲学：改的是用户库里的这一份，
-- 发布物一个字节不动。贴进来的原文只进用户自己的库，不出服务器
-- ——「骨架内置、原文外挂」的边界在网页上找到了口子，但方向没变。
-- **不能往 user_control 里插内置 id**：all_control 是 UNION ALL，
-- 同一个 id 会回两行，get_control 拿哪行看运气。
CREATE TABLE IF NOT EXISTS control_body_override (
    control_id   TEXT PRIMARY KEY,
    body         TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- 用户上传的自有文档（制度、程序、评估报告）。设计 §8 S5
--
-- 存的是**解析出来的文本**，不是原始文件。原始文件留在服务器上只是多一份
-- 拷贝、多一个泄漏面，而起草要用的从来只是文字。
CREATE TABLE IF NOT EXISTS user_document (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    uploaded_at  TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    chars        INTEGER NOT NULL DEFAULT 0,
    uploaded_by  TEXT NOT NULL DEFAULT ''
);

-- 切好的段。检索的单位是段，不是整篇——整篇喂给模型等于把信号稀释掉。
CREATE TABLE IF NOT EXISTS user_document_chunk (
    document_id  TEXT NOT NULL REFERENCES user_document(id),
    ordinal      INTEGER NOT NULL,
    heading      TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL,
    PRIMARY KEY (document_id, ordinal)
);

-- 受版权标准原文。**永远为空。**主 spec §3.2②、网页服务化设计 §7(A)
--
-- 本地版的设想是「用户在自己机器上注入自己买的那份」。改成联网服务之后，
-- 那份原文就落在我们的服务器上——同一张表，性质全变了。§7 选的是 (A)：
-- 不建这条路。当时零成本（一行都没实现），所以这里没有砍掉任何功能。
--
-- 表留着不删：`llm/guard.py` 从它读「不许出网的文本」，是红线三的执行体；
-- 而且已经建过这张表的库还在外面跑着。留表、封写。
CREATE TABLE IF NOT EXISTS original_text (
    control_id   TEXT NOT NULL,
    locale       TEXT NOT NULL,
    body         TEXT NOT NULL,
    source_doc   TEXT REFERENCES user_document(id),
    PRIMARY KEY (control_id, locale)
);

-- 门开在库上，不开在某个 store 的方法里：绕过 Python 直接写 SQL 也进不来。
CREATE TRIGGER IF NOT EXISTS original_text_is_never_written
BEFORE INSERT ON original_text
BEGIN
    SELECT RAISE(ABORT,
        'original_text 永远为空：受版权标准原文不进服务器。见设计 §7(A)');
END;

-- 通用确认机制：A 阶段 actor_type='author'，B 阶段 actor_type='user'。spec §8④
CREATE TABLE IF NOT EXISTS confirmation (
    target_kind  TEXT NOT NULL,   -- 'mapping' | 'interpretation'
    target_id    TEXT NOT NULL,   -- mapping: "from_id|to_id"；interpretation: control_id
    actor        TEXT NOT NULL,
    actor_type   TEXT NOT NULL,   -- 'author' | 'user'
    confirmed_at TEXT NOT NULL,
    model_version TEXT,
    PRIMARY KEY (target_kind, target_id, actor)
);

-- 历史问卷/审计回答。C 阶段的回答记忆库依赖它
CREATE TABLE IF NOT EXISTS answer_history (
    id           TEXT PRIMARY KEY,
    control_id   TEXT NOT NULL,
    question     TEXT NOT NULL,
    answer       TEXT NOT NULL,
    audience     TEXT NOT NULL,
    answered_at  TEXT NOT NULL,
    approved_by  TEXT
);

-- 内容包升级后指向已删除控制的悬空引用，标记而非删除。spec §6.1
CREATE TABLE IF NOT EXISTS orphaned_reference (
    control_id   TEXT NOT NULL,
    detected_at  TEXT NOT NULL,
    pack_version TEXT NOT NULL,
    PRIMARY KEY (control_id, pack_version)
);

-- 用户导入框架的解读。主 spec §7.3.5
--
-- 不进 content/interpretations/：那里是**我们要发布的内容**，进 git、要评审、
-- 会被 `make build` 烘进内容包。用户自己公司的制度解读不是我们的内容，
-- 它是用户数据——和自评做邻居，跟 user.sqlite 一起备份，`make clean` 删不掉，
-- 也永远不会被我们分发出去。
--
-- 字段行的形状与内容包的 interpretation 表一致，查询层才能把两边并成一个视图。
-- state 与出处放在 meta 表：一条解读只有一个成色、一个起草它的模型。
CREATE TABLE IF NOT EXISTS user_interpretation (
    control_id   TEXT NOT NULL,
    locale       TEXT NOT NULL DEFAULT 'zh-CN',
    field        TEXT NOT NULL,
    value_json   TEXT NOT NULL,
    basis        TEXT NOT NULL,
    PRIMARY KEY (control_id, locale, field)
);

CREATE TABLE IF NOT EXISTS user_interpretation_meta (
    control_id   TEXT NOT NULL,
    locale       TEXT NOT NULL DEFAULT 'zh-CN',
    state        TEXT NOT NULL DEFAULT 'draft',
    provenance   TEXT NOT NULL DEFAULT '',   -- JSON：谁用哪个模型、哪版提示词起草的
    interview    TEXT NOT NULL DEFAULT '',   -- JSON：作者原话，B 路线下通常为空
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (control_id, locale)
);

-- 文档导入的预览态。见 2026-08-25 AI 导入设计 §3
--
-- 为什么落盘而不是放进程内：web/jobs.py 里的任务状态可以丢，因为起草的
-- 结果已经在用户库里，丢的只是「跑到第几条」。这里的结果**还没进库**——
-- 重启一次，用户已经花掉的那几十次调用就没了，且他不知道为什么，
-- 只能再花一次。
--
-- source_text 是原文快照，必须一起存：条款正文靠行号从它截，
-- 它变了正文就跟着变了。
CREATE TABLE IF NOT EXISTS import_draft (
    id           TEXT PRIMARY KEY,
    framework_id TEXT NOT NULL,
    name         TEXT NOT NULL,
    source_text  TEXT NOT NULL,
    spans        TEXT NOT NULL,      -- JSON
    dropped      TEXT NOT NULL,      -- JSON 数组，被取消勾选的下标
    problems     TEXT NOT NULL,      -- JSON
    created_at   TEXT NOT NULL,
    created_by   TEXT NOT NULL DEFAULT ''
);

-- 条款详情页上和 AI 的对话。见 2026-08-25 会话
--
-- **对话跟着条款走，不跟着人走**：这个产品是一个安全团队协作一套材料，
-- 签字的人要能看到「这句话当初是怎么来的」——那比任何审计记录都管用。
--
-- `proposal` 是模型提议的字段修改（JSON）。**它只是提议**：写库要人点头，
-- 点头之后 `applied_at` 才有值。模型说的话永远不会自己进库。
CREATE TABLE IF NOT EXISTS control_chat (
    id          TEXT PRIMARY KEY,
    control_id  TEXT NOT NULL,
    at          TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL,              -- user | ai
    text        TEXT NOT NULL,
    proposal    TEXT NOT NULL DEFAULT '',   -- JSON: [{field, value}]
    applied_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_control_chat ON control_chat(control_id, at);

-- 网页搜索命中。首页「经常搜索」读这里。
-- 记的是点得进去的条款，不是那句查询——同一条被不同问法命中，仍然算经常。
CREATE TABLE IF NOT EXISTS search_hit (
    control_id TEXT NOT NULL,
    at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_hit_control ON search_hit(control_id);
CREATE INDEX IF NOT EXISTS idx_search_hit_at ON search_hit(at);
