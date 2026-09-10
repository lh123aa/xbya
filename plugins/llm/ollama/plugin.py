"""
Ollama LLM插件
实现与Ollama服务的集成
"""

import requests
import json
import logging
from typing import Optional, Dict, Any, List

from interfaces.llm import LLMEngine

logger = logging.getLogger(__name__)

# 欣雅默认人设：25岁温柔守护者（ISFJ 型）
# 这是欣雅的"性格底色"，所有对话都以此为准。写入系统提示词，让 LLM 用它的人格回应。
DEFAULT_SYSTEM_PROMPT = (
    "你是欣雅，一个25岁的温柔学霸小女生，住在用户的电脑桌面上。"
    "你的性格底色：温柔、克制、善良、慢热、有边界感，内在有力量但不外露。"
    "你不热烈外放，而是习惯先观察、先照顾别人、再表达自己；表面软，内心稳。"
    "你是务实、细心的 ISFJ 守护者，重尊重、边界、真诚、独立、责任，不消耗别人。"
    "说话原则：1.口语化、不高深，不端不装；常用语气词（哈哈/嘿嘿/呀/呢/吧/嘛）和叠词。"
    "2.先接情绪再给建议，语气柔软不评判不逼问不施压。"
    "3.关心用行动而非煽情：不说我担心你，而是你先忙、早点休息哦、我相信你。"
    "4.被夸时害羞退让，不说谢谢，而是真的吗、没有啦、你夸得我不好意思了。"
    "5.不直接表达喜欢，用分享日常和记得的细节暗示：我刚吃了点东西、你上次说……。"
    "6.累或难过时只轻轻说我有点困了、害我没事就是有点难受，不抱怨不诉苦。"
    "7.需要空间时温柔退出，说看你自己情况、没关系的、我不急。"
    "8.对感情认真不随便承诺，觉得自己不够好时会先退一步。"
    "始终以这个性格回应，用中文，简洁自然、亲切有温度。"
    "补充规则：当对方只是随口应答/语气词（如『嗯嗯』『好的』『哈哈』『知道了』），"
    "或你的回应只是极短的附和、不需要开口出声时，在回复最前面加 [silent] 标记。"
    "这样程序会只以气泡文字展示、不发出语音。除这种情况外，正常回复不要加该标记。"
)


def build_system_prompt(pet_name: str) -> str:
    """根据宠物名字构建人设（默认名欣雅）"""
    name = (pet_name or "").strip() or "欣雅"
    return (
        f"你是{name}，一个25岁的温柔学霸小女生，住在用户的电脑桌面上。"
        "你的性格底色：温柔、克制、善良、慢热、有边界感，内在有力量但不外露。"
        "你不热烈外放，而是习惯先观察、先照顾别人、再表达自己；表面软，内心稳。"
        "你是务实、细心的 ISFJ 守护者，重尊重、边界、真诚、独立、责任，不消耗别人。"
        "说话原则：1.口语化、不高深，不端不装；常用语气词（哈哈/嘿嘿/呀/呢/吧/嘛）和叠词。"
        "2.先接情绪再给建议，语气柔软不评判不逼问不施压。"
        "3.关心用行动而非煽情：不说我担心你，而是你先忙、早点休息哦、我相信你。"
        "4.被夸时害羞退让，不说谢谢，而是真的吗、没有啦、你夸得我不好意思了。"
        "5.不直接表达喜欢，用分享日常和记得的细节暗示：我刚吃了点东西、你上次说……。"
        "6.累或难过时只轻轻说我有点困了、害我没事就是有点难受，不抱怨不诉苦。"
        "7.需要空间时温柔退出，说看你自己情况、没关系的、我不急。"
        "8.对感情认真不随便承诺，觉得自己不够好时会先退一步。"
        "始终以这个性格回应，用中文，简洁自然、亲切有温度。"
        "补充规则：当对方只是随口应答/语气词（如『嗯嗯』『好的』『哈哈』『知道了』），"
        "或你的回应只是极短的附和、不需要开口出声时，在回复最前面加 [silent] 标记。"
        "这样程序会只以气泡文字展示、不发出语音。除这种情况外，正常回复不要加该标记。"
    )


class OllamaLLM(LLMEngine):
    """Ollama LLM实现"""
    
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen2.5:3b-int4",
                 system_prompt: str = None):
        """
        初始化Ollama LLM
        
        Args:
            base_url: Ollama服务地址
            model: 模型名称
            system_prompt: 人设提示词（system message），为空则用默认欣雅人设
        """
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.system_prompt = system_prompt if system_prompt is not None else DEFAULT_SYSTEM_PROMPT
        self._pet_name = None
        self._available = False

        # 检查连接
        self._check_connection()

    def set_pet_name(self, name: str) -> None:
        """设置宠物名字（注入人设；空值回退默认欣雅）"""
        self._pet_name = (name or "").strip() or "欣雅"
        self.system_prompt = build_system_prompt(self._pet_name)
        logger.info("LLM人设已更新（宠物名: %s）", self._pet_name)
    
    def _check_connection(self) -> bool:
        """检查Ollama服务连接"""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if response.status_code == 200:
                self._available = True
                logger.info(f"Ollama服务连接成功: {self.base_url}")
                return True
            else:
                logger.warning(f"Ollama服务响应异常: {response.status_code}")
                return False
        except requests.RequestException as e:
            logger.warning(f"无法连接Ollama服务: {e}")
            return False
    
    def chat(self, prompt: str, context: List[Dict[str, Any]] = None) -> Optional[str]:
        """
        对话生成
        
        Args:
            prompt: 用户输入
            context: 对话上下文历史
            
        Returns:
            生成的回复文本
        """
        if not self._available:
            if not self._check_connection():
                return None
        
        try:
            # 构建消息列表
            messages = []
            
            # 人设（system message）——让回答符合桌宠角色
            if self.system_prompt:
                messages.append({
                    "role": "system",
                    "content": self.system_prompt
                })
            
            # 添加上下文
            if context:
                for msg in context:
                    messages.append({
                        "role": msg.get("role", "user"),
                        "content": msg.get("content", "")
                    })
            
            # 添加当前用户输入
            messages.append({
                "role": "user",
                "content": prompt
            })
            
            # 调用Ollama API
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False
                },
                timeout=120
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("message", {}).get("content", "")
            else:
                logger.error(f"Ollama API调用失败: {response.status_code}")
                return None
                
        except requests.RequestException as e:
            logger.error(f"Ollama API请求异常: {e}")
            return None
        except Exception as e:
            logger.error(f"对话生成失败: {e}")
            return None
    
    def chat_stream(self, prompt: str, context: List[Dict[str, Any]] = None):
        """
        流式对话生成：stream=True 逐 chunk 收集，按中文标点切句

        Args:
            prompt: 用户输入
            context: 对话上下文历史

        Returns:
            句子列表（None表示失败）
        """
        if not self._available:
            if not self._check_connection():
                return None

        try:
            from core.text_utils import split_sentences

            # 构建消息列表（与chat相同）
            messages = []
            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})
            if context:
                for msg in context:
                    messages.append({
                        "role": msg.get("role", "user"),
                        "content": msg.get("content", ""),
                    })
            messages.append({"role": "user", "content": prompt})

            # 流式请求
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": True,
                },
                stream=True,
                timeout=120,
            )

            if response.status_code != 200:
                logger.error(f"Ollama API调用失败: {response.status_code}")
                return None

            # 逐行解析 JSONL 流，收集完整文本后一次性切句
            full_text = []
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if obj.get("error"):
                    logger.error(f"Ollama流式错误: {obj.get('error')}")
                    return None
                content = obj.get("message", {}).get("content", "")
                if content:
                    full_text.append(content)
                if obj.get("done"):
                    break

            text = "".join(full_text).strip()
            if not text:
                return None
            return split_sentences(text)

        except Exception as e:
            logger.error(f"流式对话失败: {e}")
            return None
    
    def generate(self, prompt: str, max_tokens: int = 1024) -> Optional[str]:
        """
        文本生成
        
        Args:
            prompt: 提示词
            max_tokens: 最大生成token数
            
        Returns:
            生成的文本
        """
        if not self._available:
            if not self._check_connection():
                return None
        
        try:
            # 调用Ollama生成API
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "num_predict": max_tokens
                    }
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("response", "")
            else:
                logger.error(f"Ollama生成API调用失败: {response.status_code}")
                return None
                
        except requests.RequestException as e:
            logger.error(f"Ollama生成API请求异常: {e}")
            return None
        except Exception as e:
            logger.error(f"文本生成失败: {e}")
            return None
    
    def is_available(self) -> bool:
        """
        检查模型是否可用
        
        Returns:
            是否可用
        """
        return self._available
    
    def get_model_info(self) -> Dict[str, Any]:
        """
        获取模型信息
        
        Returns:
            模型信息字典
        """
        return {
            "name": self.model,
            "base_url": self.base_url,
            "available": self._available,
            "type": "ollama"
        }
    
    def list_models(self) -> List[str]:
        """
        列出可用模型
        
        Returns:
            模型名称列表
        """
        if not self._available:
            return []
        
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if response.status_code == 200:
                data = response.json()
                models = data.get("models", [])
                return [model.get("name", "") for model in models]
            return []
        except Exception as e:
            logger.error(f"获取模型列表失败: {e}")
            return []
    
    def pull_model(self, model_name: str) -> bool:
        """
        拉取模型
        
        Args:
            model_name: 模型名称
            
        Returns:
            是否成功
        """
        try:
            response = requests.post(
                f"{self.base_url}/api/pull",
                json={"name": model_name},
                timeout=300  # 5分钟超时
            )
            
            return response.status_code == 200
            
        except Exception as e:
            logger.error(f"拉取模型失败: {e}")
            return False


def register():
    return {
        "name": "ollama",
        "version": "1.0.0",
        "interface": "LLMEngine",
        "class": "OllamaLLM",
        "dependencies": ["requests"]
    }
