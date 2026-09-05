# Web 工作台 UI 翻新：苹果深色风 + 克制动效

日期：2026-08-28 ｜ 状态：已完成
用户拍板：黑底白字为主、克制版动效、核心先行（高频页精修，其余吃变量自动变体）。

## 硬约束

1. **零外部资源、零构建链不变**（views.py:145 的既定决策）：动画 = 纯 CSS
   transition/animation + 原生 IntersectionObserver 几行 JS；字体 = 系统栈
   （macOS 上即 SF Pro + 苹方）。不引 React/CDN/字体文件。
2. **发布手册不跟着翻**：THEME_CSS（publish/theme.py）是工作台与发布页共用
   的令牌，手册是打印用的正式文档，保持浅色。工作台的深色令牌在 `_CSS`
   里用 `:root{}` 覆盖（`<style>` 注入顺序在后，同特异性后者胜）。
3. **类名与文本全不动**：tests/web 36 处依赖 class=/文本断言，翻新只动
   CSS 与壳的呈现属性，不重排语义标签。
4. `prefers-reduced-motion: reduce` 时全部动画关闭（可及性）。

## 视觉规格（苹果深色）

- 底 `--ground:#000`、面 `--surface:#161617`、沉 `--sunk:#1d1d1f`、
  字 `--ink:#f5f5f7 / --body:#d2d2d7 / --muted:#86868b`、线 `--rule:#2c2c2e`
- 链接与强调 `--accent:#2997ff`（苹果深色模式链接蓝，黑白灰里唯一的
  功能色）；主按钮改白底黑字胶囊（苹果主按钮）；警示 `--ask` 转苹果红系
- 标题：紧字距（-0.02em）、大字号、细线分隔；`.wrap` 放宽到 62rem
- 顶栏 `.top`：sticky + 负 margin 拉全宽 + `backdrop-filter` 毛玻璃
  （rgba 黑 72%）——HTML 结构不变，纯 CSS

## 动效清单（克制版）

- 页面入场：`.wrap` 一次性 fade-rise（纯 CSS animation，无需 JS）
- 卡片/表格行 hover：背景与边框过渡 0.2s，卡片 `translateY(-2px)`
- 进度条 `.bar i` 宽度过渡
- 滚动渐现 `.reveal`：observer 基建 + `.js` 类门（no-js 默认可见），
  Task 3 给卡片网格挂类
- reduced-motion 全关

## 任务

1. [x] **Task 1 设计令牌 + 壳**：`_CSS` 头部覆盖变量、字体栈、毛玻璃顶栏、
   `.wrap` 布局、入场动画、`.reveal` 基建 + page() 里 5 行 observer 脚本
   （顺带把 `_CSS` 里的字面 `\n` 转义清成真实多行三引号）。
2. [x] **Task 2 组件层**：按钮（白胶囊）、输入框、表格、卡片、note/hint/err、
   bar/状态标签——全部只改 `_CSS`，页面函数不动。
3. [x] **Task 3 核心页精修**：frameworks 卡片挂 `.reveal`、framework 条款表、
   control 条款页与对话、换版对照页。少量内联 style 清理。
4. [x] **Task 4 回归 + 截图验证**：`make test` 全绿；起 dev server 用浏览器
   验证——截图被宿主浏览器面板未挂载挡住，改用逐元素计算样式审计：
   5 页 0 亮色残留、0 低对比文字，毛玻璃 sticky 顶栏实测生效；
   附带修掉 password 输入框未吃到新样式的残留。

## 明确不做

- 不改任何路由、权限、CSRF、审计逻辑（纯呈现层）
- 不动 THEME_CSS（发布手册保持浅色）
- 不做双主题切换（一期黑底定死；变量体系留了余地，想加是后续小活）
