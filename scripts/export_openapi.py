#!/usr/bin/env python3
"""从 FastAPI 应用导出 OpenAPI 到 docs/openapi.yaml。

用法：
    python scripts/export_openapi.py            # 写到 docs/openapi.yaml
    python scripts/export_openapi.py --stdout   # 打印到标准输出

说明：只读取 app.openapi()（装配期无需连库/连 MQTT），不启动服务。
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def build_spec() -> dict:
    import main  # noqa: F401  导入即完成路由装配
    from main import app
    return app.openapi()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("-o", "--output", default=None)
    args = ap.parse_args()

    spec = build_spec()
    try:
        import yaml
        text = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False,
                              default_flow_style=False)
    except ImportError:
        # 无 pyyaml 时退回 JSON（格式仍可被 OpenAPI 工具识别）
        text = json.dumps(spec, ensure_ascii=False, indent=2)

    if args.stdout:
        print(text)
        return 0

    out = args.output or os.path.join(ROOT, "docs", "openapi.yaml")
    with io.open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"已导出 {out}（{len(spec.get('paths', {}))} 个路径）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
