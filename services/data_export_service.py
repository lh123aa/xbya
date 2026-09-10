"""
数据导出服务模块
负责导出用户数据和配置
"""

import json
import sqlite3
import logging
import os
import shutil
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class DataExportService:
    """数据导出服务"""
    
    def __init__(self):
        """初始化数据导出服务"""
        self.export_dir = Path("data/exports")
        self.export_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info("数据导出服务初始化")
    
    def export_config(self, output_path: Optional[str] = None) -> bool:
        """
        导出配置文件
        
        Args:
            output_path: 输出路径
            
        Returns:
            是否成功
        """
        try:
            config_path = Path("config.yaml")
            if not config_path.exists():
                logger.error("配置文件不存在")
                return False
            
            if output_path is None:
                output_path = self.export_dir / f"config_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.yaml"
            
            shutil.copy2(config_path, output_path)
            
            logger.info(f"配置文件导出成功: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"导出配置文件失败: {e}")
            return False
    
    def export_file_index(self, output_path: Optional[str] = None) -> bool:
        """
        导出文件索引
        
        Args:
            output_path: 输出路径
            
        Returns:
            是否成功
        """
        try:
            db_path = Path("data/index.db")
            if not db_path.exists():
                logger.error("数据库文件不存在")
                return False
            
            # 连接数据库
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            
            # 查询所有索引数据
            cursor.execute("""
                SELECT file_path, file_name, file_type, file_size, 
                       created_time, modified_time, summary
                FROM file_index
            """)
            
            data = []
            for row in cursor.fetchall():
                data.append({
                    "file_path": row[0],
                    "file_name": row[1],
                    "file_type": row[2],
                    "file_size": row[3],
                    "created_time": row[4],
                    "modified_time": row[5],
                    "summary": row[6]
                })
            
            conn.close()
            
            # 导出为JSON
            if output_path is None:
                output_path = self.export_dir / f"file_index_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"文件索引导出成功: {output_path}，共 {len(data)} 条记录")
            return True
            
        except Exception as e:
            logger.error(f"导出文件索引失败: {e}")
            return False
    
    def export_voiceprints(self, output_path: Optional[str] = None) -> bool:
        """
        导出声纹数据
        
        Args:
            output_path: 输出路径
            
        Returns:
            是否成功
        """
        try:
            voiceprint_dir = Path("data/voiceprint")
            if not voiceprint_dir.exists():
                logger.warning("声纹目录不存在")
                return True
            
            if output_path is None:
                output_path = self.export_dir / f"voiceprint_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            # 复制声纹目录
            shutil.copytree(voiceprint_dir, output_path)
            
            logger.info(f"声纹数据导出成功: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"导出声纹数据失败: {e}")
            return False
    
    def export_conversation_history(self, history: List[Dict[str, Any]], 
                                   output_path: Optional[str] = None) -> bool:
        """
        导出对话历史
        
        Args:
            history: 对话历史
            output_path: 输出路径
            
        Returns:
            是否成功
        """
        try:
            if output_path is None:
                output_path = self.export_dir / f"conversation_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            
            logger.info(f"对话历史导出成功: {output_path}，共 {len(history)} 条记录")
            return True
            
        except Exception as e:
            logger.error(f"导出对话历史失败: {e}")
            return False
    
    def export_all(self, output_dir: Optional[str] = None) -> bool:
        """
        导出所有数据
        
        Args:
            output_dir: 输出目录
            
        Returns:
            是否成功
        """
        try:
            if output_dir is None:
                output_dir = self.export_dir / f"full_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            
            # 导出配置
            self.export_config(output_path / "config.yaml")
            
            # 导出文件索引
            self.export_file_index(output_path / "file_index.json")
            
            # 导出声纹数据
            self.export_voiceprints(output_path / "voiceprints")
            
            # 创建导出清单
            manifest = {
                "export_time": datetime.now().isoformat(),
                "export_path": str(output_path),
                "files": [
                    "config.yaml",
                    "file_index.json",
                    "voiceprints/"
                ]
            }
            
            with open(output_path / "manifest.json", 'w', encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            
            logger.info(f"所有数据导出成功: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"导出所有数据失败: {e}")
            return False
    
    def clear_all_data(self, confirm: bool = False) -> bool:
        """
        清除所有数据
        
        Args:
            confirm: 是否确认
            
        Returns:
            是否成功
        """
        if not confirm:
            logger.warning("清除数据需要确认")
            return False
        
        try:
            # 清除文件索引
            db_path = Path("data/index.db")
            if db_path.exists():
                os.remove(db_path)
            
            # 清除声纹数据
            voiceprint_dir = Path("data/voiceprint")
            if voiceprint_dir.exists():
                shutil.rmtree(voiceprint_dir)
            
            # 清除向量数据库
            vectordb_dir = Path("data/vectordb")
            if vectordb_dir.exists():
                shutil.rmtree(vectordb_dir)
            
            # 清除对话历史（内存中）
            
            logger.info("所有数据已清除")
            return True
            
        except Exception as e:
            logger.error(f"清除数据失败: {e}")
            return False
    
    def get_data_summary(self) -> Dict[str, Any]:
        """
        获取数据摘要
        
        Returns:
            数据摘要
        """
        summary = {
            "config_exists": Path("config.yaml").exists(),
            "database_exists": Path("data/index.db").exists(),
            "voiceprint_exists": Path("data/voiceprint").exists(),
            "vectordb_exists": Path("data/vectordb").exists(),
        }
        
        # 统计文件索引数量
        if summary["database_exists"]:
            try:
                conn = sqlite3.connect("data/index.db")
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM file_index")
                summary["file_count"] = cursor.fetchone()[0]
                conn.close()
            except:
                summary["file_count"] = 0
        
        # 统计声纹数量
        if summary["voiceprint_exists"]:
            try:
                voiceprint_files = list(Path("data/voiceprint").glob("*.npy"))
                summary["voiceprint_count"] = len(voiceprint_files)
            except:
                summary["voiceprint_count"] = 0
        
        return summary


# 全局数据导出服务实例
_data_export_service: Optional[DataExportService] = None


def get_data_export_service() -> DataExportService:
    """
    获取数据导出服务单例
    
    Returns:
        数据导出服务实例
    """
    global _data_export_service
    
    if _data_export_service is None:
        _data_export_service = DataExportService()
    
    return _data_export_service
