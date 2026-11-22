"""Cất token Hugging Face (khi bạn chọn "Nhớ token") và API key của LLM soạn guideline trong Windows Credential Manager.
Không ghi ra file."""
from __future__ import annotations

SERVICE = "SAM3 CVAT Assist"   # tên cũ, giữ nguyên: đổi là mất token đã lưu trong Windows Credential Manager
USER = "huggingface-token"
LLM_USER = "guideline-llm-api-key"   # 0.9.3: key của Claude / máy chủ OpenAI dùng để soạn guideline


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


def get(user: str = USER) -> str:
    kr = _kr()
    try:
        return (kr.get_password(SERVICE, user) or "") if kr else ""
    except Exception:  # noqa: BLE001
        return ""


def set(token: str, user: str = USER) -> None:  # noqa: A001 - tên ngắn gọn trong module riêng
    kr = _kr()
    if kr:
        kr.set_password(SERVICE, user, token)


def delete(user: str = USER) -> None:
    kr = _kr()
    if kr:
        try:
            kr.delete_password(SERVICE, user)
        except Exception:  # noqa: BLE001
            pass
