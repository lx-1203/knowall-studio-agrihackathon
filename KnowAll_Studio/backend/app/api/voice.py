"""语音 API 路由 - STT (语音→文字) + TTS (文字→语音)"""
import logging
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.voice.stt_engine import stt_engine
from app.core.voice.tts_engine import tts_engine

logger = logging.getLogger("knowall.voice")
router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

# Whitelist of supported Whisper model sizes. Passing an arbitrary string here
# would otherwise be handed straight to WhisperModel(), which accepts a path or
# model name — enabling disk/path probing by an unauthenticated caller.
ALLOWED_MODEL_SIZES = {"tiny", "base", "small", "medium", "large-v3"}


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    voice: str = Field(default="xiaoxiao", max_length=100)
    rate: str = Field(default="+0%", max_length=20)
    volume: str = Field(default="+0%", max_length=20)


# ---------- 语音识别 (STT) ----------

@router.post("/stt")
async def speech_to_text(
    audio: UploadFile = File(...),
    language: str = Form(default="zh"),
    model_size: str = Form(default="base"),
):
    """语音转文字

    接收音频文件（wav/mp3/webm/ogg），返回识别文字。
    使用 faster-whisper 本地模型，零 API 费用。

    Args:
        audio: 音频文件
        language: 语言代码 (zh/en/auto)
        model_size: 模型大小 (tiny/base/small/medium/large-v3)
    """
    if model_size not in ALLOWED_MODEL_SIZES:
        raise HTTPException(400, f"不支持的 model_size: {model_size}（可用: {sorted(ALLOWED_MODEL_SIZES)}）")

    if not audio.content_type or not audio.content_type.startswith("audio/"):
        # 允许一些常见的音频 MIME 类型
        allowed = {"audio/wav", "audio/mpeg", "audio/mp3", "audio/webm", "audio/ogg",
                    "audio/mp4", "audio/x-m4a", "audio/flac", "application/octet-stream"}
        if audio.content_type not in allowed:
            logger.warning("Unusual audio content-type: %s, proceeding anyway", audio.content_type)

    audio_bytes = await audio.read()
    if len(audio_bytes) == 0:
        raise HTTPException(400, "音频文件为空")
    if len(audio_bytes) > 50 * 1024 * 1024:  # 50MB 限制
        raise HTTPException(400, "音频文件过大（最大 50MB）")

    try:
        result = await stt_engine.transcribe(
            audio_bytes=audio_bytes,
            language=language,
            model_size=model_size,
        )
        return result
    except ImportError:
        raise HTTPException(503, "语音识别模块未安装，请运行: pip install faster-whisper")
    except Exception as e:
        logger.exception("STT failed")
        raise HTTPException(500, f"语音识别失败: {str(e)}")


# ---------- 语音合成 (TTS) ----------

@router.post("/tts")
async def text_to_speech(req: TTSRequest):
    """文字转语音

    接收文字，返回 MP3 音频。
    使用 edge-tts (微软免费神经语音)，零 API 费用。

    可用声音:
    - xiaoxiao: 晓晓 - 女声 活泼自然 (默认)
    - yunxi: 云希 - 男声 年轻自然
    - yunjian: 云健 - 男声 成熟稳重
    - xiaoyi: 晓伊 - 女声 温柔甜美
    - yunyang: 云扬 - 男声 新闻播报
    """
    try:
        audio_bytes = await tts_engine.synthesize(
            text=req.text,
            voice=req.voice,
            rate=req.rate,
            volume=req.volume,
        )
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "inline",
                "Content-Length": str(len(audio_bytes)),
            },
        )
    except ImportError:
        raise HTTPException(503, "语音合成模块未安装，请运行: pip install edge-tts")
    except Exception as e:
        logger.exception("TTS failed")
        raise HTTPException(500, f"语音合成失败: {str(e)}")


@router.get("/voices")
async def list_voices():
    """获取可用 TTS 声音列表"""
    return {"voices": tts_engine.list_voices()}
