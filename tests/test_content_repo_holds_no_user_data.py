"""内容仓只放我们要发布的内容。主 spec §7.3.5

b971e12 把一份用户导入框架的解读提交进了 `content/interpretations/`——
用户自己公司的制度进了我们的产品仓库，还被 git 追踪。成因是起草器不分层，
一律写 `InterpretationStore`。存储层已经按 tier 分流（interpret/user_store.py），
这条测试守住：用户框架解读不许出现在内容仓里。
"""
from pathlib import Path

import pytest

from framework_reader.publish.site import FRAMEWORKS

ROOT = Path(__file__).resolve().parent.parent
INTERPRETATIONS = ROOT / "content" / "interpretations"


def test_the_content_repo_carries_no_user_framework():
    if not INTERPRETATIONS.exists():
        pytest.skip("没有内容目录")
    strays = {
        d.name for d in INTERPRETATIONS.iterdir()
        if d.is_dir() and d.name not in FRAMEWORKS
    }
    assert not strays, (
        f"用户导入框架的解读出现在内容仓里：{sorted(strays)}。"
        "它该落 user.sqlite——起草时用 interpret.user_store.store_for 选存储。"
    )
