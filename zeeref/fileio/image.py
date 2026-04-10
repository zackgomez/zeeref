# This file is part of ZeeRef.
#
# ZeeRef is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# ZeeRef is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with ZeeRef.  If not, see <https://www.gnu.org/licenses/>.

import io
import logging
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib import request

from PyQt6 import QtCore

from PIL import Image, ImageCms, ImageOps

Image.MAX_IMAGE_PIXELS = None  # Qt's allocation limit handles this

logger = logging.getLogger(__name__)

SRGB_PROFILE = ImageCms.createProfile("sRGB")


def _ensure_srgb(pil_img: Image.Image) -> Image.Image:
    """Convert CMYK or ICC-profiled images to sRGB."""
    icc = pil_img.info.get("icc_profile")

    if pil_img.mode == "CMYK":
        if icc:
            logger.debug("_ensure_srgb: CMYK with ICC profile, converting")
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.ImageCmsProfile(SRGB_PROFILE)
            result = ImageCms.profileToProfile(pil_img, src, dst, outputMode="RGB")
            assert result is not None
            return result
        else:
            logger.warning("CMYK image with no ICC profile, using naive conversion")
            return pil_img.convert("RGB")

    if icc and pil_img.mode in ("RGB", "RGBA"):
        logger.debug("_ensure_srgb: RGB/RGBA with ICC profile, converting")
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.ImageCmsProfile(SRGB_PROFILE)
            result = ImageCms.profileToProfile(
                pil_img, src, dst, outputMode=pil_img.mode
            )
            assert result is not None
            return result
        except ImageCms.PyCMSError:
            logger.debug("ICC profile conversion failed, using image as-is")

    logger.debug("_ensure_srgb: no conversion needed")
    return pil_img


def load_pil(path: Path) -> Image.Image | None:
    """Load image via Pillow with EXIF rotation and color management.
    Returns a PIL Image, or None if loading fails."""
    import time

    try:
        t0 = time.monotonic()
        pil_img = Image.open(path)
        original_format = pil_img.format
        t1 = time.monotonic()
        pil_img = ImageOps.exif_transpose(pil_img)
        if original_format and not pil_img.format:
            pil_img.format = original_format
        t2 = time.monotonic()
        pil_img = _ensure_srgb(pil_img)
        t3 = time.monotonic()
        pil_img.load()
        t4 = time.monotonic()
        logger.debug(
            f"load_pil: open={t1 - t0:.3f}s exif={t2 - t1:.3f}s srgb={t3 - t2:.3f}s load={t4 - t3:.3f}s"
        )
        return pil_img
    except Exception:
        logger.debug(f"Failed to load image: {path}")
        return None


def load_pil_from_source(path: Path | QtCore.QUrl) -> tuple[Image.Image | None, str]:
    """Load a PIL image from a local path or URL. Returns (pil_img, filename)."""
    if isinstance(path, Path):
        return (load_pil(path), str(path))
    if path.isLocalFile():
        local = Path(path.toLocalFile())
        return (load_pil(local), str(local))

    url = path.toEncoded().data().decode()
    try:
        imgdata = request.urlopen(url).read()
    except URLError as e:
        logger.debug(f"Downloading image failed: {e.reason}")
        return (None, url)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_file = Path(tmp) / "img"
        tmp_file.write_bytes(imgdata)
        return (load_pil(tmp_file), url)
