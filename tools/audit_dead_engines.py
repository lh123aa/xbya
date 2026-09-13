# -*- coding: utf-8 -*-
"""验证：配置指向的 embedding / vector_db 引擎在运行时到底是死是活。

疑问来自 `tools/audit_plugin_params.py`：`plugins.embedding.engine: embed_anything`
与 `plugins.vector_db.engine: leann` 两处，对应的插件目录里**没有 plugin.py**。

本探针不靠读代码下结论，只跑真实加载路径：
  1. `PluginLoader.scan()` 能不能发现它们
  2. `PluginLoader.load()` 返回什么
  3. `FileService` 初始化后 `self.embedding` / `self.vector_db` 是什么类的实例
  4. 那个实例的 `embed()` / `add()` 真的产出什么（是零向量？还是假成功？）
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')


def main() -> int:
    from core.plugin_loader import PluginLoader

    print()
    print('=' * 78)
    print('  探针 1：scan() 发现哪些插件')
    print('=' * 78)
    pl = PluginLoader()
    pl.scan()
    names = sorted(pl.plugins.keys())
    print(f'  发现 {len(names)} 个: {names}')
    for want in ('embed_anything', 'leann'):
        print(f'  {want:16s} 被发现? {"是" if want in names else "否 ← 配置里写的就是它"}')

    print()
    print('=' * 78)
    print('  探针 2：load() 的返回值')
    print('=' * 78)
    for cap, eng in [('embedding', 'embed_anything'), ('vector_db', 'leann')]:
        got = pl.load(eng, params={})
        print(f'  {cap:11s} engine={eng:16s} -> {got!r}')

    print()
    print('=' * 78)
    print('  探针 3：FileService 真实初始化后的实际类型')
    print('=' * 78)
    from services.file_service import FileService

    svc = FileService()
    ok = svc.initialize()
    print(f'  FileService.initialize() -> {ok}')
    emb = getattr(svc, 'embedding', None)
    vdb = getattr(svc, 'vector_db', None)
    print(f'  svc.embedding  = {type(emb).__name__ if emb else None}')
    print(f'  svc.vector_db  = {type(vdb).__name__ if vdb else None}')

    print()
    print('=' * 78)
    print('  探针 4：这两个实例真的能干活吗')
    print('=' * 78)
    if emb is not None:
        try:
            vec = emb.embed('测试文本')
            nz = sum(1 for x in vec) if vec else 0
            print(f'  embedding.embed("测试文本") -> 长度 {len(vec) if vec else 0}, '
                  f'非零分量 {nz}')
            print(f'    前 5 个分量: {list(vec)[:5] if vec else None}')
        except Exception as e:
            print(f'  embedding.embed() 抛异常: {type(e).__name__}: {e}')
    if vdb is not None:
        try:
            r = vdb.add('id1', [0.1, 0.2, 0.3])
            print(f'  vector_db.add("id1", [0.1,0.2,0.3]) -> {r!r}')
            r2 = vdb.search([0.1, 0.2, 0.3], top_k=3)
            print(f'  vector_db.search(...) -> {r2!r}')
        except Exception as e:
            print(f'  vector_db.add() 抛异常: {type(e).__name__}: {e}')

    print()
    print('=' * 78)
    print('  探针 5：这两条路径还有别的消费者吗')
    print('=' * 78)
    import subprocess
    pat = r'get_plugin\(.embedding|get_plugin\(.vector_db|\.embedding\b|\.vector_db\b'
    out = subprocess.run(
        ['git', 'grep', '-n', '-E', pat],
        cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    lines = [l for l in (out.stdout or '').splitlines()
             if not l.startswith(('tools/', 'tests/'))]
    for l in lines[:25]:
        print('   ', l)
    print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
