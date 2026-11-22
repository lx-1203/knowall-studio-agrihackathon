"""KAF 格式异常类型。所有解析/解密错误均以自定义异常抛出,便于上层

区分「文件损坏」(应重新下载)与「无权限」(应引导授权)。
"""


class KAFError(Exception):
    """KAF 格式基类异常。"""


class KAFParseError(KAFError):
    """文件结构解析失败(魔数/长度/字段缺失)。"""


class KAFVersionError(KAFError):
    """格式版本不兼容。"""


class KAFDecryptError(KAFError):
    """解密失败(密钥错误 / 密文被篡改 / 无权限解包)。"""


class KAFChecksumError(KAFError):
    """内容校验和不匹配(文件损坏)。"""
