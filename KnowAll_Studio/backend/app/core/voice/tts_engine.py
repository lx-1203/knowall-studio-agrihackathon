"""语音合成引擎 - edge-tts (微软免费神经语音)"""
import logging
import io

logger = logging.getLogger("knowall.voice.tts")


class TTSEngine:
    """edge-tts 语音合成引擎

    使用微软 Edge 的免费神经语音服务，无需 API Key。
    支持多种高质量中文声音。
    """

    VOICES = {
        "xiaoxiao": "zh-CN-XiaoxiaoNeural",    # 女声 活泼自然 (默认)
        "yunxi":    "zh-CN-YunxiNeural",        # 男声 年轻自然
        "yunjian":  "zh-CN-YunjianNeural",      # 男声 成熟稳重
        "xiaoyi":   "zh-CN-XiaoyiNeural",       # 女声 温柔甜美
        "yunyang":  "zh-CN-YunyangNeural",      # 男声 新闻播报
        "xiaochen": "zh-CN-XiaochenNeural",     # 女声 甜美
        "xiaomeng": "zh-CN-XiaomengNeural",     # 女声 东北口音
        "xiaomo":   "zh-CN-XiaomoNeural",       # 女声 情感丰富
        "xiaoxuan": "zh-CN-XiaoxuanNeural",     # 女声 活泼
        "xiaorui":  "zh-CN-XiaoruiNeural",      # 女声 童声
    }

    def list_voices(self) -> list[dict]:
        """列出所有可用声音"""
        return [
            {"id": k, "name": v, "description": self._voice_desc(k)}
            for k, v in self.VOICES.items()
        ]

    def _voice_desc(self, voice_id: str) -> str:
        descs = {
            "xiaoxiao": "晓晓 - 女声 活泼自然",
            "yunxi": "云希 - 男声 年轻自然",
            "yunjian": "云健 - 男声 成熟稳重",
            "xiaoyi": "晓伊 - 女声 温柔甜美",
            "yunyang": "云扬 - 男声 新闻播报",
            "xiaochen": "晓辰 - 女声 甜美",
            "xiaomeng": "晓梦 - 女声 东北口音",
            "xiaomo": "晓墨 - 女声 情感丰富",
            "xiaoxuan": "晓萱 - 女声 活泼",
            "xiaorui": "晓睿 - 女声 童声",
        }
        return descs.get(voice_id, voice_id)

    async def synthesize(self, text: str, voice: str = "xiaoxiao", rate: str = "+0%", volume: str = "+0%") -> bytes:
        """文字转语音

        Args:
            text: 要合成的文字
            voice: 声音ID（xiaoxiao/yunxi/yunjian/xiaoyi 等）或完整的 voice name
            rate: 语速调整（如 "+20%", "-10%"）
            volume: 音量调整（如 "+50%", "-20%"）

        Returns:
            MP3 音频字节
        """
        import edge_tts

        voice_name = self.VOICES.get(voice, voice)

        communicate = edge_tts.Communicate(
            text=text,
            voice=voice_name,
            rate=rate,
            volume=volume,
        )

        buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.write(chunk["data"])

        audio_bytes = buffer.getvalue()
        if not audio_bytes:
            raise ValueError("TTS synthesis returned empty audio")

        return audio_bytes

    async def synthesize_to_file(self, text: str, output_path: str, voice: str = "xiaoxiao") -> str:
        """文字转语音并保存到文件"""
        import edge_tts

        voice_name = self.VOICES.get(voice, voice)
        communicate = edge_tts.Communicate(text=text, voice=voice_name)
        await communicate.save(output_path)
        return output_path


# 全局单例
tts_engine = TTSEngine()
