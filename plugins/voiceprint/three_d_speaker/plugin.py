"""
3D-Speaker声纹识别插件 v2.0
基于 resemblyzer 的真实说话人声纹识别（替换旧的随机向量假实现）。

用途：区分「用户的声音」vs「宠物TTS合成声」，用于"仅用户声音可打断"机制。

核心能力：
- enroll(audio, user_id)：用户录一段话 → 提取声纹 embedding → 持久化
- verify(audio, user_id)：待验语音 → 提取 embedding → 与用户参考比对（余弦相似度）
- embedding 由 resemblyzer 预训练说话人模型生成（256维），
  同一说话人相似度高(>0.75)，不同说话人/TTS 相似度低(<0.7)。
"""

import logging
import os
import numpy as np
from typing import Optional, Dict, Any

from interfaces.voiceprint import VoiceprintEngine

logger = logging.getLogger(__name__)


class ThreeDSpeakerVoiceprint(VoiceprintEngine):
    """基于 resemblyzer 的说话人声纹识别实现"""

    def __init__(self, model_name: str = "resemblyzer", threshold: float = 0.75,
                 storage_dir: str = "data/voiceprint"):
        """
        初始化说话人声纹识别

        Args:
            model_name: 模型名（resemblyzer）
            threshold: 验证阈值（余弦相似度），高于此值判定为同一说话人
            storage_dir: 声纹存储目录
        """
        self.model_name = model_name
        self.threshold = threshold
        self.storage_dir = storage_dir
        self._encoder = None          # resemblyzer VoiceEncoder（懒加载）
        self._available = False
        self.voiceprint_db: Dict[str, np.ndarray] = {}

        # 尝试加载模型
        self._load_model()
        # 加载已持久化的声纹
        self._load_voiceprints()

    def _load_model(self) -> bool:
        """加载 resemblyzer 说话人模型（懒：仅初始化句柄，真正模型在首次使用前按需加载）"""
        try:
            # 编译时校验，避免导入失败；实际模型首次 embed 前 lazy 加载
            from resemblyzer import VoiceEncoder  # noqa: F401
            logger.info(f"初始化说话人声纹识别: {self.model_name} (resemblyzer, 阈值={self.threshold})")
            self._available = True
            return True
        except ImportError:
            logger.warning("resemblyzer 未安装，声纹识别不可用")
            self._available = False
            return False
        except Exception as e:
            logger.error(f"加载说话人模型失败: {e}")
            return False

    def _get_encoder(self):
        """懒加载 VoiceEncoder 模型"""
        if self._encoder is None:
            from resemblyzer import VoiceEncoder
            self._encoder = VoiceEncoder()
            logger.info("resemblyzer VoiceEncoder 已加载")
        return self._encoder

    def _extract_features(self, audio_path: str) -> Optional[np.ndarray]:
        """
        提取音频的说话人 embedding（256维）

        Args:
            audio_path: 音频文件路径

        Returns:
            归一化特征向量，失败返回 None
        """
        try:
            from resemblyzer import preprocess_wav
            encoder = self._get_encoder()
            wav = preprocess_wav(audio_path)   # 重采样到16k单声道
            emb = encoder.embed_utterance(wav)
            if emb is None:
                return None
            emb = np.asarray(emb, dtype=np.float32)
            norm = np.linalg.norm(emb)
            if norm < 1e-8:
                return None
            return emb / norm
        except Exception as e:
            logger.error(f"提取声纹特征失败: {e}")
            return None

    @staticmethod
    def _cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
        """余弦相似度（向量已归一化则直接点积）"""
        return float(np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2) + 1e-8))

    def enroll(self, audio_path: str, user_id: str) -> bool:
        """
        注册声纹（用户录一段话 → 存 embedding）

        Args:
            audio_path: 音频文件路径
            user_id: 用户ID

        Returns:
            是否注册成功
        """
        if not self._available:
            logger.error("声纹识别引擎不可用")
            return False
        try:
            features = self._extract_features(audio_path)
            if features is None:
                return False
            self.voiceprint_db[user_id] = features
            self._save_voiceprint(user_id, features)
            logger.info(f"声纹注册成功: {user_id}")
            return True
        except Exception as e:
            logger.error(f"声纹注册失败: {e}")
            return False

    def verify(self, audio_path: str, user_id: str) -> bool:
        """
        验证声纹：待验语音是否属于该用户

        Args:
            audio_path: 音频文件路径
            user_id: 用户ID

        Returns:
            是否通过（相似度≥阈值）
        """
        if not self._available:
            logger.error("声纹识别引擎不可用")
            return False
        if user_id not in self.voiceprint_db:
            logger.warning(f"用户未注册声纹: {user_id}")
            return False
        try:
            features = self._extract_features(audio_path)
            if features is None:
                return False
            stored = self.voiceprint_db[user_id]
            similarity = self._cosine_similarity(features, stored)
            logger.info(f"声纹验证: 相似度={similarity:.4f}, 阈值={self.threshold}")
            return similarity >= self.threshold
        except Exception as e:
            logger.error(f"声纹验证失败: {e}")
            return False

    def verify_similarity(self, audio_path: str, user_id: str) -> float:
        """
        返回待验语音与用户声纹的相似度（不做阈值判断，供调试/调参）

        Args:
            audio_path: 音频文件路径
            user_id: 用户ID

        Returns:
            相似度（-1~1）
        """
        if user_id not in self.voiceprint_db:
            return -1.0
        try:
            features = self._extract_features(audio_path)
            if features is None:
                return -1.0
            return self._cosine_similarity(features, self.voiceprint_db[user_id])
        except Exception:
            return -1.0

    def identify(self, audio_path: str) -> Optional[str]:
        """
        识别声纹（识别是谁）

        Args:
            audio_path: 音频文件路径

        Returns:
            最相似的已注册用户ID；低于阈值返回 None
        """
        if not self._available:
            return None
        if not self.voiceprint_db:
            return None
        try:
            features = self._extract_features(audio_path)
            if features is None:
                return None
            best_user, best_sim = None, -1.0
            for uid, stored in self.voiceprint_db.items():
                sim = self._cosine_similarity(features, stored)
                if sim > best_sim:
                    best_sim, best_user = sim, uid
            if best_sim >= self.threshold:
                logger.info(f"声纹识别: 用户={best_user}, 相似度={best_sim:.4f}")
                return best_user
            logger.info(f"声纹识别失败: 最高相似度={best_sim:.4f} < 阈值={self.threshold}")
            return None
        except Exception as e:
            logger.error(f"声纹识别失败: {e}")
            return None

    def delete_user(self, user_id: str) -> bool:
        if user_id in self.voiceprint_db:
            del self.voiceprint_db[user_id]
            vp = os.path.join(self.storage_dir, f"{user_id}.npy")
            if os.path.exists(vp):
                try:
                    os.remove(vp)
                except Exception:
                    pass
            logger.info(f"声纹删除成功: {user_id}")
            return True
        return False

    def is_enrolled(self, user_id: str) -> bool:
        """检查用户是否已注册声纹"""
        return user_id in self.voiceprint_db

    def _save_voiceprint(self, user_id: str, features: np.ndarray) -> None:
        try:
            os.makedirs(self.storage_dir, exist_ok=True)
            np.save(os.path.join(self.storage_dir, f"{user_id}.npy"), features)
        except Exception as e:
            logger.error(f"保存声纹文件失败: {e}")

    def _load_voiceprints(self) -> None:
        """加载所有已持久化的声纹"""
        try:
            if not os.path.exists(self.storage_dir):
                return
            for fn in os.listdir(self.storage_dir):
                if fn.endswith(".npy"):
                    uid = fn[:-4]
                    self.voiceprint_db[uid] = np.load(os.path.join(self.storage_dir, fn)).astype(np.float32)
                    logger.info(f"加载声纹: {uid}")
        except Exception as e:
            logger.error(f"加载声纹失败: {e}")

    def is_available(self) -> bool:
        return self._available


def register():
    return {
        "name": "3d_speaker",
        "version": "2.0.0",
        "interface": "VoiceprintEngine",
        "class": "ThreeDSpeakerVoiceprint",
        "dependencies": ["resemblyzer"],
    }
