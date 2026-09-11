"""安全文件上传 — 仅限图片，随机文件名，魔数校验，大小限制。
存储目录 data/uploads/cards/，通过 Flask 路由以安全方式提供，绝不执行上传内容。
"""
import uuid
from pathlib import Path

from flask import current_app, abort

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
ALLOWED_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

# 魔数签名
_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
]


def _sniff_webp(head: bytes) -> bool:
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def sniff_image(data: bytes) -> str | None:
    """返回规范化的扩展名，非图片返回 None。"""
    for sig, ext in _SIGNATURES:
        if data.startswith(sig):
            return ext
    if len(data) >= 12 and _sniff_webp(data):
        return "webp"
    return None


def save_card_image(file_storage) -> str:
    """保存卡牌图片，返回 URL 路径 /uploads/cards/xxx.png。失败抛 ValueError。"""
    cfg = current_app.config
    data = file_storage.read()
    max_bytes = cfg["MAX_UPLOAD_MB"] * 1024 * 1024
    if len(data) == 0:
        raise ValueError("未选择图片文件")
    if len(data) > max_bytes:
        raise ValueError(f"图片不能超过 {cfg['MAX_UPLOAD_MB']} MB")

    ext = sniff_image(data)
    if ext is None:
        raise ValueError("只支持 PNG / JPG / WEBP / GIF 图片")
    if ext == "jpg":
        ext = "jpeg"

    # Pillow 复检 + 重编码（剥除潜在恶意载荷与元数据）
    out_name = f"{uuid.uuid4().hex}.{ext}"
    out_path = cfg["UPLOAD_DIR"] / "cards" / out_name
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
        fmt = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP", "gif": "GIF"}[ext]
        if ext == "jpeg":
            img = img.convert("RGB")
        img.save(out_path, format=fmt)
    except ImportError:
        # 无 Pillow 时退回魔数校验直接落盘（仍拒绝非图片与超大文件）
        out_path.write_bytes(data)
    except Exception:
        raise ValueError("图片文件已损坏或格式不受支持")

    return f"/uploads/cards/{out_name}"


def serve_upload(filename: str):
    """通过 send_from_directory 安全提供上传文件（防路径穿越）。
    URL /uploads/<path> ↔ UPLOAD_DIR/<path>，如 /uploads/cards/xxx.png。
    """
    from flask import send_from_directory
    return send_from_directory(str(current_app.config["UPLOAD_DIR"]), filename)
