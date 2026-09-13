"""繁简归一化测试（`agent/text_norm.py`）

这个模块存在的唯一理由是**修一个具体的、危险的失败**：

    ASR 把「删除桌面上的截图」转写成繁体「删除桌面上的截圖。」
    → 规则路由认得"删除"、认不出"截圖"
    → params 没有 pattern，而置信度仍是 0.95 没被拦下
    → 管线用实体栈兜底 → 删除对象变成"上一次搜索到的文件"

所以这里既测词表本身，也测**那个真实失败场景已经不复现**。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.text_norm import (  # noqa: E402
    T2S_AMBIGUOUS_TRAD, T2S_CHARS, T2S_WORDS, has_traditional, to_simplified)


class TestToSimplified:
    """繁体词 → 简体词"""

    @pytest.mark.parametrize("trad, simp", [
        ("截圖", "截图"),
        ("刪除桌面上的截圖", "删除桌面上的截图"),
        ("刪除", "删除"),
        ("圖片", "图片"),
        ("壓縮包", "压缩包"),
        ("打開網頁", "打开网页"),
        ("電腦", "电脑"),
        ("螢幕截圖", "屏幕截图"),
        ("下載", "下载"),
        ("資料夾", "文件夹"),
        ("確定", "确定"),
        ("什麼", "什么"),
    ])
    def test_traditional_becomes_simplified(self, trad, simp):
        assert to_simplified(trad) == simp

    def test_simplified_input_is_unchanged(self):
        """已经是简体就不该被改动（这是"加了等于没加"的保证）"""
        for s in ("删除桌面上的截图", "找一下桌面上的PDF文件", "打开资源管理器",
                  "把下载目录里的安装包挪到文档"):
            assert to_simplified(s) == s

    def test_unknown_text_is_unchanged(self):
        """词表外的文本原样返回 —— 不命中就是恒等，所以没有"改坏"的回退风险"""
        for s in ("今天天气不错", "hello world", "12345", "C:\\Windows\\System32"):
            assert to_simplified(s) == s

    def test_mixed_variant_sentence(self):
        """真实 ASR 输出是**混着**的：简化字 + 繁体词同时在句子里"""
        got = to_simplified("删除桌面上的截圖。")
        assert got == "删除桌面上的截图。"

    def test_empty_and_none(self):
        assert to_simplified("") == ""
        assert to_simplified(None) == ""

    def test_ambiguous_single_chars_are_not_touched(self):
        """**只做整词替换**：单字歧义（干/面/后/里）绝不碰

        字符级繁简转换会在这里翻车：「后面」的"后"与「皇后」的"后"、
        「面条」的"面"与「里面」的"面" 传统写法不同，一个字对一个字地替换
        必然误改无关词汇。本模块只用多字词，所以这些字保持原样。
        """
        for s in ("皇后", "面条", "里面", "干嘛", "后面", "前后"):
            assert to_simplified(s) == s

    def test_longer_word_wins(self):
        """包含关系要长词优先（螢幕截圖 不能被 截圖 先吃掉一半）"""
        assert to_simplified("螢幕截圖") == "屏幕截图"

    def test_every_entry_maps_to_simplified_output(self):
        """词表自校验：所有键都能被替换，且替换结果不再含该繁体键"""
        for trad, simp in T2S_WORDS.items():
            assert to_simplified(trad) == simp, trad
            assert to_simplified(trad) != trad, f"{trad} 的替换等于原样，等于没生效"

    def test_no_entry_is_a_single_char(self):
        """**结构约束**：`T2S_WORDS` 是**整词表**，里面不允许出现单字条目

        单字繁简映射单独放在 `T2S_CHARS` 里（它有自己的不变量，见
        `TestT2SChars`）。两张表混在一起会出现"这个 `图` 到底是整词还是单字"的
        糊涂账 —— 所以词表只管整词，单字一概拒收。
        """
        bad = [k for k in T2S_WORDS if len(k) < 2]
        assert bad == [], f"词表里出现单字条目：{bad}"

    def test_no_identity_entry(self):
        """**结构约束**：不允许恒等条目（键 == 值）

        恒等条目本身不做事，却会让匹配正则把**简体词**也算成命中 ——
        实测踩过：表里原有 `"桌面": "桌面"`，于是
        `has_traditional("删除桌面上的截图")` 误报为 True。
        """
        bad = [k for k, v in T2S_WORDS.items() if k == v]
        assert bad == [], f"词表里出现恒等条目：{bad}"


class TestHasTraditional:
    """自检辅助"""

    def test_detects(self):
        assert has_traditional("截圖") is True
        assert has_traditional("删除桌面上的截圖。") is True

    def test_not_detected(self):
        assert has_traditional("删除桌面上的截图") is False
        assert has_traditional("今天天气不错") is False
        assert has_traditional("") is False
        assert has_traditional(None) is False

    def test_char_table_does_not_create_false_positive(self):
        """字级表不得把**简体**文本判成繁体

        实测踩过：字级表里混进恒等条目 `"器": "器"`，于是
        `has_traditional("打开资源管理器")` 误报 True（"器"被当成繁体命中）。
        """
        for s in ("打开资源管理器", "打开任务管理器", "删除桌面上的截图",
                  "帮我看看电脑还有多少电"):
            assert has_traditional(s) is False, s


class TestT2SChars:
    """字级表的结构不变量（`T2S_CHARS`）

    字级表是补"词表没覆盖到的字"用的（`內存`/`氣溫`/`硬盤`…）。
    它的安全性来自**方向**：繁→简是多对一，`圖`→`图` 不会产生歧义；
    反过来才有歧义。所以这里的不变量是"自洽 + 不成环 + 不碰歧义字"。
    """

    def test_every_entry_is_single_char(self):
        bad = [k for k, v in T2S_CHARS.items() if len(k) != 1 or len(v) != 1]
        assert bad == [], f"字级表出现非单字条目：{bad}"

    def test_no_identity_entry(self):
        bad = [k for k, v in T2S_CHARS.items() if k == v]
        assert bad == [], f"字级表出现恒等条目：{bad}"

    def test_no_cycle(self):
        """值不能再是键（否则 `a→b→a` 会随替换顺序摇摆）"""
        bad = [k for k, v in T2S_CHARS.items() if v in T2S_CHARS]
        assert bad == [], f"字级表成环：{bad}"

    def test_values_are_fixed_points(self):
        """每个值再归一化一次必须不变（"简体输入不动"这条保证的机器化版本）"""
        bad = [f"{k}->{v}" for k, v in T2S_CHARS.items()
               if to_simplified(v) != v]
        assert bad == [], f"值不是不动点：{bad}"

    def test_ambiguous_traditional_chars_are_absent(self):
        """**故意不收**多义繁体字：`乾`（乾坤/乾燥）、`徵`（宫商角徵羽）

        这两个字简化到哪个简体字要看语境，字级替换会换错意思。
        这条断言把"后来者图省事加进去"钉死。
        """
        bad = [c for c in T2S_AMBIGUOUS_TRAD if c in T2S_CHARS]
        assert bad == [], f"歧义繁体字混进字级表：{bad}"

    def test_speak_words_are_normalized(self):
        """`說話` 必须归一成 `说话`（不能留半繁半简）

        实测缺口：`說` 在表里但 `話` 不在，于是 `說話` → `说話` ——
        **半繁半简**。这比全繁更隐蔽：人一眼看不出错，路由匹配 `说话`
        却匹配不到，用户说"你能说话吗"会被当成闲聊。
        补录那两个字时把这条钉住。
        """
        assert to_simplified("說話") == "说话"
        assert to_simplified("那你也說話嗎?") == "那你也说话吗?"
        # 詞表级优先：`搜尋` 走的是整词映射 → `搜索`（不是逐字的 `搜寻`）
        assert to_simplified("搜尋") == "搜索"


class TestRouterVocabularyCoverage:
    """**覆盖合同**：规则路由词表的繁体写法必须能归一化回简体关键词

    这是缺陷 14 的一般化。原先只修了「截圖」，验收阶段用
    `python tools/measure_asr_stimulus.py --synth 6` 一测才发现
    「給我講個笑話」漏了；再逐条核对规则路由的真实词表，又发现
    `內存`/`電池`/`硬盤`/`氣溫`/`啟動`/`減去`/`等於`/`訪問`/`粘貼` 全漏 ——
    它们都是**路由关键词**，漏了就等于"用户说这句话时静默丢参数"。

    所以这里把覆盖写成**表格化的合同**：往路由词表里加关键词时，
    若忘了在 `agent/text_norm.py` 补对应写法，就在这里补一行 —— 补不出来
    就说明归一化没覆盖，测试会红。
    """

    PAIRS = [
        # 文件搜索 / 类型
        ("刪除桌面上的截圖", "删除桌面上的截图"),
        ("幫我找一下桌面上的PDF檔案", "帮我找一下桌面上的PDF文件"),
        ("打開資料夾", "打开文件夹"),
        ("重命名這個檔案", "重命名这个文件"),
        ("挪個位置放到文檔", "挪个位置放到文档"),
        ("刪掉不要了", "删掉不要了"),
        ("打開C盤Windows文件夾", "打开C盘Windows文件夹"),
        # 系统状态
        ("內存還剩多少", "内存还剩多少"),
        ("電池還有電嗎", "电池还有电吗"),
        ("硬盤空間夠不夠", "硬盘空间够不够"),
        ("系統狀態怎麼樣", "系统状态怎么样"),
        ("電腦怎麼樣", "电脑怎么样"),
        ("機器狀態呢", "机器状态呢"),
        # 剪贴板 / 计算 / 翻译
        ("複製這段文字", "复制这段文字"),
        ("粘貼到文檔裡", "粘贴到文档里"),
        ("剪貼板裡有什麼", "剪贴板里有什么"),
        ("翻譯成英文", "翻译成英文"),
        ("用英文怎麼說", "用英文怎么说"),
        ("日語怎麼說", "日语怎么说"),
        ("算一下等於多少", "算一下等于多少"),
        ("減去三", "减去三"),
        # 系统工具
        ("打開記事本", "打开记事本"),
        ("打開計算器", "打开计算器"),
        ("打開畫圖", "打开画图"),
        ("打開瀏覽器", "打开浏览器"),
        ("打開任務管理器", "打开任务管理器"),
        ("打開資源管理器", "打开资源管理器"),
        ("打開命令提示符", "打开命令提示符"),
        ("啟動一下", "启动一下"),
        ("截個圖", "截个图"),
        ("屏幕截圖", "屏幕截图"),
        ("執行命令", "执行命令"),
        ("運行命令", "运行命令"),
        ("命令行裡執行一下", "命令行里执行一下"),
        # 提醒 / 天气
        ("提醒我三分鐘後", "提醒我三分钟后"),
        ("別忘了提醒一下", "别忘了提醒一下"),
        ("定個鬧鐘", "定个闹钟"),
        ("天氣怎麼樣", "天气怎么样"),
        ("氣溫多少度", "气温多少度"),
        ("熱不熱", "热不热"),
        ("要不要帶傘", "要不要带伞"),
        # 网页 / 搜索
        ("這個網頁裡說了什麼", "这个网页里说了什么"),
        ("頁面內容是什麼", "页面内容是什么"),
        ("這篇文章講了什麼", "这篇文章讲了什么"),
        ("打開網站訪問一下", "打开网站访问一下"),
        ("打開百度", "打开百度"),
        ("打開淘寶", "打开淘宝"),
        ("打開京東", "打开京东"),
        ("上網查一下", "上网查一下"),
        ("問一下網上搜", "问一下网上搜"),
        # 确认 / 取消 / 闲聊
        ("給我講個笑話", "给我讲个笑话"),
        ("甚麼", "什么"),
        ("確定", "确定"),
        ("確認一下", "确认一下"),
        ("繼續", "继续"),
        ("停下來", "停下来"),
    ]

    @pytest.mark.parametrize("trad,simp", PAIRS)
    def test_router_vocabulary_traditional_form_normalizes(self, trad, simp):
        assert to_simplified(trad) == simp

    def test_simplified_sentences_are_untouched(self):
        """反向保证：**简体**说法一个字都不许改（改了就是把匹配带偏）"""
        for s in ("删除桌面上的截图", "帮我看看电脑还有多少电", "打开资源管理器",
                  "内存还剩多少", "电池还有电吗", "硬盘空间够不够", "启动一下浏览器",
                  "减去三", "粘贴到文档里", "访问这个网站", "今天天气不错",
                  "皇后", "面条", "里面", "干嘛", "后面", "前后"):
            assert to_simplified(s) == s, s
