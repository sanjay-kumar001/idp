# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Fail-open OCR preprocess: EXIF, long-edge cap, crop, deskew, CLAHE."""

from __future__ import annotations

import os
import tempfile
from typing import Any

from PIL import Image, ImageOps

from idp.core.logger import get_logger

logger = get_logger("idp.ocr.preprocess")

LONG_EDGE_CAP = 3000


def _save_temp(img: Image.Image) -> str:
	fd, tmp_path = tempfile.mkstemp(suffix=".png")
	os.close(fd)
	img.save(tmp_path, format="PNG")
	return tmp_path


def exif_transpose(img: Image.Image) -> Image.Image:
	return ImageOps.exif_transpose(img) or img


def downscale_long_edge(img: Image.Image, cap: int = LONG_EDGE_CAP) -> Image.Image:
	w, h = img.size
	long_edge = max(w, h)
	if long_edge <= cap:
		return img
	scale = cap / float(long_edge)
	return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def perspective_crop(img: Image.Image) -> Image.Image:
	"""Warp to document quad when OpenCV finds a confident contour; else passthrough."""
	try:
		import cv2
		import numpy as np
	except Exception:
		return img

	arr = np.array(img.convert("RGB"))
	gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
	blur = cv2.GaussianBlur(gray, (5, 5), 0)
	edged = cv2.Canny(blur, 50, 150)
	contours, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
	contours = sorted(contours, key=cv2.contourArea, reverse=True)[:8]
	quad = None
	area_img = float(arr.shape[0] * arr.shape[1])
	for cnt in contours:
		peri = cv2.arcLength(cnt, True)
		approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
		if len(approx) == 4 and cv2.contourArea(approx) > 0.2 * area_img:
			quad = approx.reshape(4, 2).astype("float32")
			break
	if quad is None:
		return img

	def order_points(pts: Any) -> Any:
		s = pts.sum(axis=1)
		diff = np.diff(pts, axis=1)
		rect = np.zeros((4, 2), dtype="float32")
		rect[0] = pts[np.argmin(s)]
		rect[2] = pts[np.argmax(s)]
		rect[1] = pts[np.argmin(diff)]
		rect[3] = pts[np.argmax(diff)]
		return rect

	rect = order_points(quad)
	(tl, tr, br, bl) = rect
	width_a = np.linalg.norm(br - bl)
	width_b = np.linalg.norm(tr - tl)
	max_w = int(max(width_a, width_b))
	height_a = np.linalg.norm(tr - br)
	height_b = np.linalg.norm(tl - bl)
	max_h = int(max(height_a, height_b))
	if max_w < 80 or max_h < 80:
		return img
	dst = np.array(
		[[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]], dtype="float32"
	)
	matrix = cv2.getPerspectiveTransform(rect, dst)
	warped = cv2.warpPerspective(arr, matrix, (max_w, max_h))
	return Image.fromarray(warped)


def deskew(img: Image.Image) -> Image.Image:
	try:
		import cv2
		import numpy as np
	except Exception:
		return img

	arr = np.array(img.convert("L"))
	edges = cv2.Canny(arr, 50, 150, apertureSize=3)
	lines = cv2.HoughLines(edges, 1, np.pi / 180, 200)
	if lines is None:
		return img
	angles = []
	for rho_theta in lines[:40]:
		_rho, theta = rho_theta[0]
		angle = (theta * 180.0 / np.pi) - 90.0
		if -45 < angle < 45:
			angles.append(angle)
	if not angles:
		return img
	median = float(np.median(angles))
	if abs(median) < 0.4:
		return img
	return img.rotate(-median, expand=True, fillcolor=255)


def clahe_contrast(img: Image.Image) -> Image.Image:
	try:
		import cv2
		import numpy as np
	except Exception:
		from PIL import ImageEnhance

		return ImageEnhance.Contrast(img.convert("L")).enhance(1.3)

	gray = np.array(img.convert("L"))
	clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
	out = clahe.apply(gray)
	return Image.fromarray(out)


def preprocess_pipeline(image_path: str) -> str:
	"""Run the ordered preprocess chain; never raise for step failures."""
	try:
		img = Image.open(image_path)
	except Exception as exc:
		from idp.core.exceptions import OCRError

		raise OCRError(f"Cannot open image '{image_path}': {exc}") from exc

	steps = (
		("exif_transpose", exif_transpose),
		("downscale", downscale_long_edge),
		("perspective_crop", perspective_crop),
		("deskew", deskew),
		("clahe", clahe_contrast),
	)
	current = img
	for name, fn in steps:
		try:
			current = fn(current)
		except Exception as exc:
			logger.warning("preprocess step %s failed; keeping prior image: %s", name, exc)

	if current.mode != "L":
		current = current.convert("L")
	return _save_temp(current)
