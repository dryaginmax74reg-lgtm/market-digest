"""«Живое сообщение дня»: одно закреплённое сообщение в чате, в которое алерты дописывают события
(без звука — сообщение просто меняется). Срочное приходит отдельным сообщением со звуком.
Вечерний итог закрывает день и открепляет сообщение.

Состояние — в alerts_state.json (сайт публичный, поэтому туда пишется только то, что и так видно на рынке:
строки без пометок «⭐ ваш список»)."""
import os
import sys

from common import MONTHS, log, tg_call, tg_creds, tg_send

LIMIT = 3800


def _dry():
    return os.environ.get("DRY_RUN") == "1" or "--demo" in sys.argv


def render(st, now, closed=False):
    head = f"📌 <b>День · {now.day} {MONTHS[now.month - 1]}</b>"
    sub = ("<i>день завершён · итог — в вечернем сообщении</i>" if closed
           else f"<i>обновлено {now:%H:%M} МСК · срочное приходит отдельно со звуком</i>")
    lines = st.get("lines") or []
    body = "\n".join(lines) if lines else "Пока спокойно: сильных движений и событий нет."
    text = f"{head}\n{sub}\n\n{body}"
    cut = 0
    while len(text) > LIMIT and cut < len(lines) - 1:
        cut += 1
        body = f"<i>… ранее событий: {cut}</i>\n" + "\n".join(lines[cut:])
        text = f"{head}\n{sub}\n\n{body}"
    return text


def update(state, now, new_lines):
    """Дописать строки (уже в HTML) и обновить закреплённое сообщение. Ошибки — только в лог."""
    st = state.setdefault("live", {"id": None, "lines": []})
    st["lines"] = (st.get("lines") or []) + [f"<b>{now:%H:%M}</b> {ln}" for ln in new_lines]
    text = render(st, now)
    if _dry():
        print("[живое сообщение]\n" + text + "\n" + "-" * 40)
        return
    try:
        token, chat = tg_creds()
        if st.get("id"):
            ok, res = tg_call(token, "editMessageText", chat_id=chat, message_id=st["id"], text=text,
                              parse_mode="HTML", disable_web_page_preview="true")
            if ok or "not modified" in str(res):
                return
            log(f"[warn] живое сообщение не обновилось ({res}) — создаю новое")
        old = state.pop("unpin", None)
        if old:
            tg_call(token, "unpinChatMessage", chat_id=chat, message_id=old)
        msg = tg_send(token, chat, text, silent=True)
        st["id"] = msg.get("message_id")
        if st["id"]:
            ok, res = tg_call(token, "pinChatMessage", chat_id=chat, message_id=st["id"], disable_notification="true")
            if not ok:
                log(f"[warn] не закрепилось: {res}")
    except SystemExit:
        raise
    except Exception as ex:  # noqa: BLE001
        log(f"[warn] живое сообщение: {type(ex).__name__}: {ex}")


def close(state, now):
    """Вечером: пометить день завершённым и открепить."""
    st = state.get("live") or {}
    if not st.get("id"):
        return
    text = render(st, now, closed=True)
    if _dry():
        print("[живое сообщение — закрыто]\n" + text)
        return
    try:
        token, chat = tg_creds()
        tg_call(token, "editMessageText", chat_id=chat, message_id=st["id"], text=text, parse_mode="HTML",
                disable_web_page_preview="true")
        tg_call(token, "unpinChatMessage", chat_id=chat, message_id=st["id"])
        st["closed"] = True
    except SystemExit:
        raise
    except Exception as ex:  # noqa: BLE001
        log(f"[warn] живое сообщение (закрытие): {type(ex).__name__}: {ex}")
