"""基础安全守卫（Service Provider）

R1「误操作损坏文件」的核心防护实现。

五层防护：
1. 路径白名单   —— 只允许操作桌面/文档/下载/图片
2. 路径规范化   —— 解析 .. 与符号链接，防逃逸
3. 三级风险     —— low 自动 / medium 确认 / high 双确认 / critical 拒绝
4. 操作预览     —— 执行前展示将影响的对象
5. 审计日志     —— 所有写操作留痕，可追溯

硬性约束（代码级，不可配置放宽）：
- 白名单外路径一律拒绝
- 删除操作强制走回收站（由工具层实现，本模块只做路径校验）
- UNC 路径、绝对系统路径一律拒绝
"""

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.seams.safety import PathNotAllowed, SafetyService, SafetyVerdict, SecurityError

logger = logging.getLogger(__name__)


# ── 默认白名单（四个常用目录）──
DEFAULT_WHITELIST_NAMES = ["Desktop", "Documents", "Downloads", "Pictures"]

# ── 默认风险规则表 ──
DEFAULT_RISK_RULES: Dict[str, List[str]] = {
    "low": [
        "file_search", "file_list", "file_read",
        "system_info", "clipboard_get", "translate", "calculate",
    ],
    "medium": [
        "file_write", "file_rename", "file_move", "clipboard_set", "open_app",
    ],
    "high": [
        "file_delete", "run_command", "screenshot",
    ],
    "critical": [
        "format_disk", "registry_edit", "permanent_delete",
    ],
}

# ── 风险等级 → 是否需确认 / 是否需双确认 ──
RISK_POLICY: Dict[str, Dict[str, bool]] = {
    "low": {"confirm": False, "double": False},
    "medium": {"confirm": True, "double": False},
    "high": {"confirm": True, "double": True},
    "critical": {"confirm": False, "double": False},  # 直接拒绝，不提供确认通道
}

# ── 确认超时（秒）──
DEFAULT_CONFIRM_TIMEOUT = 30.0

# ── 确认记忆有效期（秒）：同操作同目录在此期限内不重复询问 ──
DEFAULT_REMEMBER_TTL = 900.0  # 15 分钟

# ── 单次批量操作对象数量上限 ──
MAX_BATCH_SIZE = 200


@dataclass
class PendingConfirm:
    """待确认操作"""

    request_id: str
    action: str
    params: Dict[str, Any]
    question: str
    created_at: float = field(default_factory=time.time)
    timeout: float = DEFAULT_CONFIRM_TIMEOUT
    approved: Optional[bool] = None

    @property
    def expired(self) -> bool:
        """是否已超时"""
        return (time.time() - self.created_at) > self.timeout


class BasicGuard(SafetyService):
    """基础安全守卫

    用法：
        guard = BasicGuard()
        verdict = guard.check("file_delete", {"targets": ["~/Desktop/a.png"]})
        if verdict.rejected:
            return  # 拒绝
        if verdict.confirm_needed:
            question = guard.request_confirm(rid, "file_delete", params)
            # ... 等待用户响应 ...
            guard.resolve_confirm(rid, approved=True)
        # 执行时校验路径
        paths = guard.validate_paths(params["targets"])
    """

    capability_name = "safety"

    def __init__(
        self,
        whitelist: Optional[List[str]] = None,
        risk_rules: Optional[Dict[str, List[str]]] = None,
        audit_db: Optional[str] = None,
        confirm_timeout: float = DEFAULT_CONFIRM_TIMEOUT,
        remember_ttl: float = DEFAULT_REMEMBER_TTL,
        remember_choices: bool = True,
        audit_enabled: bool = True,
    ) -> None:
        """
        Args:
            whitelist: 白名单目录（支持 ~ 展开）；None 使用默认四目录
            risk_rules: 风险规则表；None 使用默认表
            audit_db: 审计数据库路径；None 使用内存库
            confirm_timeout: 确认超时秒数
            remember_ttl: 确认记忆有效期秒数
            remember_choices: 是否记住用户确认选择
            audit_enabled: 是否启用审计日志
        """
        self._roots: List[Path] = self._build_roots(whitelist)
        self._risk_rules: Dict[str, List[str]] = risk_rules or DEFAULT_RISK_RULES
        self._action_to_risk: Dict[str, str] = {}
        for risk, actions in self._risk_rules.items():
            for action in actions:
                self._action_to_risk[action] = risk

        self._pending: Dict[str, PendingConfirm] = {}
        self._confirmed: Dict[str, bool] = {}       # request_id → approved
        self._remembered: Dict[str, float] = {}     # "action|dir" → 到期时间戳
        self._confirm_timeout = confirm_timeout
        self._remember_ttl = remember_ttl
        self._remember_choices = remember_choices
        self._audit_enabled = audit_enabled

        self._db: Optional[sqlite3.Connection] = None
        if audit_enabled:
            self._init_audit_db(audit_db)

        logger.info(
            "[safety] BasicGuard 就绪：白名单 %d 个目录，风险规则 %d 条",
            len(self._roots), len(self._action_to_risk),
        )

    # ══════════════════════════════════════════════
    #  初始化辅助
    # ══════════════════════════════════════════════

    def _build_roots(self, whitelist: Optional[List[str]]) -> List[Path]:
        """构建并规范化白名单根目录"""
        names = whitelist if whitelist is not None else DEFAULT_WHITELIST_NAMES
        roots: List[Path] = []
        home = Path.home()

        for item in names:
            try:
                p = Path(item).expanduser()
                if not p.is_absolute():
                    p = home / item
                # 尽量解析；失败时用绝对路径兜底
                try:
                    resolved = p.resolve()
                except (OSError, RuntimeError):
                    resolved = Path(os.path.abspath(str(p)))
                roots.append(resolved)
            except Exception as e:
                logger.warning("[safety] 白名单目录无效，跳过: %s (%s)", item, e)

        return roots

    def _init_audit_db(self, db_path: Optional[str]) -> None:
        """初始化审计数据库"""
        try:
            target = db_path or ":memory:"
            self._db = sqlite3.connect(target, check_same_thread=False)
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS audit (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL    NOT NULL,
                    action    TEXT    NOT NULL,
                    params    TEXT,
                    success   INTEGER NOT NULL,
                    detail    TEXT
                )
                """
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts)")
            self._db.commit()
        except Exception as e:
            logger.error("[safety] 审计库初始化失败，降级为无审计: %s", e)
            self._db = None

    # ══════════════════════════════════════════════
    #  路径校验（R1 核心）
    # ══════════════════════════════════════════════

    @staticmethod
    def _norm_for_compare(s: str) -> str:
        """路径归一化：统一分隔符、去尾部分隔符、转小写（Windows 大小写不敏感）"""
        return s.replace("/", "\\").rstrip("\\").lower()

    def validate_path(self, path: str) -> Path:
        """校验路径并返回规范化绝对路径

        防护的攻击类型：
        - 目录穿越（../）
        - 符号链接逃逸
        - 绝对系统路径
        - UNC 路径
        - 大小写绕过
        - 8.3 短名

        Raises:
            PathNotAllowed: 路径不在白名单内或格式非法
        """
        if path is None or not str(path).strip():
            raise PathNotAllowed(str(path), self._roots)

        raw = str(path).strip().strip('"').strip("'")

        # 1. UNC 路径直接拒绝（\\server\share 形式）
        if raw.startswith("\\\\") or raw.startswith("//"):
            logger.warning("[safety] 拒绝 UNC 路径: %s", raw)
            raise PathNotAllowed(raw, self._roots)

        # 2. 空字节注入
        if "\x00" in raw:
            raise PathNotAllowed(raw, self._roots)

        # 3. 展开 ~
        try:
            p = Path(raw).expanduser()
        except (RuntimeError, ValueError) as e:
            logger.warning("[safety] 路径展开失败: %s (%s)", raw, e)
            raise PathNotAllowed(raw, self._roots)

        # 4. 规范化：解析 .. 、. 与符号链接
        #    Path.resolve() 在 Python 3.6+ 默认解析符号链接（strict=False）
        try:
            resolved = p.resolve()
        except (OSError, RuntimeError) as e:
            logger.warning("[safety] 路径解析失败（可能非法字符/过长）: %s (%s)", raw, e)
            raise PathNotAllowed(raw, self._roots)

        # 5. 白名单校验
        if not self.is_allowed_path(resolved):
            logger.warning("[safety] 拒绝白名单外路径: %s", resolved)
            raise PathNotAllowed(str(resolved), self._roots)

        return resolved

    def validate_paths(self, paths: List[str]) -> List[Path]:
        """批量校验路径（任一失败则整体失败）"""
        if not isinstance(paths, list):
            raise PathNotAllowed(str(paths), self._roots)
        if len(paths) > MAX_BATCH_SIZE:
            raise SecurityError(
                "batch_limit",
                f"一次最多处理 {MAX_BATCH_SIZE} 个文件，这次有 {len(paths)} 个，太多了呢",
            )
        return [self.validate_path(p) for p in paths]

    def is_allowed_path(self, path: Path) -> bool:
        """纯判断：路径是否在白名单内（不抛异常）"""
        try:
            target = self._norm_for_compare(str(path))
        except Exception:
            return False

        for root in self._roots:
            root_str = self._norm_for_compare(str(root))
            if target == root_str or target.startswith(root_str + "\\"):
                return True
        return False

    def whitelist_roots(self) -> List[Path]:
        """当前白名单根目录"""
        return list(self._roots)

    # ══════════════════════════════════════════════
    #  风险评估
    # ══════════════════════════════════════════════

    def risk_of(self, action: str) -> str:
        """查询操作的风险等级（未知操作按 medium 保守处理）"""
        return self._action_to_risk.get(action, "medium")

    def check(self, action: str, params: Dict[str, Any]) -> SafetyVerdict:
        """评估操作风险

        评估流程：
        1. 查风险等级
        2. critical → 直接拒绝
        3. 路径类操作 → 预校验路径（失败即拒绝）
        4. medium/high → 检查是否已确认 / 已记忆
        5. 生成操作预览
        """
        params = params or {}
        risk = self.risk_of(action)
        policy = RISK_POLICY.get(risk, RISK_POLICY["medium"])

        # ── critical：硬拒绝 ──
        if risk == "critical":
            return SafetyVerdict(
                allowed=False,
                risk=risk,
                confirm_needed=False,
                require_double=False,
                reason=f"「{action}」这类操作太危险了，我不能做哦",
            )

        # ── 路径预校验（涉及路径的操作）──
        path_error = self._precheck_paths(action, params)
        if path_error:
            return SafetyVerdict(
                allowed=False,
                risk=risk,
                confirm_needed=False,
                require_double=False,
                reason=path_error,
            )

        preview = self._build_preview(action, params, risk)

        # ── 是否需要确认 ──
        if not policy["confirm"]:
            return SafetyVerdict(allowed=True, risk=risk, preview=preview)

        # 已记忆（用户此前批准过同操作同目录）→ 免确认
        if self._remember_choices and self._is_remembered(action, params):
            return SafetyVerdict(allowed=True, risk=risk, preview=preview)

        return SafetyVerdict(
            allowed=False,
            risk=risk,
            confirm_needed=True,
            require_double=policy["double"],
            preview=preview,
        )

    @staticmethod
    def _looks_like_path(raw: str) -> bool:
        """判断取值是否"看起来是路径"（需要做白名单校验）

        安全模型：
        - 绝对路径 / 含分隔符 / 以 ~ 开头 → 必须通过白名单校验
        - 裸名字（"Desktop" / "b.txt"）→ 由工具在白名单目录内解析，天然安全

        这样既拦住 `../../etc/passwd` 这类穿越，又允许路由产出的人类说法
        （"桌面" → "Desktop"、"报告" → "*报告*"）。
        """
        s = str(raw).strip().strip('"').strip("'")
        if not s:
            return False
        if s.startswith("~"):
            return True
        if os.path.isabs(s):
            return True
        # 含路径分隔符（含 Windows 的两种分隔符）
        if "/" in s or "\\" in s:
            return True
        # Windows 盘符形式（如 C:foo）
        if len(s) >= 2 and s[1] == ":":
            return True
        # 口语盘符形式（"C盘" / "C盘Windows文件夹" / "C 盘 Windows"）
        #
        # 为什么必须认：语音说「打开 C 盘 Windows 文件夹」时 ASR 稳定输出这种写法，
        # 旧实现不认 → `file_read(target="c盘windows")` 被当成**裸名字**放行，
        # 于是白名单预校验根本没介入：既没有"不能动哦"的拒绝，也没有审计留痕
        # （语音回环验证实测"审计 0 条拒绝记录"）。结果虽然安全（工具只是"没找到"），
        # 但**安全边界形同不存在** —— R1 的防护前提是"路径操作前必过白名单"。
        #
        # 只认"不含点"的形式，避免把桌面上真实存在的文件名
        # （如 `C盘说明.txt`）误判成路径而拒掉：带扩展名的按原来的裸名字处理。
        compact = "".join(s.split())
        if (len(compact) >= 2 and compact[0].isascii() and compact[0].isalpha()
                and compact[1] == "盘" and "." not in compact):
            return True
        return False

    def _precheck_paths(self, action: str, params: Dict[str, Any]) -> str:
        """路径类操作的预校验；返回错误文案（空串=通过）

        只校验"看起来是路径"的取值；裸文件名/目录别名交给工具在白名单内解析。
        """
        candidates: List[str] = []

        # 常见路径参数字段（含搜索类工具的目录参数）
        for key in ("target", "source", "path", "dest", "dest_dir", "directory", "dir"):
            v = params.get(key)
            if isinstance(v, str) and v.strip():
                candidates.append(v)

        for key in ("targets", "paths", "files", "dirs"):
            v = params.get(key)
            if isinstance(v, list):
                candidates.extend([x for x in v if isinstance(x, str) and x.strip()])

        for raw in candidates:
            if not self._looks_like_path(raw):
                continue  # 裸名字：由工具在白名单目录内解析
            try:
                self.validate_path(raw)
            except PathNotAllowed as e:
                return str(e.reason)
            except SecurityError as e:
                return e.reason

        return ""

    def _build_preview(self, action: str, params: Dict[str, Any], risk: str) -> str:
        """生成操作预览文案"""
        targets: List[str] = []
        for key in ("targets", "paths", "files"):
            v = params.get(key)
            if isinstance(v, list):
                targets.extend([str(x) for x in v if x])
        for key in ("target", "source", "path"):
            v = params.get(key)
            if isinstance(v, str) and v.strip():
                targets.append(v)

        if not targets:
            return ""

        shown = targets[:3]
        names = "、".join(Path(t).name for t in shown)
        if len(targets) > 3:
            names += f" 等 {len(targets)} 个"

        if action == "file_delete":
            return f"要移到回收站的是：{names}"
        if action in ("file_rename", "file_move"):
            return f"要处理的是：{names}"
        return f"涉及：{names}"

    # ══════════════════════════════════════════════
    #  确认状态管理
    # ══════════════════════════════════════════════

    def request_confirm(
        self,
        request_id: str,
        action: str,
        params: Dict[str, Any],
        preview: str = "",
    ) -> str:
        """登记待确认操作，返回给用户看的确认文案

        Args:
            preview: 调用方提供的操作预览（通常来自 tool.preview()）；
                     为空时回退到本地基于 params 的推断
        """
        self.expire_check()

        verdict = self.check(action, params)
        if not preview:
            preview = verdict.preview

        if action == "file_delete":
            question = f"{preview}。确定要移到回收站吗？" if preview else "确定要删除吗？"
        elif verdict.require_double:
            question = f"{preview}。这个操作比较重要，确定要继续吗？" if preview else "确定要继续吗？"
        else:
            question = f"{preview}。要我继续吗？" if preview else "要我继续吗？"

        self._pending[request_id] = PendingConfirm(
            request_id=request_id,
            action=action,
            params=params,
            question=question,
            timeout=self._confirm_timeout,
        )
        logger.info("[safety] 待确认 %s: %s", request_id, question)
        return question

    def resolve_confirm(self, request_id: str, approved: bool) -> bool:
        """处理用户确认响应

        Returns:
            True=找到并对该请求作出了决定；False=无此待确认项（可能已超时）
        """
        pending = self._pending.pop(request_id, None)
        if pending is None:
            logger.info("[safety] 确认响应无匹配待确认项: %s", request_id)
            return False

        if pending.expired:
            logger.info("[safety] 确认已超时: %s", request_id)
            return False

        self._confirmed[request_id] = bool(approved)

        if approved and self._remember_choices:
            self._remember(pending.action, pending.params)

        logger.info("[safety] %s → %s", request_id, "已批准" if approved else "已取消")
        return True

    def is_confirmed(self, request_id: str) -> bool:
        """该请求是否已获确认（或无需确认）"""
        return self._confirmed.get(request_id, False)

    def consume_confirm(self, request_id: str) -> bool:
        """读取并清除确认状态（执行完成后调用，避免状态泄漏）"""
        return self._confirmed.pop(request_id, False)

    def pending_count(self) -> int:
        """当前待确认数量"""
        self.expire_check()
        return len(self._pending)

    def get_pending(self, request_id: str) -> Optional[PendingConfirm]:
        """查询待确认项"""
        return self._pending.get(request_id)

    def expire_check(self) -> List[str]:
        """清理超时的待确认项，返回被清理的 request_id 列表"""
        expired = [rid for rid, p in self._pending.items() if p.expired]
        for rid in expired:
            self._pending.pop(rid, None)
            logger.info("[safety] 确认超时自动取消: %s", rid)
        return expired

    # ── 确认记忆 ──

    def _remember_key(self, action: str, params: Dict[str, Any]) -> str:
        """构造记忆键：action + 首个路径的父目录"""
        directory = ""
        for key in ("target", "source", "path", "dest_dir", "directory"):
            v = params.get(key)
            if isinstance(v, str) and v.strip():
                try:
                    directory = str(Path(v).expanduser().parent)
                except Exception:
                    directory = ""
                break
        if not directory:
            targets = params.get("targets")
            if isinstance(targets, list) and targets:
                try:
                    directory = str(Path(str(targets[0])).expanduser().parent)
                except Exception:
                    directory = ""
        return f"{action}|{self._norm_for_compare(directory)}"

    def _remember(self, action: str, params: Dict[str, Any]) -> None:
        """记住用户的批准选择"""
        key = self._remember_key(action, params)
        self._remembered[key] = time.time() + self._remember_ttl

    def _is_remembered(self, action: str, params: Dict[str, Any]) -> bool:
        """该操作是否已被记住（在有效期内）"""
        key = self._remember_key(action, params)
        until = self._remembered.get(key)
        if until is None:
            return False
        if time.time() > until:
            self._remembered.pop(key, None)
            return False
        return True

    def clear_remembered(self) -> None:
        """清空确认记忆（用户修改配置时调用）"""
        self._remembered.clear()

    # ══════════════════════════════════════════════
    #  审计日志
    # ══════════════════════════════════════════════

    def audit(
        self,
        action: str,
        params: Dict[str, Any],
        success: bool,
        detail: str = "",
    ) -> None:
        """记录审计日志（写失败不抛异常，避免影响主流程）"""
        if not self._audit_enabled or self._db is None:
            return
        try:
            import json

            self._db.execute(
                "INSERT INTO audit (ts, action, params, success, detail) VALUES (?, ?, ?, ?, ?)",
                (
                    time.time(),
                    action,
                    json.dumps(params, ensure_ascii=False, default=str),
                    1 if success else 0,
                    detail,
                ),
            )
            self._db.commit()
        except Exception as e:
            logger.error("[safety] 审计写入失败: %s", e)

    def query_audit(self, limit: int = 100) -> List[Dict[str, Any]]:
        """查询最近的审计记录（按时间倒序）"""
        if self._db is None:
            return []
        try:
            cur = self._db.execute(
                "SELECT ts, action, params, success, detail FROM audit ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            return [
                {
                    "ts": row[0],
                    "action": row[1],
                    "params": row[2],
                    "success": bool(row[3]),
                    "detail": row[4],
                }
                for row in cur.fetchall()
            ]
        except Exception as e:
            logger.error("[safety] 审计查询失败: %s", e)
            return []

    def cleanup_audit(self, retention_days: int = 30) -> int:
        """清理过期审计记录，返回删除条数"""
        if self._db is None:
            return 0
        try:
            cutoff = time.time() - retention_days * 86400
            cur = self._db.execute("DELETE FROM audit WHERE ts < ?", (cutoff,))
            self._db.commit()
            return cur.rowcount or 0
        except Exception as e:
            logger.error("[safety] 审计清理失败: %s", e)
            return 0

    def close(self) -> None:
        """释放资源"""
        if self._db is not None:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None
        self._pending.clear()
        self._confirmed.clear()
        self._remembered.clear()
