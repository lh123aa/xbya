# -*- coding: utf-8 -*-
"""备用模型必须是「快且不烧思维链」的（D41）。

## 缺陷现场（量化）

受控端到端实测闲聊链路：

    ① 测激   -> ② ASR 709ms ✓ -> ③ LLM 13.24s ✗ <- -> ④ TTS 1.81s ✓

**LLM 一段占掉 13 秒**，而 ASR+TTS 合计只要 2.5 秒。用户感知
"听到半天不回复"的直接来源就是这里。

### 根因：备用模型是推理型，思维链吃掉了时间

Groq 主模型配额耗尽（429）后每次都走备用。实测备用
`nex-agi/nex-n2.5-pro:free` 的返回结构：

    第1次 4.87s   正文 31 字   思维链 507 字
    第2次 12.97s  正文 25 字   思维链 699 字
    第3次 5.63s   正文  6 字   思维链 799 字

5 次复测平均 **8.76 秒**，最慢 **17.29 秒**；每次都先吐 400~800 字
看不见的推理，才给 20~30 字正文。

对照实测 `nex-agi/nex-n2.5-mini:free`：平均 **1.03 秒**，快 8.5 倍。

## 本用例钉住什么

1. **配置里的备用模型不能是已知的"慢推理型"** —— 特别是别再用
   实测 8.76s 的 pro 变体。
2. `_stream_deadline_sec` 必须存在且是个合理上限 —— 它是"用户最多等多久"
   的最后防线（当前 6.0s）。没有它，推理模型能把一轮拖到 17 秒以上。
3. 空正文（思维链吃光 max_tokens）时必须**转备用链路**而不是静默返回空 ——
   这条已有实现，用例防它被改坏。
"""

import inspect

import pytest

#: 实测的推理型模型（思维链数百字、平均 >8s）。用作备用会让用户干等。
SLOW_REASONING_MODELS = {
    'nex-agi/nex-n2.5-pro:free': 8.76,      # 实测平均秒数
}


class TestFallbackModelIsFast:
    def test_config_fallback_is_not_the_slow_reasoning_one(self):
        import yaml
        from pathlib import Path

        cfg = Path(__file__).resolve().parent.parent / 'config.yaml'
        if not cfg.exists():
            pytest.skip('config.yaml 不存在（未跟踪文件）')
        c = yaml.safe_load(cfg.read_text(encoding='utf-8'))
        fb = (c.get('plugins', {}).get('llm', {}).get('params', {})
              .get('fallback_model', ''))
        assert fb, '没有配置 fallback_model'
        assert fb not in SLOW_REASONING_MODELS, (
            f'备用模型 {fb} 实测平均 '
            f'{SLOW_REASONING_MODELS.get(fb)}s（思维链数百字），'
            f'会让用户"听到半天不回复"。已知更快的选择：'
            f'nex-agi/nex-n2.5-mini:free（实测 1.03s）'
        )

    def test_config_has_a_fallback_at_all(self):
        """没有备用 = 主失败时直接哑掉（D37 就是这么来的）。"""
        import yaml
        from pathlib import Path

        cfg = Path(__file__).resolve().parent.parent / 'config.yaml'
        if not cfg.exists():
            pytest.skip('config.yaml 不存在')
        p = yaml.safe_load(cfg.read_text(encoding='utf-8'))
        params = p.get('plugins', {}).get('llm', {}).get('params', {})
        for k in ('fallback_api_key', 'fallback_base_url', 'fallback_model'):
            assert params.get(k), f'备用提供商缺 {k}'


class TestStreamDeadlineGuardsLatency:
    """总时长上限是"用户最多等多久"的最后防线。"""

    def test_deadline_exists_and_is_bounded(self):
        from plugins.llm.openrouter.plugin import UniversalLLM

        llm = UniversalLLM(api_key='k', base_url='https://x/v1', model='m')
        d = getattr(llm, '_stream_deadline_sec', None)
        assert d is not None, '没有流式总时长上限 —— 推理模型能把一轮拖到 17 秒以上'
        assert 2.0 <= d <= 12.0, (
            f'上限 {d}s 不合理：太小会频繁截断，太大用户干等'
        )

    def test_streaming_counts_reasoning_but_excludes_it_from_text(self):
        """思维链要计数（用于识别"吃光 max_tokens"），但**不能进正文**。

        若混进正文，TTS 会念出"用户问我明天星期几，我得先推算一下……"
        这种旁白 —— 用户听到的就是驴唇不对马嘴。

        定位实际实现：流式解析在 `_chat_stream_once`（不是 `chat`）。
        """
        from plugins.llm.openrouter.plugin import UniversalLLM

        # 找到真正含流式 delta 解析的方法
        src = None
        for name in dir(UniversalLLM):
            if name.startswith('__'):
                continue
            fn = getattr(UniversalLLM, name, None)
            if not callable(fn):
                continue
            try:
                s = inspect.getsource(fn)
            except (TypeError, OSError):
                continue
            if 'delta' in s:
                src = s
                break
        assert src, '找不到含 delta 解析的流式实现 —— 结构变了，请更新本用例'
        assert 'reasoning' in src, (
            '流式解析没有处理 reasoning 字段：推理型模型的思维链会混进正文，'
            'TTS 会把旁白念出来'
        )
        # 正文只收 content，不许把 reasoning 也 append 进 full
        import re
        appends = re.findall(r'full\.append\(([^)]+)\)', src)
        assert appends, '找不到正文收集点'
        assert all('reasoning' not in a for a in appends), (
            f'把 reasoning 追加进正文了: {appends}'
        )
