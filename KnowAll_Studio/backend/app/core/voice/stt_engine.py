"""语音识别引擎 - faster-whisper 本地部署"""
import logging
import os
import tempfile

logger = logging.getLogger("knowall.voice.stt")


class STTEngine:
    """faster-whisper 语音识别引擎（单例模式）

    首次调用时懒加载模型，后续复用。
    支持中文、英文等多语言识别。
    """

    def __init__(self):
        self.model = None
        self.model_size = None

    def load_model(self, model_size: str = "base"):
        """加载 Whisper 模型

        Args:
            model_size: 模型大小 (tiny/base/small/medium/large-v3)
                - tiny:   ~1GB, 最快，精度一般
                - base:   ~1GB, 较快，精度较好 (推荐默认)
                - small:  ~2GB, 中等速度，精度好
                - medium: ~5GB, 较慢，精度很好
                - large-v3: ~10GB, 最慢，精度最高
        """
        if self.model is not None and self.model_size == model_size:
            return

        try:
            from faster_whisper import WhisperModel
            logger.info("Loading Whisper model: %s (CPU int8)...", model_size)
            self.model = WhisperModel(model_size, device="cpu", compute_type="int8")
            self.model_size = model_size
            logger.info("Whisper model loaded: %s", model_size)
        except ImportError:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            raise
        except Exception as e:
            logger.error("Failed to load Whisper model: %s", e)
            raise

    async def transcribe(self, audio_bytes: bytes, language: str = "zh", model_size: str = "base") -> dict:
        """识别音频为文字

        Args:
            audio_bytes: 音频文件字节 (支持 wav/mp3/webm/ogg 等)
            language: 语言代码 (zh=中文, en=英文, auto=自动检测)
            model_size: 模型大小

        Returns:
            {text: str, language: str, duration: float, segments: list}
        """
        self.load_model(model_size)

        # 写入临时文件
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            lang = None if language == "auto" else language
            segments, info = self.model.transcribe(
                tmp_path,
                language=lang,
                beam_size=5,
                vad_filter=True,  # 去除静音段
            )

            segments_list = []
            full_text_parts = []
            for seg in segments:
                segments_list.append({
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": seg.text.strip(),
                })
                full_text_parts.append(seg.text.strip())

            full_text = " ".join(full_text_parts)

            return {
                "text": full_text,
                "language": info.language,
                "duration": round(info.duration, 2),
                "segments": segments_list,
            }
        finally:
            os.unlink(tmp_path)


# 全局单例
stt_engine = STTEngine()
