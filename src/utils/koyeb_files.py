import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional, Dict

from ..systemLog import logger


# Koyeb Volume (или другой persistent FS) обычно монтируется в контейнер.
# Мы используем тот же относительный data/, но при деплое у вас может быть смонтировано
# в /data или аналогичный путь.
# Для надежности: base_dir по умолчанию указывает на data/.


def _sanitize_relative_path(rel_path: str) -> Path:
    """Sanitize relative path to avoid directory traversal.

    Разрешаем только буквы/цифры/._- и вложенность через /.
    """
    if not rel_path:
        raise ValueError("rel_path is empty")

    # Normalize separators
    rel_path = rel_path.replace('\\', '/')

    # Disallow absolute paths and traversal
    if rel_path.startswith('/') or rel_path.startswith('..') or '../' in rel_path or '..\\' in rel_path:
        raise ValueError("Invalid relative path")

    # Restrict characters a bit (keep it strict)
    if not re.match(r'^[A-Za-z0-9._\-/]+$', rel_path):
        raise ValueError("Invalid characters in relative path")

    p = Path(rel_path)
    # Extra checks
    if p.is_absolute() or any(part in ('.', '..') for part in p.parts):
        raise ValueError("Invalid relative path")

    return p


def ensure_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"[koyeb_files] ensure_dir failed: {path}: {e}")


def atomic_write_bytes(file_path: Path, data: bytes, *, encoding: Optional[str] = None) -> None:
    """Atomic write for bytes using temp file + replace.

    На persistent FS это обычно fast и предотвращает битые JSON при параллельных записях.
    """
    ensure_dir(file_path.parent)

    # Create temp file in same dir to make replace atomic
    tmp_fd: Optional[int] = None
    tmp_path: Optional[Path] = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=file_path.name + '.', suffix='.tmp', dir=str(file_path.parent))
        tmp_fd = fd
        tmp_path = Path(tmp_name)

        with os.fdopen(tmp_fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        os.replace(str(tmp_path), str(file_path))
    except Exception as e:
        logger.warning(f"[koyeb_files] atomic_write_bytes failed: {file_path}: {e}")
    finally:
        # If tmp exists and wasn't replaced, remove it
        try:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass


def atomic_write_text(file_path: Path, text: str, *, encoding: str = 'utf-8') -> None:
    atomic_write_bytes(file_path, text.encode(encoding), encoding=encoding)


def write_json_atomic(file_path: Path, obj: Any) -> None:
    try:
        text = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
        atomic_write_text(file_path, text)
    except Exception as e:
        logger.warning(f"[koyeb_files] write_json_atomic failed: {file_path}: {e}")


def create_and_write_file(
    base_dir: str | Path,
    rel_path: str,
    content: str | bytes | Any,
    *,
    encoding: str = 'utf-8',
    content_type: str = 'text',
) -> Path:
    """Create directories and write content to file.

    content_type:
    - 'text' -> content is str
    - 'bytes' -> content is bytes
    - 'json' -> content is python obj, serialized to json
    """
    base_dir_p = Path(base_dir)
    rel_p = _sanitize_relative_path(rel_path)
    file_path = base_dir_p / rel_p

    if content_type == 'text':
        if not isinstance(content, str):
            content = str(content)
        atomic_write_text(file_path, content, encoding=encoding)
    elif content_type == 'bytes':
        if not isinstance(content, (bytes, bytearray)):
            raise ValueError('content must be bytes when content_type=bytes')
        atomic_write_bytes(file_path, bytes(content))
    elif content_type == 'json':
        write_json_atomic(file_path, content)
    else:
        raise ValueError(f"Unknown content_type: {content_type}")

    return file_path


def delete_file(base_dir: str | Path, rel_path: str) -> bool:
    base_dir_p = Path(base_dir)
    rel_p = _sanitize_relative_path(rel_path)
    file_path = base_dir_p / rel_p

    try:
        if file_path.exists():
            file_path.unlink()
            return True
        return False
    except Exception as e:
        logger.warning(f"[koyeb_files] delete_file failed: {file_path}: {e}")
        return False

