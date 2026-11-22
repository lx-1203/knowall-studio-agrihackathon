"""临时文件/目录上下文（移植自 docreader/utils/tempfile.py）。

上游为需要真实路径的第三方解析器（ebooklib 等）准备这个工具；本包里的
epub/mhtml 都在内存里解析，不落盘，所以它目前是给后续解析器备用。
"""
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


class TempFileContext:
    def __init__(self, file_content: bytes, suffix: str):
        self.file_content = file_content
        self.suffix = suffix

    def _cleanup(self):
        """关闭并删除临时文件。

        close() 会把之前 flush 的错误重新抛出来，所以 unlink 必须照样执行，
        且清理自身的异常不能盖掉原始异常。
        """
        temp_file = getattr(self, "temp_file", None)
        if not temp_file:
            return
        try:
            temp_file.close()
        except OSError:
            pass
        try:
            os.remove(temp_file.name)
        except OSError:
            pass

    def __enter__(self):
        self.temp_file = tempfile.NamedTemporaryFile(suffix=self.suffix, delete=False)
        # 写/刷新失败时异常从 __enter__ 逃逸，__exit__ 不会被调用；而此时
        # delete=False 的文件连 GC 都不会删，只能在这里自己收尾，否则永久泄漏。
        try:
            self.temp_file.write(self.file_content)
            self.temp_file.flush()
        except BaseException:
            self._cleanup()
            raise
        logger.info(
            f"Saved {self.suffix} content to temporary file: {self.temp_file.name}"
        )
        return self.temp_file.name

    def __exit__(self, exc_type, exc_val, exc_tb):
        if getattr(self, "temp_file", None):
            name = self.temp_file.name
            self._cleanup()
            logger.info(f"File {name} has been deleted.")
        return False


class TempDirContext:
    def __enter__(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        logger.info(f"Created temporary directory: {self.temp_dir.name}")
        return self.temp_dir.name

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.temp_dir and os.path.exists(self.temp_dir.name):
            self.temp_dir.cleanup()
            logger.info(f"Directory {self.temp_dir.name} has been deleted.")
        return False
