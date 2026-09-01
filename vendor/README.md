# vendor/

本目录存放外部源文件，**永不进入 Git**（见 `.gitignore`）。

两类内容：
1. NIST 公共领域文件——由 `scripts/fetch_sources.sh` 自动取回
2. 已购买的受版权标准原文（如 ISO 27002）——手动放入，用于构建时的原文泄漏扫描比对

任何文件进入本目录前，先确认其来源已登记在 `content/allowed_sources.yaml`。
