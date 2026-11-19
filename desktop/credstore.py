"""Cất token Hugging Face trong Windows Credential Manager (chỉ khi bạn chọn "Nhớ token"). Không ghi ra file."""
from __future__ import annotations

SERVICE = "SAM3 CVAT Assist"
USER = "huggingface-token"


def _kr():
    try:
        import keyring
        from keyring.backends import fail

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:  # noqa: BLE001 - không có keyring thì coi như không nhớ được
        return None


def available() -> bool:
    return _kr() is not None


def get() -> str:
    kr = _kr()
    try:
        return (kr.get_password(SERVICE, USER) or "") if kr else ""
    except Exception:  # noqa: BLE001
        return ""


def set(token: str) -> None:  # noqa: A001 - tên ngắn gọn trong module riêng
    kr = _kr()
    if kr:
        kr.set_password(SERVICE, USER, token)


def delete() -> None:
    kr = _kr()
    if kr:
        try:
            kr.delete_password(SERVICE, USER)
        except Exception:  # noqa: BLE001
            pass
