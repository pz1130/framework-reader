"""每一页都必须以 `<!doctype html>` 开头。

缺了它浏览器整站落进**怪异模式**（`document.compatMode === "BackCompat"`）。
实测同一份 HTML 只差这 15 个字节：

    无 doctype   documentElement.clientHeight = 1058（= 内容高度）
    有 doctype   documentElement.clientHeight = 1000（= 视口高度）

怪异模式下 `documentElement` 不再代表视口。任何按这组数字算「页面有多高、
要截多大」的东西——整页截图、`vh`／`100%` 的高度链——拿到的都是错的尺寸，
表现为右侧多出一条没绘制的空白带、底部按错误高度被切掉。

两个出口各有一条：网页壳（`views.page`）与发布页（`publish/template.py`）。
后者是要发给别人看的那一份，坏在那儿更贵。
"""
from framework_reader.publish import template
from framework_reader.publish.site import render_page
from framework_reader.web import views

DOCTYPE = "<!doctype html>"


def test_web_shell_declares_doctype():
    html = views.page("标题", "<p>正文</p>", csrf="tok", who="谁")
    assert html.startswith(DOCTYPE), html[:60]


def test_web_shell_declares_doctype_on_bare_pages():
    """登录页与邀请页走 bare 分支，也是一整份文档。"""
    html = views.page("登录", "<p>表单</p>", csrf="tok", who="", bare=True)
    assert html.startswith(DOCTYPE), html[:60]


def test_publish_page_declares_doctype():
    assert template.PAGE.startswith(DOCTYPE), template.PAGE[:60]


def test_rendered_publish_page_declares_doctype():
    """模板常量对了不等于产物对了——渲染完再看一眼。"""
    assert render_page([]).startswith(DOCTYPE)
