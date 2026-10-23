"""Preprocess unit tests (no Paddle).

Callers: python -m unittest idp.tests.test_preprocess.
User: "Implement the plan as specified, it is attached for your reference."
"""

from __future__ import annotations

import os
import tempfile
import unittest

from PIL import Image


class TestPreprocess(unittest.TestCase):
	def _tmp_png(self, size=(800, 600), color=200) -> str:
		fd, path = tempfile.mkstemp(suffix=".png")
		os.close(fd)
		Image.new("RGB", size, (color, color, color)).save(path)
		return path

	def test_downscale_huge_image(self):
		from idp.ocr.preprocess import LONG_EDGE_CAP, downscale_long_edge

		img = Image.new("RGB", (6000, 4000), (255, 255, 255))
		out = downscale_long_edge(img)
		self.assertLessEqual(max(out.size), LONG_EDGE_CAP)

	def test_perspective_fallback_on_blank(self):
		from idp.ocr.preprocess import perspective_crop

		img = Image.new("RGB", (400, 400), (240, 240, 240))
		out = perspective_crop(img)
		self.assertEqual(out.size, img.size)

	def test_pipeline_returns_temp_file(self):
		from idp.ocr.preprocess import preprocess_pipeline

		src = self._tmp_png()
		try:
			out = preprocess_pipeline(src)
			self.assertTrue(os.path.isfile(out))
			os.unlink(out)
		finally:
			os.unlink(src)

	def test_engine_preprocess_fail_open(self):
		from idp.ocr.engine import preprocess_image

		src = self._tmp_png(size=(200, 200))
		try:
			out = preprocess_image(src)
			self.assertTrue(os.path.isfile(out))
			os.unlink(out)
		finally:
			os.unlink(src)


if __name__ == "__main__":
	unittest.main()
