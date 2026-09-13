"""规则路由（Service Provider）

本地关键词 + 正则匹配，不调用 LLM，延迟 <10ms。
覆盖约 85% 的日常指令，剩余 15% 交给 LLMRouter 兜底。

算法：
1. 空文本 → chat（置信度 0）
2. 确认/取消响应 → confirm / cancel（由 Pipeline 判断是否真有待确认项）
3. 未实现能力排除（网页/天气等）→ chat
4. 意图关键词评分 → 取最高分
5. 按意图提取参数
6. 计算置信度（基础分 + 关键词加成 + 参数完整度 + 句式加成）
"""

import logging
import re
from typing import Any, Dict, List, Optional

from agent.message import AgentCommand
from agent.seams.router import DEFAULT_CONFIDENCE_THRESHOLD, IntentDef, RouterService
from agent.text_norm import to_simplified

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
#  词表
# ══════════════════════════════════════════════════════

#: 目录别名 → 白名单目录名
DIR_ALIASES: Dict[str, str] = {
    "桌面": "Desktop",
    "文档": "Documents",
    "下载": "Downloads",
    "图片": "Pictures",
    "照片": "Pictures",
    "相册": "Pictures",
}

#: 时间限定词 → time_range 取值
TIME_ALIASES: Dict[str, str] = {
    "今天": "today",
    "刚才": "today",
    "昨天": "yesterday",
    "前天": "last_2_days",
    "前两天": "last_2_days",
    "上周": "last_week",
    "最近": "last_week",
    "本周": "this_week",
    "这周": "this_week",
}

#: 文件类型别名 → glob 模式
EXT_ALIASES: Dict[str, str] = {
    "pdf": "*.pdf",
    "excel": "*.xlsx",
    "xlsx": "*.xlsx",
    "表格": "*.xlsx",
    "word": "*.docx",
    "docx": "*.docx",
    "ppt": "*.pptx",
    "幻灯片": "*.pptx",
    "图片": "*.png",
    "照片": "*.jpg",
    "截图": "*截图*",
    "视频": "*.mp4",
    "音乐": "*.mp3",
    "音频": "*.mp3",
    "压缩包": "*.zip",
    "txt": "*.txt",
    "文本": "*.txt",
    "代码": "*.py",
}

#: 提取文件名时要去除的噪声词（按长度倒序替换，避免短词先吃掉长词）
NOISE_WORDS: List[str] = [
    "帮我", "给我", "麻烦", "请你", "请", "我的", "我",
    "一下", "一个", "那个", "这个", "那些", "这些",
    "关于", "有关", "所有", "全部",
    "文件夹", "文件", "文档", "东西",
    "的", "上", "里", "中", "在", "把", "有", "没有", "有没有", "在哪",
    "找找", "找", "搜索", "搜", "查找", "查", "看看", "看",
] + list(DIR_ALIASES.keys()) + list(TIME_ALIASES.keys())

#: 未实现能力的提示词（命中则转闲聊，避免误路由到已有工具）
#: 注：随着工具集扩展，本表应持续收缩
UNSUPPORTED_HINTS: List[str] = [
    "邮件", "微信", "qq", "钉钉", "飞书",
    "股票", "汇率", "基金", "发票",
    "播放音乐", "放首歌", "音量",
    "关机", "重启电脑", "锁屏",
]

#: 确认短语（仅在存在待确认项时有效，由 Pipeline 判定）
CONFIRM_PHRASES: List[str] = [
    "确定", "确认", "是的", "对的", "没错", "可以", "行吧", "好吧",
    "好的", "好", "嗯", "继续", "删吧", "做吧", "执行吧", "同意", "批准",
]

#: 取消短语
CANCEL_PHRASES: List[str] = [
    "算了", "不用了", "不用", "取消", "不要了", "不要", "别删", "不删",
    "不做了", "不执行", "停下", "停止", "否", "不对",
]

#: 单字确认/取消语旁边允许出现的**语气助词**（P4-B5）
#:
#: 只收真正的语气词/话语标记，绝不能把「是 / 对 / 行 / 不」这类**实义字**放进来 ——
#: 放进来会让「你是否在听」重新变成 cancel、或让否定句变成确认。
SINGLE_CHAR_FILLERS = set("呀啊哦呃吧啦呢嘛哈哟噢唉诶那的咯喽")


def _strip_fillers(text: str) -> str:
    """去掉标点与空白，只留字（用于单字短语的"整句成立"判定）

    只在 `_matches_any` 里被调用，而它已经在入口挡掉了空文本 ——
    所以这里不做 `or ""` 兜底：那个分支永远走不到，留着就是一行不可达代码。
    """
    return re.sub(r"[^\w]", "", str(text))


#: 多步标记（计数式）：用户明确告知"这件事分几步做"
#: 只收「分N步」形态 —— 裸的「两步」「三步」太容易出现在无关说法里（如"隔两步"）
PLAN_COUNT_MARKERS: List[str] = [
    "分两步", "分三步", "分四步", "分五步", "分几步", "分多步", "分步",
]

#: 「分 3 步」「分三步」通吃（阿拉伯数字与中文数字）
PLAN_COUNT_RE = re.compile(r"分\s*[0-9一二三四五六七八九十两]\s*步")

#: 多步标记（连接式）：连接词把两个动作子句串起来
#:
#: 单独的「再」风险最高（"再看看" / "再找找" 都是单步），
#: 所以连接式标记一律附带两个附加条件才成立：
#:   ① 连接词两侧各有 ≥2 字实质内容（挡掉"最后找一下合同"这类句尾语气词）
#:   ② 文本命中 ≥2 个**不同**意图（挡掉"先看看电脑状态"——虽然「看看」和
#:      「电脑状态」各自命中 file_read 与 system_info，但它只有一个真实动作）
PLAN_CONNECTORS: List[str] = [
    "然后", "接着", "之后", "完了再", "最后再", "最后", "再", "并且", "同时",
]

#: 连接词两侧各自所需的最少字符数
PLAN_CONNECTOR_MIN_SIDE = 2


# ══════════════════════════════════════════════════════
#  意图表
# ══════════════════════════════════════════════════════

DEFAULT_INTENTS: List[IntentDef] = [
    IntentDef(
        action="plan",
        description="多步任务：一句话里包含多个先后动作（如「先找到合同再挪到归档」）",
        category="default",
        base_confidence=0.55,
        # 故意不给关键词：本意图**不参与关键词打分**。
        # 打分制会让它跟单步意图抢分（「挪到」权重 2.5 会赢过任何多步标记），
        # 于是改由 route() 里的专用预检 _route_multi_step() 判定。
        # 留在这里的目的只有一个：让 describe_intents() / supported_actions()
        # 把 plan 这个动作**告知 LLM 路由**，使它也能产出多步意图。
        keywords=[],
    ),
    IntentDef(
        action="file_search",
        description="在指定目录搜索文件，支持名称模式与时间范围",
        category="search",
        base_confidence=0.60,
        keywords=[
            ("找一下", 2.2), ("找找", 2.2), ("搜一下", 2.2), ("搜索", 2.0),
            ("查找", 2.0), ("找", 1.8), ("搜", 1.8),
            ("哪里有", 1.6), ("在哪", 1.6), ("有没有", 1.4),
        ],
    ),
    IntentDef(
        action="file_list",
        description="列出目录下的文件",
        category="search",
        base_confidence=0.60,
        keywords=[
            ("有什么", 2.2), ("有啥", 2.0), ("列出", 2.0), ("显示", 1.6),
            ("目录里", 1.5), ("文件夹里", 1.5), ("看一下", 1.2),
        ],
    ),
    IntentDef(
        action="file_read",
        description="打开或读取文件内容",
        category="read",
        base_confidence=0.60,
        keywords=[
            ("打开", 2.2), ("读一下", 2.2), ("读读", 2.0), ("查看", 1.8),
            ("内容是什么", 2.0), ("的内容", 1.6), ("看看", 1.5),
        ],
    ),
    IntentDef(
        action="file_rename",
        description="重命名文件",
        category="write",
        base_confidence=0.65,
        keywords=[
            ("重命名", 2.5), ("改名为", 2.5), ("改名成", 2.5), ("改名", 2.0),
            ("改成", 1.8), ("名字改", 2.0), ("换个名字", 1.8),
        ],
    ),
    IntentDef(
        action="file_move",
        description="移动文件到其他目录",
        category="write",
        base_confidence=0.65,
        keywords=[
            ("移到", 2.5), ("移动", 2.2), ("挪到", 2.5), ("挪", 2.0),
            ("放到", 2.2), ("挪个位置", 2.5),
        ],
    ),
    IntentDef(
        action="file_delete",
        description="删除文件（移入回收站，可恢复）",
        category="delete",
        base_confidence=0.70,
        keywords=[
            ("删除", 2.5), ("删掉", 2.5), ("清理", 2.2), ("清空", 2.2),
            ("不要了", 2.0), ("删了", 2.2), ("删", 1.8),
        ],
    ),
    IntentDef(
        action="system_info",
        description="查询系统状态（电量/内存/CPU/磁盘）",
        category="system",
        base_confidence=0.65,
        keywords=[
            ("内存", 2.5), ("cpu", 2.5), ("电量", 2.5), ("电池", 2.5),
            ("磁盘", 2.5), ("硬盘", 2.5), ("系统状态", 2.5), ("处理器", 2.2),
            # 口语化说法（"看看电脑状态" 曾因 file_read 的「看看」误判为读文件）
            ("电脑状态", 3.0), ("电脑怎么样", 3.0), ("电脑咋样", 3.0),
            ("电脑卡", 2.8), ("电脑还行", 2.8), ("电脑运行", 2.8),
            ("多少电", 2.6), ("机器状态", 2.5),
        ],
    ),
    IntentDef(
        action="clipboard",
        description="读写剪贴板",
        category="write",
        base_confidence=0.65,
        keywords=[("复制", 2.5), ("粘贴", 2.5), ("剪贴板", 2.5)],
    ),
    IntentDef(
        action="translate",
        description="翻译文本",
        category="default",
        base_confidence=0.70,
        keywords=[
            ("翻译", 2.5), ("用英文说", 2.2), ("英文怎么说", 2.2),
            ("日语怎么说", 2.2), ("译成", 2.2),
        ],
    ),
    IntentDef(
        action="calculate",
        description="数学计算",
        category="default",
        base_confidence=0.65,
        keywords=[
            ("算一下", 2.5), ("算算", 2.2), ("计算", 2.2),
            ("等于多少", 2.0), ("乘以", 2.0), ("加上", 2.0), ("减去", 2.0),
        ],
    ),
    # ── 系统工具 ──
    IntentDef(
        action="open_app",
        description="用默认程序打开文件、文件夹或应用",
        category="default",
        base_confidence=0.62,
        keywords=[
            ("打开记事本", 3.0), ("打开计算器", 3.0), ("打开画图", 3.0),
            ("打开浏览器", 3.0), ("打开任务管理器", 3.0), ("打开资源管理器", 3.0),
            ("打开命令提示符", 3.0), ("启动", 2.0),
        ],
    ),
    IntentDef(
        action="screenshot",
        description="截取屏幕",
        category="system",
        base_confidence=0.75,
        keywords=[("截图", 2.8), ("截屏", 2.8), ("屏幕截图", 3.0), ("截个图", 2.8)],
    ),
    IntentDef(
        action="run_command",
        description="执行系统命令",
        category="system",
        base_confidence=0.70,
        keywords=[
            ("执行命令", 3.0), ("运行命令", 3.0), ("执行一下", 2.0),
            ("cmd里", 2.5), ("powershell", 2.5), ("命令行", 2.2),
        ],
    ),
    # ── 生产力工具 ──
    IntentDef(
        action="reminder",
        description="设置提醒",
        category="default",
        base_confidence=0.70,
        keywords=[
            ("提醒我", 3.0), ("提醒一下", 3.0), ("分钟后", 2.2),
            ("小时后", 2.2), ("定个闹钟", 3.0), ("别忘了", 2.0),
        ],
    ),
    IntentDef(
        action="weather",
        description="查询天气",
        category="default",
        base_confidence=0.72,
        keywords=[
            ("天气", 2.8), ("气温", 2.5), ("下雨", 2.2), ("冷不冷", 2.2),
            ("热不热", 2.2), ("要不要带伞", 2.5),
        ],
    ),
    # ── 浏览器工具 ──
    IntentDef(
        action="web_read",
        description="读取网页正文",
        category="read",
        base_confidence=0.68,
        keywords=[
            ("这个网页", 2.5), ("网页里说", 2.8), ("读一下网页", 3.0),
            ("页面内容", 2.5), ("这篇文章", 2.2),
        ],
    ),
    IntentDef(
        action="web_open",
        description="用默认浏览器打开网址或网站",
        category="default",
        base_confidence=0.68,
        keywords=[
            ("打开网页", 2.8), ("打开网站", 2.8), ("访问", 2.2),
            ("打开百度", 3.0), ("打开知乎", 3.0), ("打开github", 3.0),
            ("打开b站", 3.0), ("打开淘宝", 3.0), ("打开京东", 3.0),
        ],
    ),
    IntentDef(
        action="web_search",
        description="搜索引擎查询",
        category="search",
        base_confidence=0.68,
        keywords=[
            ("百度一下", 3.0), ("网上搜", 2.8), ("搜一下网上", 3.0),
            ("帮我查一下", 2.2), ("谷歌一下", 3.0), ("上网查", 2.5),
        ],
    ),
]


# ══════════════════════════════════════════════════════
#  规则路由实现
# ══════════════════════════════════════════════════════

class RuleRouter(RouterService):
    """规则路由：关键词 + 正则，延迟 <10ms

    用法：
        router = RuleRouter()
        cmd = router.route("找一下桌面上的合同文件")
        # AgentCommand(action="file_search",
        #              params={"pattern": "*合同*", "dirs": ["Desktop"]},
        #              confidence=0.96)
    """

    capability_name = "router"
    provider_name = "rule"

    #: 各 action 的声明参数（用于置信度的参数完整度加成）
    DECLARED_PARAMS: Dict[str, List[str]] = {
        "plan": [],                 # 目标就是原句本身，无需从文本里再抽参数
        "file_search": ["pattern"],
        "file_list": ["dirs"],
        "file_read": ["target"],
        "file_rename": ["source", "target"],
        "file_move": ["dest"],
        "file_delete": [],          # 目标通常来自实体追踪
        "system_info": [],
        "clipboard": [],
        "translate": [],
        "calculate": ["expression"],
        "open_app": ["target"],
        "screenshot": [],
        "run_command": ["command"],
        "reminder": ["what"],
        "weather": [],
        "web_open": ["url"],
        "web_search": ["query"],
        "web_read": ["url"],
    }

    def __init__(
        self,
        intents: Optional[List[IntentDef]] = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        """
        Args:
            intents: 意图表；None 使用 DEFAULT_INTENTS
            confidence_threshold: 低置信度阈值（仅记录日志用，分流由 HybridRouter 负责）
        """
        self._intents = intents if intents is not None else DEFAULT_INTENTS
        self._threshold = confidence_threshold
        self._action_map = {i.action: i for i in self._intents}

    # ══════════════════════════════════════════════
    #  主入口
    # ══════════════════════════════════════════════

    def route(self, text: str, context: Dict[str, Any] = None) -> AgentCommand:
        """把文本路由为 AgentCommand

        Args:
            text: 用户输入
            context: 上下文；支持 {"has_pending_confirm": bool, "entities": EntityTracker}

        Returns:
            AgentCommand；无法识别时 action="chat"
        """
        context = context or {}

        # ── 0. 空文本 ──
        if not text or not str(text).strip():
            return self._chat_command(text, 0.0)

        norm = self._normalize(text)

        # ── 1. 确认/取消（仅在存在待确认项时生效，否则视为闲聊）──
        if context.get("has_pending_confirm"):
            if self._matches_any(norm, CANCEL_PHRASES):
                return AgentCommand(
                    action="cancel",
                    params={},
                    raw_text=text,
                    confidence=0.95,
                    source=context.get("source", "voice"),
                )
            if self._matches_any(norm, CONFIRM_PHRASES, strict=True):
                return AgentCommand(
                    action="confirm",
                    params={},
                    raw_text=text,
                    confidence=0.95,
                    source=context.get("source", "voice"),
                )

        # ── 2. 未实现能力 → 闲聊 ──
        if any(h in norm for h in UNSUPPORTED_HINTS):
            return self._chat_command(text, 0.15)

        # ── 2.4 显式多步 → plan（交给 Planner 拆步骤）──
        plan_cmd = self._route_multi_step(norm, text, context)
        if plan_cmd is not None:
            return plan_cmd

        # ── 2.5 含 URL 时按动词偏好直接分流（比关键词打分更可靠）──
        url_cmd = self._route_by_url(norm, text, context)
        if url_cmd is not None:
            return url_cmd

        # ── 3. 意图评分 ──
        scores: Dict[str, float] = {}
        for intent in self._intents:
            score = intent.keyword_weight(norm)
            if score > 0:
                scores[intent.action] = score

        if not scores:
            return self._chat_command(text, 0.10)

        best_action = max(scores, key=lambda a: scores[a])
        best_score = scores[best_action]
        intent = self._action_map[best_action]

        # ── 4. 参数提取 ──
        try:
            params = self._extract_params(best_action, norm, text, context)
        except Exception as e:
            logger.warning("[router] 参数提取失败 (%s): %s", best_action, e)
            params = {}

        # ── 5. 置信度 ──
        confidence = self._compute_confidence(intent, best_score, params, norm)

        cmd = AgentCommand(
            action=best_action,
            params=params,
            raw_text=text,
            confidence=confidence,
            source=context.get("source", "voice"),
        )
        logger.debug("[router] %r → %s", text[:30], cmd)
        return cmd

    # ══════════════════════════════════════════════
    #  内部：多步预检（P3 / D5）
    # ══════════════════════════════════════════════

    def _route_multi_step(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Optional[AgentCommand]:
        """文本是否明确要求"分几步做"→ action="plan"

        判定分两档，动机是**宁可漏判也不误判**（误判会把单步指令拖进多步通道，
        多花一次规划开销，且用户拿到的确认问句会变得奇怪）：

        1. **计数式**（"分三步…"）：用户已明说分步，**不要求**命中任何意图 ——
           "整理下载目录"这类说法里的动词不在规则词表内（规则路由只认具体动作），
           但 LLM 规划器认得。此时提前判死会把机会掐掉，而最坏结果也不过是
           规划器也拆不出来 → 管线退回闲聊，与不判 plan 的结局一致。
        2. **连接式**（"…然后…"）：要求命中 ≥2 个**不同**意图，
           且连接词两侧各有实质内容

        Returns:
            action="plan" 的命令；不构成多步时返回 None
        """
        if not norm:
            return None

        counted = bool(PLAN_COUNT_RE.search(norm)) or any(
            m in norm for m in PLAN_COUNT_MARKERS
        )
        if not counted:
            hits = self._distinct_intent_hits(norm)
            if len(hits) < 2 or not self._has_plan_connector(norm):
                return None
        else:
            hits = self._distinct_intent_hits(norm)

        params = self._extract_params("plan", norm, raw, context)
        logger.debug("[router] 多步预检命中 (%s): %s",
                     "计数式" if counted else "连接式", hits[:3])
        return AgentCommand(
            action="plan",
            params=params,
            raw_text=raw,
            confidence=0.86 if counted else 0.78,
            source=context.get("source", "voice"),
        )

    def _distinct_intent_hits(self, norm: str) -> List[str]:
        """文本命中的不同意图，按关键词得分降序

        `plan` 自身没有关键词，天然被排除在本结果之外 —— 这正是我们想要的：
        判断"是不是多步"时不能让多步意图给自己投票。
        """
        scored = [(i.action, i.keyword_weight(norm)) for i in self._intents]
        hit = [(a, s) for a, s in scored if s > 0]
        hit.sort(key=lambda x: -x[1])
        return [a for a, _ in hit]

    @staticmethod
    def _has_plan_connector(norm: str) -> bool:
        """是否存在"两侧都有实质内容"的连接词

        "最后找一下合同" → 连接词左侧为空 → 不算多步（只是一句带语气词的指令）
        "找一下合同然后再删掉" → 两侧都有内容 → 算多步
        """
        for conn in PLAN_CONNECTORS:
            start = 0
            while True:
                idx = norm.find(conn, start)
                if idx < 0:
                    break
                left = norm[:idx].strip()
                right = norm[idx + len(conn):].strip()
                if (len(left) >= PLAN_CONNECTOR_MIN_SIDE
                        and len(right) >= PLAN_CONNECTOR_MIN_SIDE):
                    return True
                start = idx + 1
        return False

    def _route_by_url(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Optional[AgentCommand]:
        """文本含 URL 时按动词偏好分流到 web_open / web_read

        "打开 X.com" / "访问 http://…"  → web_open
        "读一下 http://… 的内容"        → web_read

        Returns:
            AgentCommand；文本不含 URL 时返回 None
        """
        m = self._URL_RE.search(raw)
        if not m:
            return None

        url = m.group(1).strip().rstrip("。！？，,.")
        if not url:
            return None

        open_words = ("打开", "访问", "去", "上")
        read_words = ("读", "看", "内容", "说了什么", "讲了什么", "里面说")

        has_open = any(w in norm for w in open_words)
        has_read = any(w in norm for w in read_words)

        # 只读不打开
        if has_read and not has_open:
            return AgentCommand(
                action="web_read",
                params={"url": url},
                raw_text=raw,
                confidence=0.9,
                source=context.get("source", "voice"),
            )

        # 打开（默认行为）
        return AgentCommand(
            action="web_open",
            params={"url": url},
            raw_text=raw,
            confidence=0.9,
            source=context.get("source", "voice"),
        )

    def supported_actions(self) -> List[str]:
        """支持的 action 列表"""
        return [i.action for i in self._intents] + ["confirm", "cancel", "chat"]

    def describe_intents(self) -> List[Dict[str, Any]]:
        """导出意图描述（供 LLM 路由复用词表）"""
        return [
            {
                "action": i.action,
                "description": i.description,
                "category": i.category,
                "keywords": [w for w, _ in i.keywords],
            }
            for i in self._intents
        ]

    # ══════════════════════════════════════════════
    #  内部：基础工具
    # ══════════════════════════════════════════════

    @staticmethod
    def _normalize(text: str) -> str:
        """归一化：繁→简、转小写、合并空白

        **繁简归一是必须的，不是可选的润色**：ASR 对中文经常输出繁体
        （实测「删除桌面上的截图」→「删除桌面上的**截圖**。」），
        而本模块的词表全是简体。不归一化时会发生最危险的那类失败 ——
        意图匹配上了、文件类型关键词**静默丢失**，置信度仍是 0.95 没被拦下，
        管线便用实体栈兜底补目标，于是"删除截图"变成"删除上一次搜索到的文件"。

        归一化只作用于**匹配文本**（本方法的返回值用于查词表与提取参数）；
        `AgentCommand.raw_text` 保存的始终是用户原话，播报与气泡不受影响。
        详见 `agent/text_norm.py`（含"为什么是词级而非字级"与取舍说明）。
        """
        return re.sub(r"\s+", " ", to_simplified(text)).strip().lower()

    @staticmethod
    def _matches_any(text: str, phrases: List[str], strict: bool = False) -> bool:
        """文本是否命中任一短语

        ## 为什么确认方向与取消方向的宽严**必须不同**（P4-B5）

        四个方向的代价完全不对称：

        | 出错方向 | 代价 |
        |---------|------|
        | 误判成 **confirm** | **直接放过待确认的删除** —— 用户没批准的事被执行（R1，最高风险） |
        | 误判成 cancel | 待确认项被撤掉，用户再说一遍即可（无损） |
        | 漏判 confirm | 待确认项挂着，用户再说一遍（无损） |
        | 漏判 cancel | 待确认项挂着，用户以为取消了（B5 的原始抱怨，非破坏性） |

        所以：**确认从严（`strict=True`）、取消从宽（默认）**。

        ## 严格档（confirm）

        剥掉标点后，短语命中且**剩余字符只能是语气助词**；
        或者整句由"单字应答 + 语气助词"构成（含重复，如「嗯嗯」）。

        ```
        「确定」「好的」「嗯嗯，好的」「好呀」「嗯好」「可以的」   → 命中
        「你好呀」「我很好」「好奇怪」「好久不见」「这不太好吧」   → 不命中
        ```

        ## 宽松档（cancel，默认）

        多字短语子串命中（保住「我不要了」这种真实说法）；
        **单字短语（如「否」）仍要求整句成立** —— 否则「你是否在听」会变成 cancel。

        单字之所以在任何档位都不能做子串匹配：`CONFIRM_PHRASES` 里的「好」
        会让「你好呀 / 我很好 / 好奇怪 / 好久不见」全部变成 `confirm`
        （真实 `RuleRouter` 实测 5 条错判，是本轮修掉的危险缺陷）。
        """
        stripped = _strip_fillers(text)
        singles = {p for p in phrases if len(p) == 1}
        allowed = singles | SINGLE_CHAR_FILLERS

        if strict:
            # 短语命中，且**剩余字符只能是语气助词或单字应答**
            # （剩余集里允许别的单字应答，是为了保住「好的，删吧」这种复述式确认；
            #   它仍挡得住「这不太好吧」—— 那里的剩余是「这不太」，全不在允许集内）
            for p in sorted(phrases, key=len, reverse=True):
                if p in stripped and set(stripped.replace(p, "", 1)) <= allowed:
                    return True
        elif any(p in stripped for p in phrases if len(p) > 1):
            # 放宽档（取消方向）：多字短语子串命中即可
            return True

        # 单字（两档都）要求整句成立
        if singles and stripped and set(stripped) <= allowed:
            return any(p in stripped for p in singles)
        return False

    def _chat_command(self, text: str, confidence: float = 0.1) -> AgentCommand:
        """构造闲聊命令"""
        return AgentCommand(
            action="chat",
            params={},
            raw_text=text or "",
            confidence=confidence,
            source="rule",
        )

    # ══════════════════════════════════════════════
    #  内部：参数提取
    # ══════════════════════════════════════════════

    def _extract_params(
        self,
        action: str,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """按 action 分派到具体提取器"""
        extractor = {
            "plan": self._extract_plan_params,
            "file_search": self._extract_search_params,
            "file_list": self._extract_list_params,
            "file_read": self._extract_read_params,
            "file_rename": self._extract_rename_params,
            "file_move": self._extract_move_params,
            "file_delete": self._extract_delete_params,
            "translate": self._extract_translate_params,
            "calculate": self._extract_calculate_params,
            "system_info": self._extract_system_params,
            "open_app": self._extract_open_app_params,
            "screenshot": self._extract_screenshot_params,
            "run_command": self._extract_run_command_params,
            "reminder": self._extract_reminder_params,
            "weather": self._extract_weather_params,
            "web_open": self._extract_web_open_params,
            "web_search": self._extract_web_search_params,
            "web_read": self._extract_web_read_params,
        }.get(action)

        if extractor is None:
            return {}
        return extractor(norm, raw, context)

    def _extract_plan_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """多步指令的参数：只需要原句

        拆步骤是 Planner 的职责，路由不预判"该拆成什么"——
        把原句原样交出去，避免路由层与规划层各自维护一套动作词表。
        """
        goal = str(raw or "").strip()
        return {"goal": goal} if goal else {}

    def _extract_dirs(self, norm: str) -> List[str]:
        """提取目录别名"""
        dirs: List[str] = []
        for alias, name in DIR_ALIASES.items():
            if alias in norm and name not in dirs:
                dirs.append(name)
        return dirs

    def _extract_time_range(self, norm: str) -> Optional[str]:
        """提取时间限定（长词优先，避免"今天"吃掉"前两天"）"""
        for alias in sorted(TIME_ALIASES.keys(), key=len, reverse=True):
            if alias in norm:
                return TIME_ALIASES[alias]
        return None

    def _strip_noise(self, s: str) -> str:
        """去除噪声词与标点（激进：用于搜索模式提取）"""
        for w in sorted(NOISE_WORDS, key=len, reverse=True):
            s = s.replace(w, "")
        return s.strip("，。！？、,.!?~～ \t")

    #: 轻量噪声词（结构助词，不含"的"等可能属于文件名本身的字）
    LIGHT_NOISE: List[str] = [
        "帮我", "给我", "麻烦", "请你", "请",
        "一下", "一个", "那个", "这个",
    ]

    def _strip_light_noise(self, s: str) -> str:
        """轻量去噪（用于重命名/移动的源与目标名，避免损伤"新的"这类合法名称）"""
        for w in sorted(self.LIGHT_NOISE, key=len, reverse=True):
            s = s.replace(w, "")
        return s.strip("，。！？、,.!?~～ \t")

    def _extract_extension_pattern(self, norm: str) -> Optional[str]:
        """从文件类型别名提取 glob 模式（长别名优先）"""
        for alias in sorted(EXT_ALIASES.keys(), key=len, reverse=True):
            if alias in norm:
                return EXT_ALIASES[alias]
        return None

    # ── file_search ──

    def _extract_search_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}

        pattern = self._extract_extension_pattern(norm)
        if not pattern:
            candidate = self._strip_noise(norm)
            pattern = f"*{candidate}*" if candidate else "*"
        params["pattern"] = pattern

        dirs = self._extract_dirs(norm)
        if dirs:
            params["dirs"] = dirs

        time_range = self._extract_time_range(norm)
        if time_range:
            params["time_range"] = time_range

        return params

    # ── file_list ──

    def _extract_list_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """提取 `file_list` 的目录参数。

        ⚠️ **原来这里是 `return {"dirs": dirs or ["Desktop"]}`** ——
        没说目录就默认 `Desktop`。这造成两个后果：

        1. **闲聊被当成文件操作**：`_compute_confidence` 看到 `dirs` 已填，
           就按"参数完整"加分，于是
           `今天有什么好吃的` -> `file_list(dirs=['Desktop'])` conf=0.93。
           用户从没让它操作任何目录，却收到
           "哎呀，刚才那个「Desktop」我没太听明白呢"。
        2. **用户意图被静默替换**：他若说"列出文件"，本意是"就现在这个上下文"，
           却被替换成"列出桌面" —— 这是**猜**，不是**听**。

        正确做法：**没说就不填**。让上层按"缺目标"降置信度、回落闲聊；
        真需要默认目录时，由工具层在**用户确实表达了文件意图**之后兜底
        （那时默认才有依据）。判据：**默认值只能补"已确认的意图"，
        不能制造意图**。
        """
        dirs = self._extract_dirs(norm)
        return {"dirs": dirs} if dirs else {}

    # ── file_read ──

    _ORDINAL_RE = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*个")
    _FILENAME_RE = re.compile(
        r"([\w\u4e00-\u9fa5\-.]+\.(?:txt|pdf|docx?|xlsx?|pptx?|png|jpe?g|gif|bmp|mp4|mp3|zip|rar|py|md|json|yaml|yml|csv))",
        re.IGNORECASE,
    )

    def _extract_read_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}

        # 1. 序数指代 → 原样传给实体追踪器
        m = self._ORDINAL_RE.search(raw)
        if m:
            params["target"] = m.group(0)
            return params

        # 2. 明确文件名：先剥掉动词前缀，避免 "打开报告.docx" 匹配成 "打开报告.docx"
        stripped = re.sub(
            r"^\s*(?:帮我|给我|请|麻烦)?\s*(?:打开|查看|读一下|读读|看看)\s*",
            "",
            raw,
        ).strip()
        m = self._FILENAME_RE.search(stripped)
        if m:
            params["target"] = m.group(1)
            return params

        # 3. "打开X" 中的 X
        m = re.search(r"(?:打开|查看|读一下|读读|看看)\s*(.+?)(?:的内容|内容|文件)?$", norm)
        if m:
            candidate = self._strip_noise(m.group(1))
            if candidate:
                params["target"] = candidate

        return params

    # ── file_rename ──

    _RENAME_FULL_RE = re.compile(
        r"(?:把|将)?\s*(.+?)\s*(?:重命名为|改名为|改名成|改成|改叫|命名为|换个名字叫)\s*(.+?)$"
    )
    _RENAME_TARGET_RE = re.compile(
        r"(?:重命名为|改名为|改名成|改成|改叫|命名为)\s*(.+?)$"
    )

    def _extract_rename_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}

        m = self._RENAME_FULL_RE.search(norm)
        if m:
            source = self._strip_light_noise(m.group(1))
            target = self._strip_light_noise(m.group(2))
            if source:
                params["source"] = source
            if target:
                params["target"] = target
            if params:
                return params

        m = self._RENAME_TARGET_RE.search(norm)
        if m:
            target = self._strip_light_noise(m.group(1))
            if target:
                params["target"] = target

        return params

    # ── file_move ──

    def _extract_move_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}

        dirs = self._extract_dirs(norm)
        if dirs:
            params["dest"] = dirs[0]

        m = re.search(r"(?:把|将)?\s*(.+?)\s*(?:移到|移动到|挪到|放到)\s*(.+?)$", norm)
        if m:
            source = self._strip_noise(m.group(1))
            if source:
                params["source"] = source
            dest_text = self._strip_noise(m.group(2))
            if dest_text and "dest" not in params:
                params["dest"] = dest_text

        return params

    # ── file_delete ──

    def _extract_delete_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}

        # 0. 文本中直接给了绝对路径 → 作为显式目标（交由安全守卫校验）
        abs_path = self._extract_abs_path(raw)
        if abs_path:
            params["targets"] = [abs_path]
            return params

        dirs = self._extract_dirs(norm)
        if dirs:
            params["dirs"] = dirs

        pattern = self._extract_extension_pattern(norm)
        if pattern and pattern != "*":
            params["pattern"] = pattern

        # "把X删了" / "删掉X" 中的显式目标
        m = re.search(r"(?:把|将)\s*(.+?)\s*(?:删掉|删除|删了|清理掉)", norm)
        if m:
            target = self._strip_noise(m.group(1))
            if target:
                params["target"] = target

        return params

    # ── translate / calculate / system_info ──

    def _extract_translate_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if "英文" in norm or "english" in norm:
            params["target_lang"] = "en"
        elif "日文" in norm or "日语" in norm:
            params["target_lang"] = "ja"
        elif "中文" in norm:
            params["target_lang"] = "zh"

        m = re.search(r"(?:翻译|译成|译)\s*(?:一下)?\s*(.+?)$", norm)
        if m:
            text = m.group(1)
            # 先剥掉指示代词，避免把"这段话"里的"话"当成待翻译内容
            text = re.sub(r"^(这段|这句话|这句|这|下面这段|上面这段)\s*", "", text)
            text = self._strip_light_noise(text)
            # 纯占位词（用户只是指代，没给具体内容）→ 不产出 text 参数
            if text in ("话", "文字", "内容", "东西", "段话"):
                text = ""
            if text:
                params["text"] = text
        return params

    _MATH_RE = re.compile(r"[\d\.\+\-\*/\(\)\s]+")

    #: 绝对路径模式（Windows 盘符形式 或 POSIX 形式）
    _ABS_PATH_RE = re.compile(
        r"(?:[A-Za-z]:[\\/][^\s，。！？,;；\"'）\)]+|/[^\s，。！？,;；\"'）\)]+)"
    )

    def _extract_abs_path(self, raw: str) -> Optional[str]:
        """从文本中提取绝对路径（供删除/读取等直接定位文件）"""
        m = self._ABS_PATH_RE.search(raw)
        if not m:
            return None
        path = m.group(0).strip().rstrip("。！？，,.;；")
        return path or None

    def _extract_calculate_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        # 先把中文运算符替换为符号，再提取表达式，避免"25 乘 4"被截断为"25"
        prepared = (
            raw.replace("乘以", "*").replace("乘", "*")
            .replace("加上", "+").replace("加", "+")
            .replace("减去", "-").replace("减", "-")
            .replace("除以", "/").replace("除", "/")
            .replace("×", "*").replace("÷", "/")
        )
        m = self._MATH_RE.search(prepared)
        if not m:
            return {}
        expr = m.group(0).strip()
        return {"expression": expr} if expr else {}

    def _extract_system_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        metric_map = {
            "内存": "memory", "cpu": "cpu", "处理器": "cpu",
            "电量": "battery", "电池": "battery",
            "磁盘": "disk", "硬盘": "disk",
        }
        for alias, metric in metric_map.items():
            if alias in norm:
                return {"metric": metric}
        return {"metric": "all"}

    # ── 系统工具 ──

    #: 已知应用名（与 OpenAppTool.KNOWN_APPS 对齐）
    KNOWN_APP_NAMES = (
        "记事本", "计算器", "画图", "浏览器",
        "任务管理器", "资源管理器", "命令提示符",
    )

    def _extract_open_app_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        for name in self.KNOWN_APP_NAMES:
            if name in norm:
                return {"target": name}
        m = re.search(r"(?:打开|启动)\s*(.+?)$", norm)
        if m:
            target = self._strip_light_noise(m.group(1))
            if target:
                return {"target": target}
        return {}

    def _extract_screenshot_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        m = re.search(r"(?:存成|命名为|叫)\s*(.+?)$", norm)
        if m:
            name = self._strip_light_noise(m.group(1))
            if name:
                return {"filename": name}
        return {}

    _COMMAND_HINT_RE = re.compile(
        r"(?:执行命令|运行命令|执行一下|命令行里|cmd里|powershell里)\s*[：:]?\s*(.+?)$"
    )

    def _extract_run_command_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        # 命令内容可能被各种引号/反引号包裹，统一剥离
        wrap = "`「」\"'“”‘’《》"
        m = self._COMMAND_HINT_RE.search(norm)
        if m:
            cmd = m.group(1).strip().strip(wrap).strip()
            if cmd:
                return {"command": cmd}
        # 独立成段的引号包裹命令
        m = re.search(r"[`「\"'《]([^`」\"'》]{2,})[`」\"'》]", raw)
        if m:
            cmd = m.group(1).strip()
            if cmd:
                return {"command": cmd}
        return {}

    # ── 生产力工具 ──

    _DURATION_RE = re.compile(
        r"(\d+(?:\.\d+)?)\s*(个?小时|钟头|分钟|分|秒)\s*(?:后|之后|以后)?"
    )

    def _extract_reminder_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}

        m = self._DURATION_RE.search(raw)
        if m:
            value = float(m.group(1))
            unit = m.group(2)
            if "小时" in unit or "钟头" in unit:
                params["minutes"] = value * 60
            elif "秒" in unit:
                params["minutes"] = max(0.1, value / 60.0)
            else:
                params["minutes"] = value

        m = re.search(r"(?:提醒我|提醒一下我|别忘了提醒我|别忘了我)\s*(.+?)$", norm)
        if not m:
            m = re.search(r"(?:提醒我|提醒一下)\s*(.+?)$", norm)
        if m:
            what = self._strip_light_noise(m.group(1))
            what = self._DURATION_RE.sub("", what).strip("，,、 ")
            if what:
                params["what"] = what

        return params

    _CITY_RE = re.compile(r"([\u4e00-\u9fa5a-zA-Z]{2,8}?)(?:的)?(?:天气|气温)")

    #: 城市名前常见的口语前缀（需剥离）
    _CITY_NOISE = ("今天", "明天", "后天", "现在", "帮我", "给我", "查一下",
                   "看看", "看一下", "问问", "想知道", "了解")

    def _extract_weather_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        m = self._CITY_RE.search(norm)
        if not m:
            return {}
        city = m.group(1)
        for w in self._CITY_NOISE:
            city = city.replace(w, "")
        city = city.strip()
        return {"city": city} if city else {}

    # ── 浏览器工具 ──

    _URL_RE = re.compile(
        r"(https?://[^\s，。！？、\"'）\)]+"
        r"|(?:www\.)?[\w\-]+\.(?:com|cn|net|org|io|dev|edu|gov|top|xyz)"
        r"(?:/[^\s，。！？、\"'）\)]*)?)",
        re.IGNORECASE,
    )

    def _extract_web_open_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        m = self._URL_RE.search(raw)
        if m:
            return {"url": m.group(1).strip().rstrip("。！？，,.")}
        m = re.search(r"(?:打开|访问)\s*(.+?)$", norm)
        if m:
            target = self._strip_light_noise(m.group(1))
            if target:
                return {"url": target}
        return {}

    _SEARCH_HINT_RE = re.compile(
        r"(?:百度一下|谷歌一下|网上搜|上网查|帮我查一下|帮我搜一下|搜一下)\s*(.+?)$"
    )

    def _extract_web_search_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        m = self._SEARCH_HINT_RE.search(norm)
        if m:
            query = self._strip_light_noise(m.group(1))
            if query:
                return {"query": query}
        return {}

    def _extract_web_read_params(
        self,
        norm: str,
        raw: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        m = self._URL_RE.search(raw)
        if m:
            return {"url": m.group(1).strip().rstrip("。！？，,.")}
        return {}

    # ══════════════════════════════════════════════
    #  内部：置信度
    # ══════════════════════════════════════════════

    def _compute_confidence(
        self,
        intent: IntentDef,
        keyword_score: float,
        params: Dict[str, Any],
        norm: str,
    ) -> float:
        """置信度 = 基础分 + 关键词加成 + 参数完整度 + 句式加成

        ⚠️ **文件类动作缺少"目标"时必须显著降分**（根因修复）。

        缺陷实测：`file_list` 的关键词表里有 `"有什么"(2.2)`、`"看一下"(1.2)`
        这类**日常口语高频词**，于是纯闲聊被高置信度判成文件操作：

            今天有什么好吃的        -> file_list  conf=0.93
            你觉得我这个人有什么缺点  -> file_list  conf=0.93
            看一下我新买的鼠标       -> file_list  conf=0.85

        用户明明在聊天，却收到"哎呀，刚才那个「Desktop」我没太听明白呢"
        —— 他从没让它操作任何目录。根因是：**关键词命中就给了 0.93，
        但 `dirs` 根本没提取出来**（没有任何目录被提及），
        也就是说这个"文件操作"**没有目标**，不可能是真的文件操作。

        所以对"必须有目标"的动作，缺目标就是**强反证**，而不只是少加一点分。
        """
        conf = intent.base_confidence

        # 关键词得分（单关键词命中约 +0.16，上限 +0.25）
        conf += min(keyword_score * 0.08, 0.25)

        # 参数完整度
        declared = self.DECLARED_PARAMS.get(intent.action, [])
        if declared:
            filled = sum(1 for k in declared if params.get(k))
            conf += (filled / len(declared)) * 0.15

        # 句式完整（有礼貌前缀或"把"字句）
        if any(v in norm for v in ("帮我", "给我", "麻烦", "请", "把", "将")):
            conf += 0.05

        # ── 强反证：文件/系统类动作必须说得出"操作什么" ──
        #    什么都没说清 = 大概率是在闲聊（"今天有什么好吃的"）。
        #    降到一个明显低于规则阈值的水平，让它自然回落到 chat。
        #
        #    ⚠️ 注意"目标"对不同动作是不同的字段：
        #      file_list / file_search 看 `dirs`（列/搜哪儿）
        #      file_read / file_rename  看 `target` 或 `dirs`（读/改哪个）
        #      file_move / file_delete  看 `targets`（动哪些）
        #    第一版只查 `dirs`，于是 `打开桌面上的报告.txt` 明明有 `target`
        #    也被判成"没有目标"，置信度掉到 0.35 —— 那是**误降**。
        if intent.action in self._NEEDS_TARGET_ACTIONS:
            fields = self._NEEDS_TARGET_ACTIONS[intent.action]
            if not any(params.get(f) for f in fields):
                conf = min(conf - 0.45, 0.35)

        return round(min(conf, 0.99), 3)

    #: 这些动作**必须**说得出"操作什么"，否则不可能是真的指令。
    #:
    #: 值是该动作里"能代表目标"的字段集合 —— **任一**填了就算有目标。
    #: 为什么写死而不是自动推导：`DECLARED_PARAMS` 只说明"参数长什么样"，
    #: 不说明"缺了它是否还成立"，更不说明哪几个字段是**等价的目标表达**。
    #: 例如 `file_read` 可以给 `target`（"报告.txt"）也可以给 `dirs`（"桌面"），
    #: 两者任一都成立。这种语义判断写在这里比让程序猜更可靠，也便于评审。
    _NEEDS_TARGET_ACTIONS = {
        'file_list': ('dirs',),
        'file_search': ('dirs', 'pattern'),
        'file_read': ('target', 'dirs'),
        'file_rename': ('target', 'dirs'),
        'file_move': ('targets', 'source', 'dest'),
        'file_delete': ('targets', 'dirs', 'pattern'),
    }
