"""kaf CLI:生成密钥 / 转换 MD / 解密验证 / 检查文件。

用法:
  python -m kaf.cli keygen --out keys/
  python -m kaf.cli convert 分子生物学总结.md --out 分子生物学总结.kaf --pub keys/pub.pem --subject 分子生物学 --title "分子生物学总结"
  python -m kaf.cli inspect 分子生物学总结.kaf
  python -m kaf.cli decrypt 分子生物学总结.kaf --priv keys/priv.pem --out decrypted.json
"""
import argparse
import json
import os
import sys


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _write(path: str, data: bytes):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def cmd_keygen(args):
    from ..keys import generate_keypair, save_keypair
    priv, pub = generate_keypair(key_size=args.key_size)
    save_keypair(priv, pub, args.out)
    print(f"[OK] 密钥对已生成: {args.out}/priv.pem (私钥, 存客户端安全区)")
    print(f"     {args.out}/pub.pem (公钥, 上传服务端用于包裹内容密钥)")


def cmd_convert(args):
    from ..builder import KAFBuilder
    from ..keys import load_public_key

    md_text = _read(args.input).decode("utf-8")
    pub = load_public_key(args.pub)
    data = KAFBuilder(pub).build(
        md_text,
        subject=args.subject or "",
        title=args.title or os.path.splitext(os.path.basename(args.input))[0],
        source_doc=args.source_doc or "",
    )
    _write(args.out, data)
    print(f"[OK] 已转换: {args.input} → {args.out} ({len(data)} bytes)")


def cmd_inspect(args):
    from ..format import inspect_kaf
    data = _read(args.input)
    info = inspect_kaf(data)
    print(json.dumps(info, ensure_ascii=False, indent=2))


def cmd_decrypt(args):
    from ..format import unpack_kaf
    from ..keys import load_private_key

    data = _read(args.input)
    priv = load_private_key(args.priv)
    result = unpack_kaf(data, priv)
    if args.out:
        _write(args.out, result["payload_bytes"])
        print(f"[OK] 解密完成: {args.input} → {args.out} (payload {len(result['payload_bytes'])} bytes)")
    else:
        print(result["payload_bytes"].decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kaf", description="KnowAll Format (.kaf) 工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_keygen = sub.add_parser("keygen", help="生成账号密钥对")
    p_keygen.add_argument("--out", default="keys", help="输出目录")
    p_keygen.add_argument("--key-size", type=int, default=4096)
    p_keygen.set_defaults(func=cmd_keygen)

    p_convert = sub.add_parser("convert", help="Markdown → .kaf")
    p_convert.add_argument("input", help="输入 MD 文件")
    p_convert.add_argument("--out", required=True, help="输出 .kaf 路径")
    p_convert.add_argument("--pub", required=True, help="账号公钥 PEM")
    p_convert.add_argument("--subject", default="")
    p_convert.add_argument("--title", default="")
    p_convert.add_argument("--source-doc", default="")
    p_convert.set_defaults(func=cmd_convert)

    p_inspect = sub.add_parser("inspect", help="只读检查 .kaf 结构")
    p_inspect.add_argument("input", help=".kaf 文件")
    p_inspect.set_defaults(func=cmd_inspect)

    p_decrypt = sub.add_parser("decrypt", help="用私钥解密 .kaf")
    p_decrypt.add_argument("input", help=".kaf 文件")
    p_decrypt.add_argument("--priv", required=True, help="客户端私钥 PEM")
    p_decrypt.add_argument("--out", default=None, help="解密输出路径(默认打印到 stdout)")
    p_decrypt.set_defaults(func=cmd_decrypt)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
