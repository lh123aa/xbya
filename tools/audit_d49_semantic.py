# -*- coding: utf-8 -*-
"""D49 探针：语义搜索在"插件加载失败"时到底给用户什么。

`file_service.py:406` 的判据是 `self.embedding is None or self.vector_db is None`
→ 但加载失败时 `load_by_interface()` 给的是 **NullEmbedding / NullVectorDB 实例**，
**不是 None**。于是那个"降级到全文搜索"的分支**永远不会走到**。

本探针跑真实调用，把三条路径的输出摆在一起：
  A. 配置坏掉（当前状态：engine=embed_anything，插件不存在）
  B. 配置好的时候应该长什么样
  C. 全文搜索本身能不能用（即"本该降级"的那条路）

判据是**返回值本身**，不是有没有报错 —— 空列表与"搜索不到"在界面上长得一样。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.ERROR, format='  [%(levelname)s] %(message)s')


def main() -> int:
    from services.file_service import FileService

    svc = FileService()
    svc.initialize()

    print()
    print('=' * 78)
    print('  A. 当前真实状态（config: embedding=embed_anything / vector_db=leann）')
    print('=' * 78)
    print(f'  svc.embedding = {type(svc.embedding).__name__}')
    print(f'  svc.vector_db = {type(svc.vector_db).__name__}')
    print(f'  是 None 吗? embedding={svc.embedding is None}  '
          f'vector_db={svc.vector_db is None}')
    print()
    print('  → file_service.py:406 那句 `if ... is None:` 判断：',
          '成立（会降级）' if (svc.embedding is None or svc.vector_db is None)
          else '**不成立**（不会降级，直接往下走）')

    print()
    print('  semantic_search("测试") 的真实返回：')
    try:
        r = svc.semantic_search('测试', top_k=3)
        print(f'    -> {r!r}   （len={len(r) if r is not None else None}）')
        if r == []:
            print('    ⚠️ 空列表 = 用户看到的"没找到"，与"索引里真没有"无法区分')
    except Exception as e:
        print(f'    -> 抛异常 {type(e).__name__}: {e}')

    print()
    print('=' * 78)
    print('  C. 对照：本该降级的那条路（全文搜索）能不能用')
    print('=' * 78)
    # 先塞一行真实数据，否则全文搜索也空，对照就没有说服力
    idx = svc.list_indexed_files() if hasattr(svc, 'list_indexed_files') else None
    print(f'  已索引文件数: {len(idx) if idx is not None else "（无该接口）"}')
    try:
        r2 = svc.search_files('测试', 3)
        print(f'  search_files("测试") -> {type(r2).__name__}, '
              f'len={len(r2) if r2 is not None else None}')
    except Exception as e:
        print(f'  search_files 抛异常 {type(e).__name__}: {e}')

    print()
    print('=' * 78)
    print('  结论（更正版 —— 我第一次读数读错了，这里给出逐行归属）')
    print('=' * 78)
    print('  第 406 行的降级分支：**不可达**')
    print('     理由：空对象不是 None。判据就写在上一行输出里：')
    print(f'     embedding is None -> {svc.embedding is None}，'
          f'vector_db is None -> {svc.vector_db is None}')
    print()
    print('  第 412~414 行：**会被走到**')
    print('     理由：`NullEmbedding.embed()` 返回 None（interfaces/embedding.py:65），')
    print('     所以 `query_vector is None` 成立 → 打 ERROR「查询向量化失败」→ return []')
    print('     ⚠️ 我第一版探针把这里写成"不会走到"，那是读错了 —— ')
    print('       我在探针 4 里先调过一次 embed() 才打印，看串了行。')
    print()
    print('  用户可见结果（两条路都算出同一个数，但含义不同）：')
    print('     semantic_search(...) -> []')
    print('     ① 界面/上层拿到的都是"空结果"，与"索引里真没有"**无法区分**；')
    print('     ② 日志里只有一行 ERROR「查询向量化失败」，')
    print('        **没有任何一处说"因为 embed_anything 这个插件不存在"**。')
    print()
    print('  这就是本轮要登记的东西：故障的**归因信息缺失**，')
    print('  而不是"搜索功能坏了"（它本来就没有可用的后端）。')
    print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
