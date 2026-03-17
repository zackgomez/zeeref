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

from PyQt6 import QtCore, QtGui

from PIL import Image, ImageCms, ImageOps

Image.MAX_IMAGE_PIXELS = None  # Qt's allocation limit handles this

logger = logging.getLogger(__name__)

SRGB_PROFILE = ImageCms.createProfile("sRGB")


def _pil_to_qimage(pil_img: Image.Image) -> QtGui.QImage:
    """Convert a PIL Image to a QImage."""
    if pil_img.mode == "RGBA":
        fmt = QtGui.QImage.Format.Format_RGBA8888
        channels = 4
    else:
        pil_img = pil_img.convert("RGB")
        fmt = QtGui.QImage.Format.Format_RGB888
        channels = 3

    data = pil_img.tobytes()
    stride = channels * pil_img.width
    qimg = QtGui.QImage(data, pil_img.width, pil_img.height, stride, fmt)
    return qimg.copy()  # detach from buffer


def _ensure_srgb(pil_img: Image.Image) -> Image.Image:
    """Convert CMYK or ICC-profiled images to sRGB."""
    icc = pil_img.info.get("icc_profile")

    if pil_img.mode == "CMYK":
        if icc:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.ImageCmsProfile(SRGB_PROFILE)
            result = ImageCms.profileToProfile(pil_img, src, dst, outputMode="RGB")
            assert result is not None
            return result
        else:
            logger.warning("CMYK image with no ICC profile, using naive conversion")
            return pil_img.convert("RGB")

    if icc and pil_img.mode in ("RGB", "RGBA"):
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

    return pil_img


def load_pil_image(path: Path) -> QtGui.QImage:
    """Load image via Pillow with EXIF rotation and color management.
    Returns a QImage (null if loading fails)."""
    try:
        pil_img = Image.open(path)
        pil_img = ImageOps.exif_transpose(pil_img)
        pil_img = _ensure_srgb(pil_img)
        return _pil_to_qimage(pil_img)
    except Exception:
        logger.debug(f"Failed to load image: {path}")
        return QtGui.QImage()


def load_image(path: Path | QtCore.QUrl) -> tuple[QtGui.QImage, str]:
    if isinstance(path, Path):
        return (load_pil_image(path), str(path))
    if path.isLocalFile():
        local = Path(path.toLocalFile())
        return (load_pil_image(local), str(local))

    url = path.toEncoded().data().decode()
    img = QtGui.QImage()
    try:
        imgdata = request.urlopen(url).read()
    except URLError as e:
        logger.debug(f"Downloading image failed: {e.reason}")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_file = Path(tmp) / "img"
            tmp_file.write_bytes(imgdata)
            logger.debug(f"Temporarily saved in: {tmp_file}")
            img = load_pil_image(tmp_file)
    return (img, url)
