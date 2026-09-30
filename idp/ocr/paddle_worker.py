"""OCR entry point for the Python that has paddlepaddle.

The bench interpreter is newer than the published paddle wheels, so the
web process cannot import the engine. This script prints the text lines
in the shape extract_text already expects.
"""

from __future__ import annotations

import json
import os
import sys

# This CPU build crashes inside oneDNN. The reference kernel still reads text.
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_use_onednn", "0")
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("PADDLE_PDX_CPU_NUM_THREADS", "2")


def _lines(page) -> list:
	texts = list(page["rec_texts"] or [])
	scores = list(page["rec_scores"] or [])
	polys = page["rec_polys"] if "rec_polys" in page else []
	rows = []
	for index, text in enumerate(texts):
		score = float(scores[index]) if index < len(scores) else 0.0
		poly = polys[index] if index < len(polys) else []
		if hasattr(poly, "tolist"):
			poly = poly.tolist()
		rows.append([poly, [str(text), score]])
	return rows


def main() -> None:
	if len(sys.argv) != 4:
		print("usage: paddle_worker.py text <lang> <image>", file=sys.stderr)
		sys.exit(2)
	_mode, lang, path = sys.argv[1:]
	from paddleocr import PaddleOCR

	engine = PaddleOCR(lang=lang, use_textline_orientation=True)
	pages = [_lines(page) for page in engine.predict(path)]
	json.dump(pages, sys.stdout)


if __name__ == "__main__":
	main()
