"""
AI服务模块
负责大语言模型推理和智能对话
"""

import logging
import json
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime

from core.event_bus import get_event_bus, EventType
from core.plugin_loader import get_plugin_loader
from core.config_manager import get_config_manager

logger = logging.getLogger(__name__)


class AIService:
    """AI服务"""
    
    def __init__(self):
        """初始化AI服务"""
        self.event_bus = get_event_bus()
        self.plugin_loader = get_plugin_loader()
        self.config_manager = get_config_manager()
        
        # 插件实例
        self.llm = None
        
        # 对话历史
        self.conversation_history: List[Dict[str, Any]] = []
        self.max_history_length = 20
        
        # 系统提示词
        self.system_prompt = """你是小忆，一个友善的AI桌面管家。
你的职责是帮助用户管理文件、回答问题、提供建议。
请用简洁、友好的语气回复用户。
如果用户要求执行操作，请确认后执行。"""
        
        logger.info("AI服务初始化")
    
    def initialize(self) -> bool:
        """
        初始化AI插件
        
        Returns:
            是否初始化成功
        """
        try:
            # 加载LLM插件
            llm_config = self.config_manager.get_plugin_config("llm")
            llm_engine = llm_config.get("engine", "ollama")
            
            if llm_engine and llm_engine != "null":
                self.llm = self.plugin_loader.load(llm_engine)
                if self.llm:
                    logger.info(f"LLM插件加载成功: {llm_engine}")
                else:
                    logger.warning(f"LLM插件加载失败: {llm_engine}")
                    self.llm = self.plugin_loader.load_by_interface("LLMEngine")
            else:
                self.llm = self.plugin_loader.load_by_interface("LLMEngine")
            
            return True
            
        except Exception as e:
            logger.error(f"初始化AI插件失败: {e}")
            return False
    
    def chat(self, user_input: str) -> Optional[str]:
        """
        对话
        
        Args:
            user_input: 用户输入
            
        Returns:
            AI回复
        """
        if self.llm is None:
            logger.error("LLM引擎未初始化")
            return "抱歉，AI功能暂时不可用。"
        
        try:
            # 发送思考中事件
            self.event_bus.emit(EventType.AI_THINKING, {
                "user_input": user_input
            })
            
            # 添加用户消息到历史
            self.conversation_history.append({
                "role": "user",
                "content": user_input,
                "timestamp": datetime.now().isoformat()
            })
            
            # 限制历史长度
            if len(self.conversation_history) > self.max_history_length:
                self.conversation_history = self.conversation_history[-self.max_history_length:]
            
            # 构建上下文
            context = self._build_context()
            
            # 调用LLM生成回复
            response = self.llm.chat(user_input, context)
            
            if response:
                # 添加AI回复到历史
                self.conversation_history.append({
                    "role": "assistant",
                    "content": response,
                    "timestamp": datetime.now().isoformat()
                })
                
                # 发送回复事件
                self.event_bus.emit(EventType.AI_RESPONSE, {
                    "user_input": user_input,
                    "response": response
                })
                
                logger.info(f"AI回复: {response[:100]}...")
                return response
            else:
                logger.warning("LLM生成回复失败")
                return "抱歉，我暂时无法理解你的意思。"
            
        except Exception as e:
            logger.error(f"对话失败: {e}")
            return "抱歉，AI功能出现异常。"
    
    def _build_context(self) -> List[Dict[str, str]]:
        """构建对话上下文"""
        context = []
        
        # 添加系统提示
        context.append({
            "role": "system",
            "content": self.system_prompt
        })
        
        # 添加对话历史
        for message in self.conversation_history:
            context.append({
                "role": message["role"],
                "content": message["content"]
            })
        
        return context
    
    def process_command(self, command: str) -> Dict[str, Any]:
        """
        处理用户命令
        
        Args:
            command: 用户命令
            
        Returns:
            处理结果
        """
        result = {
            "success": False,
            "action": None,
            "response": None,
            "data": None
        }
        
        try:
            # 解析命令
            command_lower = command.lower()
            
            # 文件操作命令
            if "打开" in command_lower or "open" in command_lower:
                result["action"] = "open_file"
                result["response"] = "请告诉我需要打开哪个文件？"
                result["success"] = True
                
            elif "删除" in command_lower or "delete" in command_lower:
                result["action"] = "delete_file"
                result["response"] = "请告诉我需要删除哪个文件？我需要确认后才能执行。"
                result["success"] = True
                
            elif "查找" in command_lower or "搜索" in command_lower or "find" in command_lower:
                result["action"] = "search_file"
                result["response"] = "请描述你要查找的文件？"
                result["success"] = True
                
            elif "整理" in command_lower or "归类" in command_lower or "organize" in command_lower:
                result["action"] = "organize_files"
                result["response"] = "我可以帮你整理文件，请告诉我整理规则。"
                result["success"] = True
                
            # 系统命令
            elif "切换模式" in command_lower or "switch mode" in command_lower:
                result["action"] = "switch_mode"
                result["response"] = "请告诉我切换到哪个模式？（低档/中档/高档）"
                result["success"] = True
                
            elif "状态" in command_lower or "status" in command_lower:
                result["action"] = "get_status"
                result["response"] = "正在获取系统状态..."
                result["success"] = True
                
            # 其他命令，交给LLM处理
            else:
                response = self.chat(command)
                result["response"] = response
                result["success"] = True
                
        except Exception as e:
            logger.error(f"处理命令失败: {e}")
            result["response"] = "抱歉，处理命令时出现错误。"
        
        return result
    
    def generate_file_summary(self, file_path: str, content: str) -> Optional[str]:
        """
        生成文件摘要
        
        Args:
            file_path: 文件路径
            content: 文件内容
            
        Returns:
            文件摘要
        """
        if self.llm is None or not self.llm.is_available():
            return None
        
        try:
            prompt = f"""请为以下文件生成一个简洁的摘要：

文件路径：{file_path}
文件内容：
{content[:2000]}

请用一句话概括这个文件的主要内容和用途。"""
            
            response = self.llm.generate(prompt, max_tokens=100)
            return response
            
        except Exception as e:
            logger.error(f"生成文件摘要失败: {e}")
            return None
    
    def suggest_file_organization(self, files: List[Dict[str, Any]]) -> Optional[str]:
        """
        建议文件整理方案
        
        Args:
            files: 文件信息列表
            
        Returns:
            整理建议
        """
        if self.llm is None or not self.llm.is_available():
            return None
        
        try:
            # 构建文件列表描述
            file_descriptions = []
            for file_info in files[:10]:  # 最多处理10个文件
                desc = f"- {file_info.get('name', 'unknown')} ({file_info.get('type', 'unknown')})"
                file_descriptions.append(desc)
            
            file_list = "\n".join(file_descriptions)
            
            prompt = f"""我有以下文件需要整理：

{file_list}

请根据文件类型和内容，建议如何分类整理这些文件。给出具体的文件夹名称和整理规则。"""
            
            response = self.llm.generate(prompt, max_tokens=200)
            return response
            
        except Exception as e:
            logger.error(f"建议文件整理方案失败: {e}")
            return None
    
    def clear_conversation(self) -> None:
        """清空对话历史"""
        self.conversation_history.clear()
        logger.info("对话历史已清空")
    
    def set_system_prompt(self, prompt: str) -> None:
        """
        设置系统提示词
        
        Args:
            prompt: 系统提示词
        """
        self.system_prompt = prompt
        logger.info("系统提示词已更新")
    
    def get_conversation_history(self) -> List[Dict[str, Any]]:
        """获取对话历史"""
        return self.conversation_history.copy()


# 全局AI服务实例
_ai_service: Optional[AIService] = None


def get_ai_service() -> AIService:
    """
    获取AI服务单例
    
    Returns:
        AI服务实例
    """
    global _ai_service
    
    if _ai_service is None:
        _ai_service = AIService()
    
    return _ai_service
