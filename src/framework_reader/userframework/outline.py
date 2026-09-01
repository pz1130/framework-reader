"""把一份连续正文切成条款。见 2026-08-25 AI 导入设计

**模型只划边界，正文由这里从原文按行号截。** 让模型直接吐正文，它会静默
把「离职当日停用」润色成「应在员工离职时及时停用其账号」——两句意思差不多，
但后面起草的解读、自评的证据、差距报告全部基于这段正文，而它不再是这家
公司的话，且没人分得清哪些字是原文、哪些是当时顺手改的。设计 §1.1

模型的输出一律当作不可信输入：解析、校验、合并全在代码里。
"""
import re
from dataclasses import dataclass

# 行号宽度。四位够到 9999 行，一份 200 页的制度约 6000 行。
_WIDTH = 4

# 模型吐了这些键里的任何一个，就说明它在写正文而不是划边界。
_BODY_KEYS = {"body", "text", "content", "正文"}

# 「塞得下」的判据是一个固定的保守阈值，**不是模型的真实上下文**——目录接口
# 只回模型 id，不回上下文长度（模型目录设计 §1），我们无从得知。
# 估错的后果是多分一次块，不是失败，所以宁可估小。不引 tokenizer：
# 为一个「宁可估小」的判断引一个依赖不值。
ONE_SHOT_MAX_CHARS = 40000


@dataclass(frozen=True)
class Span:
    """一条条款的边界。**没有 body**——正文永远从原文截。

    模型面的 JSON 用 `from` / `to`；这里用 `start` / `end`，因为 `from`
    是 Python 关键字。两套名字的转换只发生在 `parse_outline()` 一处。
    """

    ref: str
    label: str
    parent: str | None
    start: int          # 1-based，含
    end: int            # 1-based，含
    # 这个编号/标题是原文里就有的，还是补出来的。**「谁写的要能看出来」**
    # 是这个产品的地基，和条款页那套「AI 初稿」一个规矩。
    ref_from: str = "original"      # original | derived
    label_from: str = "original"


@dataclass(frozen=True)
class Problem:
    """一条能直接渲到预览页的中文说明。"""

    kind: str           # out_of_range | overlap | bad_parent | not_json | has_body | uncovered | catalog | snapped
    detail: str


def line_count(text: str) -> int:
    return len(text.splitlines())


def numbered(text: str) -> str:
    """给模型看的那一份：每行前面钉一个行号。"""
    return "\n".join(
        f"{index:0{_WIDTH}d}| {line}"
        for index, line in enumerate(text.splitlines(), start=1)
    )


def slice_lines(text: str, start: int, end: int) -> str:
    """截第 start 到第 end 行（1-based，两端含）。**逐字，不做任何加工。**

    行内的缩进、全角空格一律保留：那是制度的原样，不是格式噪声。

    越界就夹紧而不抛。越界本该被 `validate()` 挡掉，真漏到这儿抛异常
    炸掉的是后台线程，而用户看到的是一个永远停在「切分中」的页面。
    """
    lines = text.splitlines()
    lo = max(1, start)
    hi = min(len(lines), end)
    if lo > hi:
        return ""
    return "\n".join(lines[lo - 1:hi])


_FENCE = re.compile(r"^\s*```(?:json)?\s*$|^\s*```(?:json)?\s*$", re.MULTILINE)
# MiniMax-M3 这类带 reasoning trace 的模型会把思考过程塞进 <think>...</think>
# 里。咱不需要看思考——思考里的中括号会干扰下面最外层数组的配对，先剥光。
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
# 思考把 token 用光时 ``</think>`` 永远不出现。probe.py 同一条。
_THINK_UNCLOSED = re.compile(r"<think>.*$", re.DOTALL)


def _spoken(raw: str) -> str:
    """思维链剥掉，只留它真正说出口的那部分。

    闭合的 ``<think>...</think>`` 先剥；还剩一个没闭合的 ``<think>``，
    说明后面根本没有正文（实测 MiniMax 切 NIST.AI.100-1 就是这样），整段
    都当草稿纸丢掉。
    """
    text = _THINK.sub("", raw or "")
    return _THINK_UNCLOSED.sub("", text)


def _raw_for_debug(raw: str) -> str:
    """把模型原始回复压成一串能塞进 problems 的样子。

    **这是唯一一次为下次调试写生率原貌。** 平时 problems 详情都是机器
    生成的文案——但「模型没有按格式回」这种失败，下次接手的人**必须能
    看到模型回了什么**，不然在乱猜。

    先剥 ``<think>`` 标签——minimax 系列模型总是先把思考塞在里面，
    显示给用户看的时候不能把思考当正文。压成单行是因为 problems 在 UI 上是
    ``<li>``，多行会带进未转义换行难脱手。错行表示在生原文里全用 U+23CE
    替换了。
    """
    s = _spoken(raw).replace("\n", "⏎").strip()
    return s[:300] + ("..." if len(s) > 300 else "")


def _balanced_from(text: str, start: int,
                   open_ch: str = "[", close_ch: str = "]") -> str | None:
    """从 ``text[start]`` 这个开括号起，配对到对应的闭括号。

    字符串里的括号不算。配不上（截断、方括号在思考里没闭合）就回 None。
    """
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _salvage_objects(raw: str) -> tuple[list[dict], bool]:
    """从没写完的回复里救出已经写完的对象。

    两种半截都见过：
    - 数组截断：``[{...}, {...}, {"ref":``
    - 根本不包数组：一条一个 ``{...}`` 换行拼在一起，最后一条被截断
      （NIST.AI.100-1 又一次：回了 Framing Risk / 1.1 / 1.2 却判 not_json）
    """
    import json

    text = _spoken(_FENCE.sub("", raw or "").strip())
    if "{" not in text:
        text = _FENCE.sub("", raw or "").strip()
        text = _spoken(text) or text
    bracket = text.find("[")
    brace = text.find("{")
    if brace == -1:
        return [], False
    in_array = bracket != -1 and (brace == -1 or bracket < brace)
    i = (bracket + 1) if in_array else brace
    objects: list[dict] = []
    saw_close = False
    incomplete = False
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n:
            break
        if in_array and text[i] == "]":
            saw_close = True
            break
        if text[i] != "{":
            break
        blob = _balanced_from(text, i, "{", "}")
        if blob is None:
            incomplete = True
            break
        try:
            obj = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            incomplete = True
            break
        if isinstance(obj, dict):
            objects.append(obj)
        i += len(blob)
    if in_array:
        truncated = bool(objects) and (incomplete or not saw_close)
    else:
        truncated = bool(objects) and incomplete
    return objects, truncated


def _first_valid_array(text: str) -> str | None:
    """跳过解析失败的 ``[``（思考里的 ``[GOVERN]``、``[Section A]``），
    收下第一个能 ``json.loads`` 成「对象数组」的。

    空数组 ``[]`` 也算——这一段确实没有条款。纯字符串数组
    （``["Section A"]``）不算，那是思考里的举例。
    """
    import json

    cursor = 0
    while True:
        start = text.find("[", cursor)
        if start == -1:
            return None
        candidate = _balanced_from(text, start)
        if candidate:
            try:
                payload = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                cursor = start + 1
                continue
            if isinstance(payload, list) and (
                not payload or any(isinstance(row, dict) for row in payload)
            ):
                return candidate
        cursor = start + 1


def _extract_json_array(raw: str) -> str | None:
    """从模型原始回复里抠出最外层的 JSON 数组。

    现实里模型不守约的情况很多：
    - 前面加 ``Here is the JSON:``、后面加问候
    - 包在 ```json 围栏里
    - **在多段间贴补充说明**（minimax 会在 ``<think>...</think>`` 里先写思考）
    - 思考文本里举例、列表包含方括号
    - 字符串里出现 ``[`` ``]`` （比如某条 title 是 ``[草稿]``）
    - **JSON 只写在思考里**，标签外再没吐（MiniMax-M3 切长文档的常态）
    - **思考没闭合**（max_tokens 用光）

    单纯的 find("[")/rfind("]") 在这些场景下都会错配——上一次
    NIST.AI.100-1 重导看到的细节（思考 <think> 里有方括号）证实了这个。
    必须用平衡配对，并且跳过解析失败的 ``[``。

    先看剥掉思考之后的正文；没有数组再回过头从思考里找——答案写在
    草稿纸上总比整段作废强。
    """
    text = _FENCE.sub("", raw or "").strip()
    spoken = _spoken(text)
    return _first_valid_array(spoken) or _first_valid_array(text)


def parse_outline(raw: str) -> tuple[list[Span], list[Problem]]:
    """解析模型回的那一段。**从不抛异常**——调用方是后台线程。

    三档处理，别混：
    - 整块作废：不是 JSON、不是数组、或者吐了正文（它没在按契约干活）
    - 丢一条：某一条缺字段（其余条款是好的，不该被连累）
    - 照收：其余
    """
    import json

    candidate = _extract_json_array(raw)
    payload = None
    truncated = False
    if candidate is not None:
        try:
            loaded = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            loaded = None
        if isinstance(loaded, list):
            payload = loaded
        elif loaded is not None:
            return [], [Problem("not_json", "The model reply is not an array; this chunk is dropped.")]
    if payload is None:
        salvaged, truncated = _salvage_objects(raw)
        if not salvaged:
            return [], [Problem(
                "not_json",
                "The model reply is not valid JSON, so this chunk is dropped."
                f" What it actually replied: {_raw_for_debug(raw)}")]
        payload = salvaged

    # 先扫一遍 body：吐了正文就整块作废，不能只丢那一条——
    # 它已经证明自己没在按契约干活，前面几条看着正常也不能信。
    for row in payload:
        if isinstance(row, dict) and _BODY_KEYS & set(row):
            return [], [Problem(
                "has_body",
                "The model wrote body text itself, but body text may only come from your source. This chunk is dropped; you can rerun it.")]

    spans: list[Span] = []
    problems: list[Problem] = []
    if truncated:
        problems.append(Problem(
            "truncated",
            f"The model reply was truncated after clause {len(payload)}; "
            "clauses completed before the cut were kept."))
    for row in payload:
        if not isinstance(row, dict):
            problems.append(Problem("not_json", "One item in the model's array is not an object and was dropped."))
            continue
        ref = str(row.get("ref") or "").strip()
        label = str(row.get("label") or "").strip()
        parent = str(row.get("parent") or "").strip() or None
        try:
            start = int(row["from"])
            end = int(row["to"])
        except (KeyError, TypeError, ValueError):
            problems.append(Problem(
                "not_json",
                f'"{ref or label or "an unnamed clause"}" gave no line numbers and was dropped.'))
            continue
        spans.append(Span(
            ref=ref, label=label, parent=parent, start=start, end=end,
            ref_from=_source(row.get("ref_from"), ref),
            label_from=_source(row.get("label_from"), label),
        ))
    return spans, problems


def _source(value, filled: str) -> str:
    """模型说这个编号/标题是抄的还是它起的。没说就按「有值即抄的」算——
    老的回答里没有这个字段，不能因此把原文里的编号标成 AI 起的。
    """
    if str(value or "").strip() == "derived":
        return "derived"
    return "original" if filled else "derived"


def validate(spans: list[Span], total_lines: int,
             lines: list[str] | tuple = ()) -> tuple[list[Span], list[Problem]]:
    """按行号排序，丢掉越界与重叠的，把指不到的上级降成顶层。

    **丢和降是两种不同的处理，别混。** 越界的条款没有可信的正文，只能丢；
    上级指错的条款正文是好的，丢掉它等于把用户的一条制度弄丢了。
    """
    problems: list[Problem] = []
    ranged: list[Span] = []
    renames: list[tuple[str, str]] = []
    # 同一起点时**长的排前面**，否则父条款会排在子条款后面，栈就建反了。
    for span in sorted(spans, key=lambda s: (s.start, -s.end)):
        if span.start < 1 or span.end > total_lines or span.start > span.end:
            problems.append(Problem(
                "out_of_range",
                f'"{_name(span)}" gave line range {span.start}-{span.end} '
                f"outside the source text ({total_lines} lines in total), dropped."))
            continue
        ranged.append(span)

    # **条款是树，不是一串。** 一条完全落在另一条区间内，那是嵌套（3.2.2 下面
    # 的 a/b/c），不是重叠。早先这里一律判重叠丢掉，实测一份国标框架 PDF
    # 被丢了 184 条子条款——一半内容没进来。
    #
    # 真正的错是**错位相交**（10–20 与 15–25）：那种边界模型划错了，没法救。
    # 区间完全相同也是错：同一段被切了两次，那是重复不是父子。
    kept: list[Span] = []
    stack: list[Span] = []          # 当前打开着的祖先链，由外到内
    inferred: dict[int, str | None] = {}
    for span in ranged:
        while stack and span.start > stack[-1].end:
            stack.pop()
        if stack:
            outer = stack[-1]
            if span.end > outer.end:
                problems.append(Problem(
                    "overlap",
                    f'"{_name(span)}" gave line range {span.start}-{span.end} '
                    f'that crosses "{_name(outer)}" ({outer.start}-{outer.end}) - '
                    "neither its child nor its successor, dropped."))
                continue
            if span.start == outer.start and span.end == outer.end:
                problems.append(Problem(
                    "overlap",
                    f"'{_name(span)}' and '{_name(outer)}' cover the same lines "
                    f"({span.start}-{span.end}), the latter dropped."))
                continue
            # 上级取最内层那个祖先。它自己没编号就挂不上去，留空。
            inferred[id(span)] = outer.ref or None
        else:
            inferred[id(span)] = None
        kept.append(span)
        stack.append(span)

    # 上级要在**留下来的**里面找：被丢掉的那条不能再当别人的上级。
    refs = {s.ref for s in kept if s.ref}
    fixed: list[Span] = []
    seen: set[str] = set()
    for span in kept:
        parent = span.parent
        if parent is None:
            # 模型没填就按包含关系推——包含本身就说明了谁是谁的上级。
            parent = inferred.get(id(span))
        elif parent not in refs or parent == span.ref:
            problems.append(Problem(
                "bad_parent",
                f"'{_name(span)}' names parent '{parent}', "
                "but no kept clause has that number; demoted to top level."))
            parent = inferred.get(id(span))
        # 空编号是「原文没编号」，不是同一个号——人会在预览页补。
        ref = span.ref
        if ref and ref in seen:
            new = _disambiguate_ref(ref, parent, seen)
            renames.append((ref, new))
            ref = new
        if ref:
            seen.add(ref)
        fixed.append(Span(
            ref=ref, label=span.label, parent=parent,
            start=span.start, end=span.end,
            ref_from="derived" if ref != span.ref else span.ref_from,
            label_from=span.label_from))
    if renames:
        sample = "、".join(f"{old}→{new}" for old, new in renames[:6])
        extra = f" and {len(renames)} more" if len(renames) > 6 else ""
        problems.append(Problem(
            "catalog",
            f"Clause numbers conflicted with existing ones; auto-renamed to {sample}{extra}."
            "Appendices restarting from 1 are a common cause."))
    fixed = _trim_to_own_text(fixed, list(lines))
    return fixed, problems


def _disambiguate_ref(ref: str, parent: str | None, seen: set[str]) -> str:
    """附录里又从 1 数：挂到上级下面（D.1），不要让人改六个输入框。"""
    if parent:
        for cand in (f"{parent}.{ref}", f"{parent}-{ref}"):
            if cand not in seen:
                return cand
    n = 2
    while True:
        cand = f"{ref}-{n}"
        if cand not in seen:
            return cand
        n += 1


# 一两行的洞折叠成一句，三行以上的点名。
#
# 条款标题行不进正文（设计 §1.2），所以**每一条条款都会留下一个单行洞**。
# 实测一份 31 行的制度报出 7 条未覆盖，其中 5 条就是标题行；一份 600 行的
# 真制度会报出几十条，而「整章漏了」这种真问题就混在里面看不见了。
#
# 折叠不等于隐瞒：数目照说，只是不再一条一行地占满屏幕。
_HOLE_WORTH_NAMING = 3


def _trim_to_own_text(spans: list[Span], lines: list[str]) -> list[Span]:
    """父条款只留**第一个子条款之前**那一段。

    不截的话，父条款的正文会把整棵子树包一遍——起草时同一段话喂两遍
    （花两遍钱），自评时同一件事数两遍，导出的 SoA 里一句话出现两次。
    实测一份国标框架 PDF 里 197 条有 160 条受影响。

    父条款只是个分组标题、下面直接跟子条款时，截完是空的。那就是空的：
    `slice_lines` 对 start > end 返回空串，预览页照实说这一条没有自己的正文。

    **末尾的收尾句会掉出来**（父条款最后一个子条款之后那几行）。
    那不静默消失——`uncovered()` 会报出行号，人自己判断要不要合并回去。
    """
    out = []
    for span in spans:
        children = [
            other for other in spans
            if other is not span
            and span.start <= other.start and other.end <= span.end
        ]
        if children:
            first = min(children, key=lambda s: s.start)
            end = first.start - 1
            # 子条款的**标题行**也不属于父条款。提示词让 `from` 跳过自己的
            # 标题，于是那一行落进了上一条——实测「Control Matrix」的正文
            # 变成了「GOVERN」，而 GOVERN 正是它子条款的标题。
            #
            # 只在那一行确实写着子条款的标题时才多剥一行：少剥是多一行噪声，
            # 多剥是把用户的正文丢了。标题正文同行的子条款不受影响。
            if (first.label and 1 <= end <= len(lines)
                    and first.label in lines[end - 1]):
                end -= 1
            span = Span(ref=span.ref, label=span.label, parent=span.parent,
                        start=span.start, end=end)
        out.append(span)
    return out


# 1–2 行的洞是下一条的标题。3–8 行没有新标题，多半是上一条被截短的续行。
_MIN_CONTINUE_GAP = 3
_MAX_CONTINUE_GAP = 8

_TOC_PAGE = re.compile(r"\s+\d{1,3}$")
_SECTION_HEADING = re.compile(
    r"^(?P<summary>Executive Summary)\s*$"
    r"|^(?P<app>Appendix|附录)\s+(?P<appref>[A-Z一二三四五六七八九十])"
    r"(?:\s*[:：]\s*(?P<applabel>.*))?$"
    r"|^(?P<part>Part\s+\d+)\s*[:：]\s*(?P<partlabel>.{0,60})$"
    r"|^(?P<num>\d+(?:\.\d+)*)[、.．]\s+(?P<numlabel>[A-Z][^.]*)$",
    re.I,
)


def parse_section_heading(line: str) -> tuple[str, str] | None:
    """附录、Part、Executive Summary、「6. AI RMF Profiles」这种分节标题。

    目录行末尾带页码（「Appendix A: … 35」），不是正文标题。
    「6. Be useful to a wide range…」这种带句点的条目也不是新章。
    """
    text = (line or "").strip()
    if not text or _is_toc_heading(text):
        return None
    match = _SECTION_HEADING.match(text)
    if not match:
        return None
    if match.group("summary"):
        return "Summary", "Executive Summary"
    if match.group("app"):
        ref = match.group("appref")
        label = (match.group("applabel") or "").strip(" :：")
        return ref, label
    if match.group("part"):
        ref = match.group("part").replace(" ", "")
        return ref, (match.group("partlabel") or "").strip()
    if match.group("num"):
        label = (match.group("numlabel") or "").strip()
        if len(text) > 60:
            return None
        return match.group("num"), label
    return None


def _is_toc_heading(text: str) -> bool:
    return bool(_TOC_PAGE.search(text)) and len(text) < 90


def _holes(spans: list[Span], total: int) -> list[tuple[int, int]]:
    holes: list[tuple[int, int]] = []
    cursor = 1
    for span in sorted(spans, key=lambda s: s.start):
        if span.start > cursor:
            holes.append((cursor, span.start - 1))
        cursor = max(cursor, span.end + 1)
    if cursor <= total:
        holes.append((cursor, total))
    return holes


def close_small_gaps(spans: list[Span], lines: list[str]) -> list[Span]:
    """上一条被截短、下一标题还没到：把中间的续行并回去。

    分块会切在句子中间（NIST.AI.100-1 的 3.3 停在第 547 行，续行有 13
    行才到 3.4）。按「下一条标题出现的前一行」收回去，不设死 8 行上限。
    1 行的洞仍不并——那是下一条的标题。
    """
    if not spans or not lines:
        return spans
    ordered = sorted(spans, key=lambda s: (s.start, -s.end))
    by_id = {id(s): s for s in ordered}
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt.start <= prev.end + 1:
            continue
        heading = _heading_line_of(lines, prev.end + 1, nxt.start - 1, nxt)
        if heading is not None:
            new_end = heading - 1
        else:
            gap = nxt.start - prev.end - 1
            if gap < _MIN_CONTINUE_GAP or gap > _MAX_CONTINUE_GAP:
                continue
            chunk = lines[prev.end:nxt.start - 1]
            if any(parse_section_heading(ln) for ln in chunk):
                continue
            new_end = nxt.start - 1
        if new_end > prev.end:
            by_id[id(prev)] = Span(
                ref=prev.ref, label=prev.label, parent=prev.parent,
                start=prev.start, end=new_end,
                ref_from=prev.ref_from, label_from=prev.label_from)
    return [by_id[id(s)] for s in ordered]


def _heading_line_of(lines: list[str], lo: int, hi: int, nxt: Span) -> int | None:
    """洞里哪一行是下一条的标题。找不到就 None。"""
    for index in range(lo, hi + 1):
        line = lines[index - 1].strip()
        if not line:
            continue
        if nxt.label and nxt.label in line and len(line) <= 80:
            if not nxt.ref or line.startswith(str(nxt.ref)):
                return index
        if nxt.ref and re.match(
                rf"^{re.escape(str(nxt.ref))}(\s|$|[：:、.．])", line):
            rest = line[len(str(nxt.ref)):].strip(" ：:、.．")
            if rest and len(rest) <= 60 and not rest.endswith("."):
                return index
    return None


def fill_heading_holes(spans: list[Span], lines: list[str]) -> list[Span]:
    """大段没人认领时，按附录 / Part / 「6. Title」切开。

    提示词写了「附件清单不是条款」，模型常把 Appendix A 整章跳过。
    NIST.AI.100-1 的 1302–1620 行就是这样空掉的。
    """
    if not lines:
        return spans
    occupied = set()
    refs = {s.ref for s in spans if s.ref}
    for span in spans:
        occupied.update(range(span.start, span.end + 1))
    extra: list[Span] = []
    for lo, hi in _holes(spans, len(lines)):
        hits: list[tuple[int, str, str]] = []
        for index in range(lo, hi + 1):
            if index in occupied:
                continue
            parsed = parse_section_heading(lines[index - 1])
            if parsed:
                hits.append((index, parsed[0], parsed[1]))
        if not hits:
            continue
        for pos, (start, ref, label) in enumerate(hits):
            if _heading_ref_taken(ref, refs):
                continue
            next_start = hits[pos + 1][0] if pos + 1 < len(hits) else hi + 1
            end = next_start - 1
            while end > start and _line_is_chrome(lines[end - 1]):
                end -= 1
            if not label and start < end:
                label = lines[start].strip().rstrip(":")
            extra.append(Span(
                ref=ref, label=label, parent=None,
                start=start, end=max(start, end),
                ref_from="original",
                label_from="original" if label else "derived",
            ))
            refs.add(ref)
    return spans + extra


def _heading_ref_taken(ref: str, refs: set[str]) -> bool:
    """「D」和「Appendix D」是同一个附录，模型先占了其中一个就别再补。"""
    if ref in refs:
        return True
    if len(ref) == 1 and f"Appendix {ref}" in refs:
        return True
    if ref.startswith("Appendix "):
        letter = ref.split()[-1]
        if letter in refs:
            return True
    return False


def _line_is_chrome(line: str) -> bool:
    from framework_reader.userframework.catalog import _is_chrome
    return _is_chrome(line)


def uncovered(spans: list[Span], total_lines: int,
              lines: list[str] | tuple = ()) -> list[Problem]:
    """哪些行没被任何条款收进去。**不静默丢**——用户要知道有内容没进来。

    表翻页留下的 Categories / Continued / Page N 四行看起来像「漏切」，
    其实是版式。有原文行时把这种洞折进「一两行」那句，不单独点名。
    文首封面/目录单独说，不跟「没切出条款」混在一起。
    """
    holes = _holes(spans, total_lines)
    big: list[tuple[int, int]] = []
    small = 0
    front: tuple[int, int] | None = None
    for lo, hi in holes:
        if _is_front_matter_hole(lo, hi, lines):
            front = (lo, hi)
            continue
        if hi == total_lines and hi - lo + 1 <= 3:
            small += 1
            continue
        if hi - lo + 1 >= _HOLE_WORTH_NAMING and not _hole_is_chrome(lo, hi, lines):
            big.append((lo, hi))
        else:
            small += 1
    out: list[Problem] = []
    if front:
        lo, hi = front
        out.append(Problem(
            "front_matter",
            f"Lines {lo}–{hi} at the top are cover page and table of contents, not clauses - that is expected."))
    out.extend(
        Problem("uncovered", f"Lines {lo}–{hi} of the source could not be cut into clauses.")
        for lo, hi in big
    )
    if small:
        out.append(Problem(
            "uncovered",
            f"{small} more spot(s) of one or two uncaptured lines - most likely clause titles "
            "or section markers, which is expected."))
    return out


def _hole_is_chrome(lo: int, hi: int, lines: list[str] | tuple) -> bool:
    if not lines or hi < lo:
        return False
    chunk = lines[lo - 1:hi]
    if not chunk:
        return False
    return all(not ln.strip() or _line_is_chrome(ln) for ln in chunk)


def _is_front_matter_hole(lo: int, hi: int, lines: list[str] | tuple) -> bool:
    if lo != 1 or not lines or hi < lo:
        return False
    text = "\n".join(lines[:hi]).lower()
    return "table of contents" in text or "目录" in text


def _name(span: Span) -> str:
    """报错要能指认是哪一条，否则用户对不上原文。"""
    return span.ref or span.label or "an unnamed clause"


def plan_calls(text: str) -> list[tuple[int, int]]:
    """要发几次、每次覆盖哪几行。**每一行恰好落在一个区间里**，不漏不重。

    塞得下就一次过：模型看得到全文，章节层级和编号体系都在眼前，切得最准，
    而且没有跨块合并这个问题——那是这条管线里最难写对的一块。设计 §2.1
    """
    lines = text.splitlines()
    if not lines:
        return []
    if sum(len(line) for line in lines) <= ONE_SHOT_MAX_CHARS:
        return [(1, len(lines))]
    out: list[tuple[int, int]] = []
    start = 1
    size = 0
    for index, line in enumerate(lines, start=1):
        # `size and` 这个条件是给「单独一行就超了」留的（表格被抽成一行）：
        # 切不动它，就让它自己一块。少了这个判断，这里会空转出一堆空区间。
        if size and size + len(line) > ONE_SHOT_MAX_CHARS:
            out.append((start, index - 1))
            start, size = index, 0
        size += len(line)
    out.append((start, len(lines)))
    return out


def shift(spans: list[Span], offset: int) -> list[Span]:
    """把块内行号搬到整份文档的坐标系里。

    模型看到的是第二块的第 1 行，那在整份文档里是第 301 行。少了这一步，
    第二块以后的条款正文全都会从文档开头截。
    """
    if not offset:
        return spans
    return [
        Span(ref=s.ref, label=s.label, parent=s.parent,
             start=s.start + offset, end=s.end + offset)
        for s in spans
    ]


@dataclass(frozen=True)
class Outline:
    spans: list[Span]
    problems: list[Problem]
    calls: int


def load_prompt() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parent.parent / "prompts" / "outliner.md"
    ).read_text(encoding="utf-8")


def outline_document(text: str, *, client, model: str,
                     on_chunk=None) -> Outline:
    """跑完整条：分块 → 逐块调模型 → 解析 → 搬坐标 → 校验 → 报未覆盖。

    `client` 是任何有 `complete(system, messages, *, model, max_tokens)` 的
    对象——`GuardedClient` 就是。测试注入假的。

    **一块失败不带走其余块。** 重跑一整份文档要重花一次钱，而失败的那一块
    是哪几行要说出来，否则用户不知道原文的哪一段没进来。
    """
    from framework_reader.llm.client import Message

    system = load_prompt()
    lines = text.splitlines()
    spans: list[Span] = []
    problems: list[Problem] = []
    calls = 0
    pieces = plan_calls(text)
    for lo, hi in pieces:
        piece = "\n".join(lines[lo - 1:hi])
        calls += 1
        try:
            raw = client.complete(
                system, [Message(role="user", content=numbered(piece))],
                model=model, max_tokens=16384)
        except Exception as exc:                  # noqa: BLE001
            # 不只报异常类名——原因（HTTP 错误、response_format 不被支持、
            # 厂商返回结构意外……）都在 ``str(exc)`` 里，全拼到 detail 里给
            # 下次接手的人看。
            problems.append(Problem(
                "not_json",
                f"Lines {lo}–{hi} did not finish;"
                f" ({type(exc).__name__}: {str(exc).splitlines()[0][:200]}), "
                "results from the other chunks were kept."))
        else:
            piece_spans, piece_problems = parse_outline(raw)
            # 模型看到的是块内行号，落库要的是全文行号。
            spans.extend(shift(piece_spans, offset=lo - 1))
            problems.extend(piece_problems)
        # 失败的那一块也算跑完了——不然进度条会停在那儿不动。
        if on_chunk is not None:
            on_chunk(calls, len(pieces))
    # **先对齐再校验。** 整体差一行的时候，校验会把一堆本来对的条款
    # 判成越界或重叠——那些「问题」是假的，根源只有一个偏移。
    spans, snapped = snap_to_headings(spans, lines)
    problems.extend(snapped)
    # 章节号切完再收条款表。模型把 GOVERN 1.1 整张吞进「5.1 Govern」
    # 时，这里按行首的前缀编号拆开——不靠它下次认对。公司制度没有
    # 这种前缀，这一步是空操作。
    from framework_reader.userframework.catalog import apply_catalog

    spans, cataloged = apply_catalog(spans, lines)
    problems.extend(cataloged)
    # 上一条被截短的续行并回去；模型跳过的附录/摘要按标题补切。
    spans = close_small_gaps(spans, lines)
    before = len(spans)
    spans = fill_heading_holes(spans, lines)
    if len(spans) > before:
        problems.append(Problem(
            "catalog",
            f"Another {len(spans) - before} section(s)/appendix(ices) the model skipped were recovered from headings."))
    kept, validation = validate(spans, total_lines=len(lines), lines=lines)
    # 补编号与标题**在校验之后**：先把不可信的丢掉，再给留下的补齐标识。
    kept = fill_gaps(kept, lines)
    return Outline(
        spans=kept,
        problems=problems + validation + uncovered(kept, len(lines), lines=lines),
        calls=calls,
    )


# 补出来的标题最长这么些字。再长就不是标题是摘要了。
_LABEL_MAX = 24
_SENTENCE_END = "。！？；\n"


def fill_gaps(spans: list[Span], lines: list[str]) -> list[Span]:
    """原文没有编号或标题时补上，并标成 `derived`。

    **这条线要划清楚：正文不能由 AI 生成**（它是用户制度的原话，起草的解读、
    自评的证据全基于它），**编号和标题能**——它们是编目用的标识，不是制度的
    内容。留空的代价是这条条款根本存不进库（`user_control.id` 是主键），
    等于把我们的问题推给用户，而他不会改。

    编号跟随原文的体系：父条款是 `3.2`，补出来的子条款就是 `3.2.1`、`3.2.2`。
    看起来和原文一体，后面做映射、写报告引用起来自然——代价是光看编号
    分不出哪个是补的，所以 `ref_from` 必须跟着落库、预览页必须标出来。

    **不碰行号，也不碰上级。** 补标识不许动边界。
    """
    taken = {s.ref for s in spans if s.ref}
    counters: dict[str, int] = {}
    out: list[Span] = []
    for span in spans:
        ref, ref_from = span.ref, span.ref_from
        if not ref:
            base = span.parent or ""
            # 父条款自己也是补的时候 `parent` 是空的——那就退回顶层编号，
            # 否则会得到「.1」这种东西。
            ref = _next_ref(base, taken, counters)
            taken.add(ref)
            ref_from = "derived"
        elif ref_from == "original":
            ref_from = _verify(lines, span, ref)
        label, label_from = span.label, span.label_from
        if not label:
            label = _derive_label(lines, span) or ref
            label_from = "derived"
        elif label_from == "original":
            label_from = _verify(lines, span, label)
        out.append(Span(ref=ref, label=label, parent=span.parent,
                        start=span.start, end=span.end,
                        ref_from=ref_from, label_from=label_from))
    return out


# 往条款正文上面看几行——编号和标题按提示词的要求不含在正文里，
# 它们在上一行（或者，标题正文同行时，就在第一行里）。
_HEADING_LOOKBACK = 2


def _verify(lines: list[str], span: Span, value: str) -> str:
    """这几个字原文里到底有没有。**模型的自述和它的输出一样不可信。**

    实测：它给了 `ref="4"`、`label="施行与解释"`，两个都是自己编的，
    却没填 `ref_from`——按「有值即原文的」算，就成了「原文里就有」。
    那正是这个产品最不能出的错。

    正文被截空的分组标题没有原文可核对，那时候只能信它说的。
    """
    if span.end < span.start:
        # 截成空的分组标题：没有正文可核对，只能信它说的。
        return "original"
    lo = max(1, span.start - _HEADING_LOOKBACK)
    window = "\n".join(lines[lo - 1:span.end])
    if not window.strip():
        return "original"
    return "original" if value in window else "derived"


def _next_ref(base: str, taken: set[str], counters: dict[str, int]) -> str:
    """`3.2` 下面依次给 `3.2.1`、`3.2.2`，跳过原文已经占掉的号。"""
    while True:
        counters[base] = counters.get(base, 0) + 1
        candidate = f"{base}.{counters[base]}" if base else str(counters[base])
        if candidate not in taken:
            return candidate


def _derive_label(lines: list[str], span: Span) -> str:
    """拿正文第一句当标题。**这是兜底**——正常情况下模型会起一个更好的。"""
    body = slice_lines("\n".join(lines), span.start, span.end).strip()
    if not body:
        return ""
    cut = len(body)
    for index, char in enumerate(body):
        if char in _SENTENCE_END:
            cut = index
            break
    return body[:min(cut, _LABEL_MAX)].strip("，、 　")


# 标题行在正文起点附近多远的范围内找。模型差三行以上就不是「算错一位」，
# 那是切歪了，不该靠整体平移去救。
_SNAP_WINDOW = 3


def snap_to_headings(spans: list[Span],
                     lines: list[str]) -> tuple[list[Span], list[Problem]]:
    """拿原文里的标题行把边界对回去。

    模型算行号会**系统性地差一行**——实测同一份文档同一个提示词，两次跑
    一次全对、一次每条都 +1。提示词压得住一时，压不住每一次。

    但这件事代码能自己查：模型给了 `ref="3.1"`，那「3.1」那一行在原文里的
    位置是确定的，正文就该从它的下一行开始（标题正文同行时就是那一行本身）。
    和核对编号来源同一个思路——**不信模型的自述，拿原文核对。**

    **只在多数条款给出同一个偏移时才整体挪。** 个别条款对不上是它自己切歪了，
    拿它去挪所有人，会把对的那些也弄错。

    挪了就报出来。悄悄挪一行，用户永远不知道我们动过他的边界。
    """
    if not spans:
        return spans, []
    deltas: list[int] = []
    for span in spans:
        expected = _expected_start(span, lines)
        if expected is not None:
            deltas.append(expected - span.start)
    if not deltas:
        return spans, []
    delta = max(set(deltas), key=deltas.count)
    if delta == 0 or deltas.count(delta) * 2 < len(spans):
        return spans, []
    moved = [
        Span(ref=s.ref, label=s.label, parent=s.parent,
             start=s.start + delta, end=s.end + delta,
             ref_from=s.ref_from, label_from=s.label_from)
        for s in spans
    ]
    if any(s.start < 1 or s.end > len(lines) for s in moved):
        # 挪完越界说明这个偏移是假的。宁可不挪——校验层会照常报越界。
        return spans, []
    return moved, [Problem(
        "snapped",
        f"The model's line numbers were off by {delta:+d} overall; aligned to the headings in your source. "
        "Please double-check each clause's start and end.")]


def _expected_start(span: Span, lines: list[str]) -> int | None:
    """这一条的正文**本该**从第几行开始。找不到标题行就回 None。"""
    needle = span.ref or span.label
    if not needle:
        return None
    lo = max(1, span.start - _SNAP_WINDOW)
    hi = min(len(lines), span.start + 1)
    for number in range(lo, hi + 1):
        line = lines[number - 1]
        if needle not in line:
            continue
        # 标题行上除了编号和标题还有别的字，说明正文就在这一行里。
        rest = line.replace(span.ref, "", 1).replace(span.label, "", 1)
        return number if len(rest.strip(" 　、.．：:")) > 6 else number + 1
    return None
