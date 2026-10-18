"""Explicitly import local Markdown snapshots into an atomic vector index."""
import argparse
import json
from pathlib import Path
import sys

from app.config import PROJECT_ROOT
from app.knowledge import KnowledgeError, build_index, index_path, save_index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default="sample_data/policies")
    parser.add_argument("--confirm", action="store_true", help="Explicitly approve importing documents and replacing the local index")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("--confirm is required for this write action")
    try:
        source = (PROJECT_ROOT / Path(args.source_dir)).resolve()
        if not source.is_relative_to(PROJECT_ROOT):
            raise KnowledgeError("导入目录必须位于项目工作区内")
        index = build_index(source)
        save_index(index, index_path())
    except (KnowledgeError, OSError, ValueError):
        print("文档导入失败；原有索引未被替换。请检查目录、UTF-8 文档和工作区写权限。", file=sys.stderr)
        return 1
    print(json.dumps({"documents": len(index.documents), "chunks": len(index.chunks), "algorithm": index.algorithm, "revision": index.revision}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
