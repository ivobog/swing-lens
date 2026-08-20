from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.services.ceri.sec.provider import SecGuidanceDocument


class SecDocumentContentCache:
    """Durable, content-addressed SEC filing-body cache.

    Document/extraction identity remains authoritative in PostgreSQL.  This
    cache only preserves the public filing body so a processor-signature
    change can re-extract known documents without another EDGAR download.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root) / "ceri" / "sec-documents"

    def load(
        self,
        document: SecGuidanceDocument,
        *,
        expected_hash: str | None = None,
    ) -> str | None:
        path = self.path_for(document)
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                content = handle.read()
        except (FileNotFoundError, OSError, UnicodeError):
            return None
        if expected_hash and hashlib.sha256(content.encode("utf-8")).hexdigest() != expected_hash:
            return None
        return content

    def store(self, document: SecGuidanceDocument, content: str) -> Path:
        path = self.path_for(document)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with NamedTemporaryFile(
                mode="wb",
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as raw_handle:
                temporary_name = raw_handle.name
                with gzip.GzipFile(fileobj=raw_handle, mode="wb", mtime=0) as compressed:
                    compressed.write(content.encode("utf-8"))
            os.replace(temporary_name, path)
            temporary_name = None
            return path
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

    def path_for(self, document: SecGuidanceDocument) -> Path:
        identity = "\n".join(
            (
                str(document.cik).zfill(10),
                document.accession_number.strip(),
                document.document_name.strip(),
            )
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / f"{digest}.txt.gz"
