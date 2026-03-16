# This file is part of BeeRef.
#
# BeeRef is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# BeeRef is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with BeeRef.  If not, see <https://www.gnu.org/licenses/>.

import io
import logging
import os.path
import tempfile
from urllib.error import URLError
from urllib import request

from PyQt6 import QtGui

from PIL import Image, ImageCms, ImageOps


logger = logging.getLogger(__name__)

SRGB_PROFILE = ImageCms.createProfile("sRGB")


def _pil_to_qimage(pil_img):
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


def _ensure_srgb(pil_img):
    """Convert CMYK or ICC-profiled images to sRGB."""
    icc = pil_img.info.get("icc_profile")

    if pil_img.mode == "CMYK":
        if icc:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.ImageCmsProfile(SRGB_PROFILE)
            return ImageCms.profileToProfile(pil_img, src, dst, outputMode="RGB")
        else:
            logger.warning("CMYK image with no ICC profile, using naive conversion")
            return pil_img.convert("RGB")

    if icc and pil_img.mode in ("RGB", "RGBA"):
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.ImageCmsProfile(SRGB_PROFILE)
            return ImageCms.profileToProfile(pil_img, src, dst, outputMode=pil_img.mode)
        except ImageCms.PyCMSError:
            logger.debug("ICC profile conversion failed, using image as-is")

    return pil_img


def load_pil_image(path):
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


def load_image(path):
    if isinstance(path, str):
        path = os.path.normpath(path)
        return (load_pil_image(path), path)
    if path.isLocalFile():
        path = os.path.normpath(path.toLocalFile())
        return (load_pil_image(path), path)

    url = bytes(path.toEncoded()).decode()
    img = QtGui.QImage()
    try:
        imgdata = request.urlopen(url).read()
    except URLError as e:
        logger.debug(f"Downloading image failed: {e.reason}")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            fname = os.path.join(tmp, "img")
            with open(fname, "wb") as f:
                f.write(imgdata)
                logger.debug(f"Temporarily saved in: {fname}")
            img = load_pil_image(fname)
    return (img, url)
