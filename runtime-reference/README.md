# 运行后端参考快照

来源：虚拟机 /opt/greenhouse，2026-10-08只读采集。保留app、migrations、main.py、tcp_image_server.py和requirements.txt。

本分支额外保存运行代码用于与main中的正式源码比较；它不是已合并的发布版本。真实.env、数据库、uploads、令牌脚本、审计数据和虚拟环境没有包含，内嵌凭据已替换为占位符。

不要直接把本目录覆盖生产站点。先对照差异、形成PR并验证迁移与接口，再决定整合方式。
