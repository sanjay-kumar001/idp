# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

"""Concrete extractor implementations for every supported file format.

Each class extends :class:`BaseExtractor` and returns a unified
:class:`ExtractionResult`.
"""

import csv
import io
import os
from typing import ClassVar

from idp.core.exceptions import ExtractionError
from idp.core.logger import get_logger
from idp.extractors.base import BaseExtractor, ExtractionResult

logger = get_logger("idp.extractors")

# Phase 28 G3 — vision-gating heuristic.  We escalate to OCR (a vision-call
# proxy on the cost side) only when the pypdf extract is genuinely sparse.
# Defaults match the roadmap; both are overridable from IDP Settings.
_DEFAULT_VISION_TEXT_THRESHOLD = 200  # chars
_DEFAULT_ALPHANUMERIC_RATIO = 0.05


def _alphanumeric_ratio(text: str) -> float:
	"""Fraction of *text* that is letters or digits.

	Pure white-space / line-noise pypdf dumps from scanned PDFs score
	near zero, while real text (even tables) easily clears 0.05.
	"""

	if not text:
		return 0.0
	total = len(text)
	if total == 0:
		return 0.0
	hits = sum(1 for ch in text if ch.isalnum())
	return hits / total


def _read_vision_gating_settings() -> tuple[int, float]:
	"""Return ``(text_threshold_chars, min_alpha_ratio)`` from IDP Settings.

	Returns the documented defaults when Frappe is unavailable or the
	Phase 28 fields are missing.  Never raises.
	"""

	try:
		import frappe
	except ImportError:
		return _DEFAULT_VISION_TEXT_THRESHOLD, _DEFAULT_ALPHANUMERIC_RATIO
	try:
		threshold = int(
			frappe.db.get_single_value("IDP Settings", "vision_text_threshold")
			or _DEFAULT_VISION_TEXT_THRESHOLD
		)
	except Exception:
		threshold = _DEFAULT_VISION_TEXT_THRESHOLD
	# Ratio is not yet a configurable field — kept as a constant so the
	# rollback flag (IDP Settings.page_pre_pass_enabled / settings off)
	# keeps it stable across upgrades.
	return max(threshold, 0), _DEFAULT_ALPHANUMERIC_RATIO


def needs_vision_fallback(text: str, page_count: int) -> bool:
	"""Phase 28 G3 — decide whether to escalate to OCR / vision.

	Returns True when:
	* Total extracted chars are below ``IDP Settings.vision_text_threshold``
	  (default 200), OR
	* The alphanumeric ratio of the pypdf dump is below 0.05 (so the
	  text exists but is junk — page furniture, ligature noise).

	The historical "50 chars/page" heuristic is preserved as a third
	criterion so existing extractions don't regress.
	"""

	threshold, min_alpha = _read_vision_gating_settings()
	stripped = (text or "").strip()
	if len(stripped) < threshold:
		return True
	if _alphanumeric_ratio(stripped) < min_alpha:
		return True
	# Legacy guard — sparse text per page (scanned PDFs with stray text
	# in the header).  Kept so callers on older settings see no change.
	if len(stripped) < (50 * max(page_count, 1)):
		return True
	return False


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


class PDFExtractor(BaseExtractor):
	"""Extract from PDF — text-based or scanned.

	Strategy:
	1. Attempt text extraction via pypdf.
	2. If text is empty / sparse (scanned PDF) → fall back to PaddleOCR.
	3. Extract tables via PaddleOCR PPStructure.
	4. Combine into ExtractionResult.
	"""

	_MIME_TYPES: ClassVar[set[str]] = {"application/pdf"}

	def supports_mime_type(self, mime_type: str) -> bool:
		return mime_type in self._MIME_TYPES

	def extract(self, file_path: str, **kwargs) -> ExtractionResult:
		lang = kwargs.get("lang", "en")
		file_url = kwargs.get("file_url", file_path)

		text = self._extract_text_pypdf(file_path)
		page_count = self._get_page_count(file_path)
		ocr_results = None
		tables: list[list[list[str]]] = []
		confidence: float | None = None

		# Phase 28 G3 — vision-gating heuristic: escalate to OCR only when
		# the pypdf extract is below the configured char threshold OR has
		# a degenerately low alphanumeric ratio (line-noise scans).
		is_scanned = needs_vision_fallback(text, page_count)

		if is_scanned:
			logger.info(f"PDF appears scanned, falling back to OCR | file={file_url}")
			try:
				from idp.ocr.engine import process_pdf

				ocr_results = process_pdf(file_path, lang=lang)
			except Exception:
				logger.warning(f"OCR failed for a sparse PDF, keeping the text layer | file={file_url}")
				if not (text or "").strip():
					raise
			else:
				# Rebuild text from OCR results. A blank OCR pass must not
				# wipe a text layer the PDF already had.
				text_parts: list[str] = []
				all_tables: list[list[list[str]]] = []
				confidences: list[float] = []

				for page in ocr_results:
					page_text = "\n".join(b.text for b in page.text_blocks)
					text_parts.append(page_text)
					for tbl in page.tables:
						all_tables.append(tbl.rows)
					if page.text_blocks:
						confidences.append(page.average_confidence)

				ocr_text = "\n\n".join(text_parts).strip()
				if ocr_text or not (text or "").strip():
					text = "\n\n".join(text_parts)
					tables = all_tables
					confidence = sum(confidences) / len(confidences) if confidences else None
		else:
			# The page already has a text layer. Rendering it and running
			# Paddle just to look for tables reloads the model and holds the
			# request past the web timeout. The text layer is the read.
			pass

		return ExtractionResult(
			content_type="mixed" if tables else "text",
			text=text or None,
			tables=tables or None,
			metadata={
				"file_name": os.path.basename(file_path),
				"mime_type": "application/pdf",
				"page_count": page_count,
				"is_scanned": is_scanned,
			},
			ocr_results=ocr_results,
			confidence=confidence,
		)

	def _extract_text_pypdf(self, file_path: str) -> str:
		"""Extract text from all pages using pypdf."""
		try:
			from pypdf import PdfReader

			reader = PdfReader(file_path)
			parts: list[str] = []
			for page in reader.pages:
				page_text = page.extract_text() or ""
				parts.append(page_text)
			return "\n\n".join(parts)
		except Exception as exc:
			logger.warning(f"pypdf text extraction failed: {exc}")
			return ""

	def _get_page_count(self, file_path: str) -> int:
		try:
			from pypdf import PdfReader

			return len(PdfReader(file_path).pages)
		except Exception:
			return 0


# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------


class ImageExtractor(BaseExtractor):
	"""Extract from images (PNG, JPEG, TIFF, WebP) via PaddleOCR.

	1. Pre-process image (contrast, grayscale, upscale).
	2. Run PaddleOCR text extraction.
	3. Run PaddleOCR table extraction.
	4. Return text + tables.
	"""

	_MIME_TYPES: ClassVar[set[str]] = {"image/png", "image/jpeg", "image/webp", "image/tiff"}

	def supports_mime_type(self, mime_type: str) -> bool:
		return mime_type in self._MIME_TYPES

	def extract(self, file_path: str, **kwargs) -> ExtractionResult:
		lang = kwargs.get("lang", "en")

		from idp.ocr.engine import extract_table, extract_text, preprocess_image

		preprocessed: str | None = None
		try:
			preprocessed = preprocess_image(file_path)
			target = preprocessed

			# Text
			text_dicts = extract_text(target, lang=lang)
			full_text = "\n".join(d["text"] for d in text_dicts)
			confidences = [d["confidence"] for d in text_dicts]
			avg_confidence = sum(confidences) / len(confidences) if confidences else None

			# Tables
			table_objs = extract_table(target, lang=lang)
			tables = [t.rows for t in table_objs] if table_objs else None

			return ExtractionResult(
				content_type="mixed" if tables else "image",
				text=full_text or None,
				tables=tables,
				metadata={
					"file_name": os.path.basename(file_path),
					"mime_type": kwargs.get("mime_type", "image/png"),
					"page_count": 1,
				},
				confidence=avg_confidence,
			)
		except Exception as exc:
			raise ExtractionError(f"Image extraction failed: {exc}", details={"file": file_path})
		finally:
			if preprocessed:
				from idp.ocr.engine import _safe_remove

				_safe_remove(preprocessed)


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------


class ExcelExtractor(BaseExtractor):
	"""Extract from Excel (XLSX, XLS) via openpyxl.

	1. Read all sheets.
	2. Parse cell-by-cell: detect header row, data rows.
	3. Handle merged cells (use top-left value).
	4. Return as tabular ExtractionResult.
	"""

	_MIME_TYPES: ClassVar[set[str]] = {
		"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		"application/vnd.ms-excel",
	}

	def supports_mime_type(self, mime_type: str) -> bool:
		return mime_type in self._MIME_TYPES

	def extract(self, file_path: str, **kwargs) -> ExtractionResult:
		try:
			from openpyxl import load_workbook
		except ImportError:
			raise ExtractionError("openpyxl is not installed. Install with: pip install openpyxl")

		try:
			wb = load_workbook(file_path, read_only=True, data_only=True)
		except Exception as exc:
			raise ExtractionError(f"Cannot open Excel file: {exc}", details={"file": file_path})

		all_tables: list[list[list[str]]] = []
		text_parts: list[str] = []

		for sheet_name in wb.sheetnames:
			ws = wb[sheet_name]
			rows: list[list[str]] = []
			for row in ws.iter_rows(values_only=True):
				cells = [str(cell) if cell is not None else "" for cell in row]
				# Skip completely empty rows
				if any(c.strip() for c in cells):
					rows.append(cells)

			if rows:
				all_tables.append(rows)
				# Build a textual representation for mapping
				for row in rows:
					text_parts.append("\t".join(row))

		wb.close()

		return ExtractionResult(
			content_type="tabular",
			text="\n".join(text_parts) or None,
			tables=all_tables or None,
			metadata={
				"file_name": os.path.basename(file_path),
				"mime_type": kwargs.get(
					"mime_type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
				),
				"sheet_count": len(wb.sheetnames) if hasattr(wb, "sheetnames") else 0,
			},
		)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


class CSVExtractor(BaseExtractor):
	"""Extract from CSV files using stdlib csv.

	1. Auto-detect delimiter (comma, semicolon, tab).
	2. Auto-detect encoding (utf-8, latin-1, cp1252).
	3. Parse header row + data rows.
	4. Return as tabular ExtractionResult.
	"""

	_MIME_TYPES: ClassVar[set[str]] = {"text/csv"}

	def supports_mime_type(self, mime_type: str) -> bool:
		return mime_type in self._MIME_TYPES

	def extract(self, file_path: str, **kwargs) -> ExtractionResult:
		content = self._read_file(file_path)
		dialect = self._detect_dialect(content)

		reader = csv.reader(io.StringIO(content), dialect=dialect)
		rows: list[list[str]] = []
		for row in reader:
			if any(cell.strip() for cell in row):
				rows.append(row)

		text = "\n".join("\t".join(row) for row in rows) if rows else None

		return ExtractionResult(
			content_type="tabular",
			text=text,
			tables=[rows] if rows else None,
			metadata={
				"file_name": os.path.basename(file_path),
				"mime_type": "text/csv",
				"row_count": len(rows),
			},
		)

	def _read_file(self, file_path: str) -> str:
		"""Read file, trying multiple encodings."""
		for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
			try:
				with open(file_path, encoding=encoding) as f:
					return f.read()
			except (UnicodeDecodeError, UnicodeError):
				continue
		raise ExtractionError(f"Cannot decode CSV file: {file_path}")

	def _detect_dialect(self, content: str) -> csv.Dialect:
		"""Sniff the CSV dialect from the first 8 KB."""
		try:
			sample = content[:8192]
			return csv.Sniffer().sniff(sample, delimiters=",;\t|")
		except csv.Error:
			# Default to comma-delimited
			return csv.excel


# ---------------------------------------------------------------------------
# Word (DOCX)
# ---------------------------------------------------------------------------


class DocxExtractor(BaseExtractor):
	"""Extract from Word documents (DOCX) via python-docx.

	1. Extract paragraphs (with style metadata: heading, body, etc.).
	2. Extract tables (rows x columns).
	3. Return text + tables as ExtractionResult.
	"""

	_MIME_TYPES: ClassVar[set[str]] = {
		"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	}

	def supports_mime_type(self, mime_type: str) -> bool:
		return mime_type in self._MIME_TYPES

	def extract(self, file_path: str, **kwargs) -> ExtractionResult:
		try:
			from docx import Document
		except ImportError:
			raise ExtractionError("python-docx is not installed. Install with: pip install python-docx")

		try:
			doc = Document(file_path)
		except Exception as exc:
			raise ExtractionError(f"Cannot open DOCX file: {exc}", details={"file": file_path})

		# Paragraphs
		text_parts: list[str] = []
		for para in doc.paragraphs:
			stripped = para.text.strip()
			if stripped:
				text_parts.append(stripped)

		# Tables
		all_tables: list[list[list[str]]] = []
		for table in doc.tables:
			rows: list[list[str]] = []
			for row in table.rows:
				cells = [cell.text.strip() for cell in row.cells]
				rows.append(cells)
			if rows:
				all_tables.append(rows)

		return ExtractionResult(
			content_type="mixed" if all_tables else "text",
			text="\n".join(text_parts) or None,
			tables=all_tables or None,
			metadata={
				"file_name": os.path.basename(file_path),
				"mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
				"paragraph_count": len(text_parts),
				"table_count": len(all_tables),
			},
		)
