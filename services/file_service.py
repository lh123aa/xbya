"""
文件服务模块
负责文件监控、检索和管理
"""

import os
import logging
import sqlite3
from typing import Optional, Callable, List, Dict, Any
from pathlib import Path
from datetime import datetime

from core.event_bus import get_event_bus, EventType
from core.plugin_loader import get_plugin_loader
from core.config_manager import get_config_manager

logger = logging.getLogger(__name__)


class FileService:
    """文件服务"""
    
    def __init__(self):
        """初始化文件服务"""
        self.event_bus = get_event_bus()
        self.plugin_loader = get_plugin_loader()
        self.config_manager = get_config_manager()
        
        # 插件实例
        self.file_monitor = None
        self.embedding = None
        self.vector_db = None

        #: 能力名 -> 降级原因（D49）。空 = 该能力真的可用。
        #:
        #: 为什么需要它：加载失败时这里拿到的是**空对象**（NullEmbedding /
        #: NullVectorDB），不是 None；而 `semantic_search` 原先的判据是
        #: `is None`，**永远接不住空对象**，于是"降级到全文搜索"那条路不可达，
        #: 用户只会看到一句没有归因的「查询向量化失败」。
        #: 把"为什么降级"记在这里，日志里就能点名**要改哪个配置键**。
        self._degraded: Dict[str, str] = {}
        
        # 监控目录
        self.watch_dirs: List[str] = []
        
        # 回调函数
        self.on_file_detected: Optional[Callable[[str], None]] = None
        
        # 数据库连接
        self.db_connection: Optional[sqlite3.Connection] = None
        
        logger.info("文件服务初始化")
    
    def initialize(self) -> bool:
        """
        初始化文件服务插件
        
        Returns:
            是否初始化成功
        """
        try:
            # 加载文件监控插件
            monitor_config = self.config_manager.get_plugin_config("file_monitor")
            monitor_engine = monitor_config.get("engine", "watchfiles")
            
            if monitor_engine and monitor_engine != "null":
                self.file_monitor = self.plugin_loader.load(monitor_engine)
                if self.file_monitor:
                    logger.info(f"文件监控插件加载成功: {monitor_engine}")
                else:
                    logger.warning(f"文件监控插件加载失败: {monitor_engine}")
                    self.file_monitor = self.plugin_loader.load_by_interface("FileMonitorEngine")
            else:
                self.file_monitor = self.plugin_loader.load_by_interface("FileMonitorEngine")
            
            # 加载Embedding插件
            embedding_config = self.config_manager.get_plugin_config("embedding")
            embedding_engine = embedding_config.get("engine", "embed_anything")
            
            if embedding_engine and embedding_engine != "null":
                self.embedding = self.plugin_loader.load(embedding_engine)
                if self.embedding:
                    logger.info(f"Embedding插件加载成功: {embedding_engine}")
                else:
                    logger.warning(f"Embedding插件加载失败: {embedding_engine}")
                    self._degraded["embedding"] = (
                        f"plugins.embedding.engine 写的是 '{embedding_engine}'，"
                        f"但插件加载器里没有这个插件（要求目录下存在 plugin.py）"
                    )
                    self.embedding = self.plugin_loader.load_by_interface("EmbeddingEngine")
            else:
                self._degraded["embedding"] = (
                    f"plugins.embedding.engine = {embedding_engine!r}（该能力已禁用）"
                )
                self.embedding = self.plugin_loader.load_by_interface("EmbeddingEngine")
            
            # 加载VectorDB插件
            vectordb_config = self.config_manager.get_plugin_config("vector_db")
            vectordb_engine = vectordb_config.get("engine", "leann")
            
            if vectordb_engine and vectordb_engine != "null":
                self.vector_db = self.plugin_loader.load(vectordb_engine)
                if self.vector_db:
                    logger.info(f"VectorDB插件加载成功: {vectordb_engine}")
                else:
                    logger.warning(f"VectorDB插件加载失败: {vectordb_engine}")
                    self._degraded["vector_db"] = (
                        f"plugins.vector_db.engine 写的是 '{vectordb_engine}'，"
                        f"但插件加载器里没有这个插件（要求目录下存在 plugin.py）"
                    )
                    self.vector_db = self.plugin_loader.load_by_interface("VectorDBEngine")
            else:
                self._degraded["vector_db"] = (
                    f"plugins.vector_db.engine = {vectordb_engine!r}（该能力已禁用）"
                )
                self.vector_db = self.plugin_loader.load_by_interface("VectorDBEngine")
            
            # 初始化数据库
            self._init_database()
            
            return True
            
        except Exception as e:
            logger.error(f"初始化文件服务插件失败: {e}")
            return False
    
    def _init_database(self) -> None:
        """初始化SQLite数据库"""
        try:
            db_path = Path("data/index.db")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            
            self.db_connection = sqlite3.connect(str(db_path))
            
            # 创建文件索引表
            cursor = self.db_connection.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS file_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT UNIQUE NOT NULL,
                    file_name TEXT NOT NULL,
                    file_type TEXT,
                    file_size INTEGER,
                    created_time TIMESTAMP,
                    modified_time TIMESTAMP,
                    indexed_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    summary TEXT,
                    metadata TEXT
                )
            """)
            
            # 创建全文搜索表（FTS5）
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS file_search 
                USING fts5(file_name, file_type, summary, content='file_index', content_rowid='id')
            """)
            
            self.db_connection.commit()
            logger.info("数据库初始化完成")
            
        except Exception as e:
            logger.error(f"初始化数据库失败: {e}")
    
    def start_monitoring(self, callback: Optional[Callable[[str, str], None]] = None) -> bool:
        """
        启动文件监控
        
        Args:
            callback: 回调函数，参数为(事件类型, 文件路径)
            
        Returns:
            是否启动成功
        """
        try:
            if self.file_monitor is None:
                logger.error("文件监控插件未初始化")
                return False
            
            # 获取监控目录
            monitor_config = self.config_manager.get_plugin_config("file_monitor")
            watch_dirs = monitor_config.get("params", {}).get("watch_dirs", [])
            recursive = monitor_config.get("params", {}).get("recursive", True)
            
            # 展开路径
            expanded_dirs = []
            for dir_path in watch_dirs:
                expanded = os.path.expanduser(dir_path)
                if os.path.exists(expanded):
                    expanded_dirs.append(expanded)
                else:
                    logger.warning(f"监控目录不存在: {expanded}")
            
            if not expanded_dirs:
                logger.warning("没有有效的监控目录")
                return False
            
            # 启动监控
            def monitor_callback(event_type: str, file_path: str):
                logger.info(f"文件事件: {event_type} - {file_path}")
                
                # 发送文件检测事件
                self.event_bus.emit(EventType.FILE_DETECTED, {
                    "event_type": event_type,
                    "file_path": file_path
                })
                
                # 如果是新文件，生成摘要
                if event_type == "create":
                    self._process_new_file(file_path)
                
                # 调用回调函数
                if callback:
                    callback(event_type, file_path)
            
            success = self.file_monitor.start(expanded_dirs, monitor_callback, recursive)
            
            if success:
                self.watch_dirs = expanded_dirs
                logger.info(f"文件监控已启动，监控目录: {expanded_dirs}")
            
            return success
            
        except Exception as e:
            logger.error(f"启动文件监控失败: {e}")
            return False
    
    def stop_monitoring(self) -> bool:
        """
        停止文件监控
        
        Returns:
            是否停止成功
        """
        try:
            if self.file_monitor:
                success = self.file_monitor.stop()
                if success:
                    logger.info("文件监控已停止")
                return success
            return False
            
        except Exception as e:
            logger.error(f"停止文件监控失败: {e}")
            return False
    
    def _process_new_file(self, file_path: str) -> None:
        """
        处理新文件
        
        Args:
            file_path: 文件路径
        """
        try:
            # 生成文件摘要
            summary = self.generate_summary(file_path)
            
            # 索引文件
            self.index_file(file_path, summary)
            
            # 发送提醒事件
            file_name = os.path.basename(file_path)
            self.event_bus.emit(EventType.FILE_REMINDER, {
                "file_path": file_path,
                "file_name": file_name,
                "summary": summary
            })
            
        except Exception as e:
            logger.error(f"处理新文件失败: {e}")
    
    def generate_summary(self, file_path: str) -> Optional[str]:
        """
        生成文件摘要
        
        Args:
            file_path: 文件路径
            
        Returns:
            文件摘要
        """
        try:
            # 获取文件信息
            file_name = os.path.basename(file_path)
            file_ext = os.path.splitext(file_name)[1].lower()
            
            # 根据文件类型生成摘要
            if file_ext in ['.txt', '.md', '.py', '.js', '.html', '.css']:
                # 文本文件，读取内容摘要
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(1000)  # 读取前1000个字符
                    return f"文本文件: {file_name}\n内容预览: {content[:200]}..."
            
            elif file_ext in ['.doc', '.docx', '.pdf']:
                # 文档文件
                return f"文档文件: {file_name}\n类型: {file_ext}"
            
            elif file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']:
                # 图片文件
                return f"图片文件: {file_name}\n格式: {file_ext}"
            
            elif file_ext in ['.mp3', '.wav', '.flac', '.aac']:
                # 音频文件
                return f"音频文件: {file_name}\n格式: {file_ext}"
            
            elif file_ext in ['.mp4', '.avi', '.mkv', '.mov']:
                # 视频文件
                return f"视频文件: {file_name}\n格式: {file_ext}"
            
            else:
                return f"文件: {file_name}\n类型: {file_ext}"
            
        except Exception as e:
            logger.error(f"生成文件摘要失败: {e}")
            return f"文件: {os.path.basename(file_path)}"
    
    def index_file(self, file_path: str, summary: Optional[str] = None) -> bool:
        """
        索引文件
        
        Args:
            file_path: 文件路径
            summary: 文件摘要
            
        Returns:
            是否成功
        """
        try:
            if self.db_connection is None:
                logger.error("数据库未初始化")
                return False
            
            # 获取文件信息
            file_stat = os.stat(file_path)
            file_name = os.path.basename(file_path)
            file_ext = os.path.splitext(file_name)[1].lower()
            
            # 插入文件索引
            cursor = self.db_connection.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO file_index 
                (file_path, file_name, file_type, file_size, created_time, modified_time, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                file_path,
                file_name,
                file_ext,
                file_stat.st_size,
                datetime.fromtimestamp(file_stat.st_ctime).isoformat(),
                datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
                summary
            ))
            
            self.db_connection.commit()
            
            # 更新全文搜索索引
            cursor.execute("""
                INSERT INTO file_search (rowid, file_name, file_type, summary)
                SELECT id, file_name, file_type, summary FROM file_index WHERE file_path = ?
            """, (file_path,))
            
            self.db_connection.commit()
            
            logger.info(f"文件索引成功: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"索引文件失败: {e}")
            return False
    
    def search_files(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        搜索文件（全文搜索）
        
        Args:
            query: 搜索查询
            limit: 返回结果数量限制
            
        Returns:
            搜索结果列表
        """
        try:
            if self.db_connection is None:
                logger.error("数据库未初始化")
                return []
            
            cursor = self.db_connection.cursor()
            
            # 使用FTS5全文搜索
            cursor.execute("""
                SELECT f.file_path, f.file_name, f.file_type, f.file_size, f.summary,
                       rank
                FROM file_index f
                JOIN file_search fs ON f.id = fs.rowid
                WHERE file_search MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, limit))
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    "file_path": row[0],
                    "file_name": row[1],
                    "file_type": row[2],
                    "file_size": row[3],
                    "summary": row[4],
                    "relevance": row[5]
                })
            
            logger.info(f"搜索文件: {query}, 找到 {len(results)} 个结果")
            return results
            
        except Exception as e:
            logger.error(f"搜索文件失败: {e}")
            return []
    
    def _embedding_unavailable(self) -> bool:
        """embedding 插件是否不可用。

        三级判断，从强到弱：
          1. 根本没拿到实例 → 不可用
          2. 实例自报 `is_available() is False` → 不可用（空对象走这条）
          3. 没有 `is_available` 接口 → 视为可用（不确定时不假装能判）

        注意第 2 条是**判空对象的唯一可靠方式**，不能用 `is None` 代替。
        """
        if self.embedding is None:
            return True
        getter = getattr(self.embedding, "is_available", None)
        if callable(getter):
            try:
                return not bool(getter())
            except Exception as e:      # 插件自报异常：按不可用处理，但要说出来
                logger.warning("embedding.is_available() 抛异常: %s", e)
                return True
        return False

    def _vector_db_unavailable(self) -> bool:
        """vector_db 插件是否不可用。判据同 `_embedding_unavailable`。"""
        if self.vector_db is None:
            return True
        getter = getattr(self.vector_db, "is_available", None)
        if callable(getter):
            try:
                return not bool(getter())
            except Exception as e:
                logger.warning("vector_db.is_available() 抛异常: %s", e)
                return True
        return False

    def semantic_search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        语义搜索（基于向量）
        
        Args:
            query: 查询文本
            top_k: 返回结果数量
            
        Returns:
            搜索结果列表
        """
        try:
            # 判据不能是 `is None`：加载失败时拿到的是**空对象**，不是 None，
            # 所以那句 `is None` 永远不成立，"降级到全文搜索"实际不可达（D49）。
            # 正确的问法是"**这个插件自己说它可用吗**" —— 空对象的
            # `is_available()` 返回 False，这是它自己的契约。
            if self._embedding_unavailable() or self._vector_db_unavailable():
                why = '；'.join(
                    f'{k}: {v}' for k, v in sorted(self._degraded.items())
                ) or '插件未初始化'
                logger.warning("语义搜索不可用，改用全文搜索（原因：%s）", why)
                return self.search_files(query, top_k)
            
            # 将查询转换为向量
            query_vector = self.embedding.embed(query)
            if query_vector is None or len(query_vector) == 0:
                logger.error("查询向量化失败")
                return []
            
            # 向量检索
            results = self.vector_db.search(query_vector, top_k)
            
            logger.info(f"语义搜索: {query}, 找到 {len(results)} 个结果")
            return results
            
        except Exception as e:
            logger.error(f"语义搜索失败: {e}")
            return []
    
    def open_file(self, file_path: str) -> bool:
        """
        打开文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            是否成功
        """
        try:
            import subprocess
            import platform
            
            if platform.system() == 'Windows':
                os.startfile(file_path)
            elif platform.system() == 'Darwin':  # macOS
                subprocess.run(['open', file_path])
            else:  # Linux
                subprocess.run(['xdg-open', file_path])
            
            logger.info(f"打开文件: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"打开文件失败: {e}")
            return False
    
    def delete_file(self, file_path: str) -> bool:
        """
        删除文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            是否成功
        """
        try:
            # 发送安全确认事件
            self.event_bus.emit(EventType.SECURITY_CONFIRM, {
                "action": "delete",
                "file_path": file_path,
                "require_voice_confirm": True
            })
            
            # 这里应该等待用户确认
            # 暂时直接删除
            os.remove(file_path)
            
            # 从数据库中删除索引
            if self.db_connection:
                cursor = self.db_connection.cursor()
                cursor.execute("DELETE FROM file_index WHERE file_path = ?", (file_path,))
                cursor.execute("""
                    DELETE FROM file_search WHERE rowid = (
                        SELECT id FROM file_index WHERE file_path = ?
                    )
                """, (file_path,))
                self.db_connection.commit()
            
            logger.info(f"删除文件: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"删除文件失败: {e}")
            return False
    
    def export_index(self, export_path: str) -> bool:
        """
        导出索引数据
        
        Args:
            export_path: 导出路径
            
        Returns:
            是否成功
        """
        try:
            if self.db_connection is None:
                logger.error("数据库未初始化")
                return False
            
            # 查询所有索引数据
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT * FROM file_index")
            
            import json
            data = []
            for row in cursor.fetchall():
                data.append({
                    "file_path": row[1],
                    "file_name": row[2],
                    "file_type": row[3],
                    "file_size": row[4],
                    "created_time": row[5],
                    "modified_time": row[6],
                    "summary": row[7]
                })
            
            # 写入JSON文件
            with open(export_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"索引数据导出成功: {export_path}")
            return True
            
        except Exception as e:
            logger.error(f"导出索引数据失败: {e}")
            return False


# 全局文件服务实例
_file_service: Optional[FileService] = None


def get_file_service() -> FileService:
    """
    获取文件服务单例
    
    Returns:
        文件服务实例
    """
    global _file_service
    
    if _file_service is None:
        _file_service = FileService()
    
    return _file_service
