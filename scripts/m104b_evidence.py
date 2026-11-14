"""Keep accepted evidence snapshots immutable when existing tests reuse their paths."""
from contextlib import contextmanager
from pathlib import Path

@contextmanager
def preserve_accepted_evidence():
    docs=Path(__file__).resolve().parents[1]/'Docs'
    saved={p:p.read_bytes() for pattern in ('M1-03b*','M1-04a*') for p in docs.glob(pattern) if p.is_file()}
    try:yield
    finally:
        for path,content in saved.items():
            if path.read_bytes()!=content:path.write_bytes(content)
