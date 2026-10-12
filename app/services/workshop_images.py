"""Validate raster bytes, strip metadata and bound memory/decoding cost."""
from __future__ import annotations
from io import BytesIO
import warnings
import threading

_decoders=threading.BoundedSemaphore(2)


def normalize_image(raw: bytes, *, reference=False) -> tuple[bytes,str,int,int]:
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        with _decoders,warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                if image.format not in {'PNG','JPEG','WEBP'} or image.width*image.height>20000000 or min(image.size)<64:
                    raise ValueError('仅支持 PNG/JPEG/WebP 图片，尺寸须不小于 64 像素且不超过 2000 万像素')
                bounds=(1920,1920) if reference else (1920,2160)
                image.draft('RGB',bounds)
                image.load();ImageOps.exif_transpose(image,in_place=True)
                image.thumbnail(bounds,Image.Resampling.LANCZOS)
                normalized=image.convert('RGB')
                output=BytesIO();normalized.save(output,'WEBP',quality=92,method=6)
                result=(output.getvalue(),'image/webp',normalized.width,normalized.height)
                normalized.close();return result
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning) as exc:
        raise ValueError('无法读取这张图片，请上传有效的 PNG/JPEG/WebP') from exc
