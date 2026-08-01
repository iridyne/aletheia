"""Decode the output of Windows console tools (schtasks, ...) correctly.

`subprocess.run(text=True)` decodes a child's bytes with Python's locale
encoding. Under Python's UTF-8 mode — the default for this runtime — that is
UTF-8, while `schtasks` writes the *console output code page* (cp936 on a
Simplified-Chinese Windows). The mismatch turned schtasks' real message
"错误: 系统找不到指定的文件。" into mojibake, so the localized "not found"
markers in the caller's classifier never matched: deleting an already-absent
task reported a hard `schtasks_error` instead of an idempotent success, and the
`detail` shown to the operator was unreadable.

Decode order: byte-order mark → strict UTF-8 → strict console/OEM code page →
console code page with replacement. Strict UTF-8 first is safe because legacy
double-byte text is almost never a valid UTF-8 sequence, whereas cp936 accepts
nearly any byte string and would silently mojibake genuinely UTF-8 output.
"""

import locale
import sys

# (BOM, codec) pairs — schtasks /Query /XML declares encoding="UTF-16" in its
# text but emits code-page bytes when redirected; other tools do emit UTF-16.
_BOMS: tuple[tuple[bytes, str], ...] = (
    # "utf-16" and "utf-8-sig" consume the mark itself instead of leaving a
    # stray U+FEFF at the start of the first parsed field.
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
)


def console_encoding() -> str | None:
    """Return the codec a Windows console tool writes, or None off Windows.

    Prefers the live console output code page, falls back to the process OEM
    code page, then to the ANSI code page via the `mbcs` alias.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        for code_page in (kernel32.GetConsoleOutputCP(), kernel32.GetOEMCP()):
            if code_page:
                return f"cp{int(code_page)}"
    except Exception:
        pass
    return "mbcs"


def _system_code_page_encodings() -> tuple[str, ...]:
    """Return code pages used by hidden Windows child processes."""
    if sys.platform != "win32":
        return ()
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        pages = (kernel32.GetOEMCP(), kernel32.GetACP())
        return tuple(dict.fromkeys(f"cp{int(page)}" for page in pages if page))
    except Exception:
        return ()


def decode_console(data: bytes | str | None) -> str:
    """Decode console-tool bytes to text without mangling localized messages.

    Passes str through unchanged so callers can keep using stubbed/text streams.
    Never raises: the last attempt replaces undecodable bytes.
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return data

    for bom, codec in _BOMS:
        if data.startswith(bom):
            return data.decode(codec, errors="replace")

    fallback = console_encoding() or locale.getpreferredencoding(False) or "utf-8"
    codecs = tuple(dict.fromkeys(("utf-8", fallback, *_system_code_page_encodings())))
    for codec in codecs:
        try:
            return data.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode(fallback, errors="replace")
