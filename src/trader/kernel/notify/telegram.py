"""텔레그램 발신·수신. 토큰·chat_id 가 없으면 NullNotifier(항상 성공)·수신기 None.

토큰이 URL 에 들어가므로 URL·예외 메시지를 로그에 남기지 않는다.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

import httpx

from trader.kernel.config.secrets import Secrets
from trader.kernel.platform.logging_setup import quiet_httpx

logger = logging.getLogger("trader.notify")

MAX_LEN = 4000


@dataclass
class Message:
    """종류·계좌·버튼이 붙은 알림. buttons 는 (라벨, callback_data) 행 목록(렌더는 텔레그램 몫)."""

    text: str
    kind: str = "info"
    account_id: str | None = None
    buttons: list[list[tuple[str, str]]] | None = None


class Notifier(Protocol):
    def send(self, text: str, *, escape: bool = True) -> bool: ...


def send(notifier: Notifier, msg: Message) -> bool:
    """`send_message` 가 있으면 그것으로, 없는 옛 알림기·가짜는 평문 `send` 로(버튼은 버린다)."""
    fn = getattr(notifier, "send_message", None)
    if fn is not None:
        return fn(msg)
    return notifier.send(msg.text)


class NullNotifier:
    """설정이 없을 때 쓰는 빈 알림기. 항상 성공으로 돌려준다."""

    def send_message(self, msg: Message) -> bool:
        _check_buttons(msg.buttons)
        return self.send(msg.text)

    def send(self, text: str, *, escape: bool = True) -> bool:
        logger.debug("알림 생략(텔레그램 미설정): %s", text[:80])
        return True


CALLBACK_DATA_MAX = 64  # 텔레그램 callback_data 한도(UTF-8 바이트). 넘으면 BUTTON_DATA_INVALID


def _check_buttons(buttons: list[list[tuple[str, str]]] | None) -> None:
    """callback_data 가 한도를 넘으면 ValueError. 보내기·보관 전에 호출부에서 실패시킨다 —
    거부될 메시지가 outbox 에 남아 뒤 알림의 재전송을 막지 않게(T4-R1)."""
    for row in buttons or []:
        for _label, data in row:
            if len(data.encode("utf-8")) > CALLBACK_DATA_MAX:
                raise ValueError(
                    f"텔레그램 버튼 데이터는 {CALLBACK_DATA_MAX}바이트 이하여야 합니다: {data!r}"
                )


def _markup(buttons: list[list[tuple[str, str]]] | None) -> dict | None:
    """(라벨, callback_data) 행 목록 → 텔레그램 reply_markup. 버튼이 없으면 None."""
    rows = [
        [{"text": label, "callback_data": data} for label, data in row] for row in buttons or []
    ]
    rows = [r for r in rows if r]
    return {"inline_keyboard": rows} if rows else None


def _chunks(text: str, size: int, *, escape: bool) -> list[str]:
    """원문을 잘라 청크마다 이스케이프한다. 이스케이프 후 길이가 size 를 넘지 않게 하고,
    엔티티(&lt; 등)는 원문 단위로 자르므로 중간에서 끊기지 않는다. 줄바꿈 경계를 우선한다."""
    conv = html.escape if escape else (lambda s: s)
    out: list[str] = []
    while text:
        if len(conv(text)) <= size:
            out.append(conv(text))
            break
        # 이스케이프 후 길이가 size 이하인 최대 원문 접두사를 찾는다 (이진 탐색)
        lo, hi = 1, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(conv(text[:mid])) <= size:
                lo = mid
            else:
                hi = mid - 1
        cut = text.rfind("\n", 0, lo)
        if cut <= 0:
            cut = lo
        out.append(conv(text[:cut]))
        text = text[cut:].lstrip("\n")
    return out


OUTBOX_TTL = timedelta(hours=24)  # 이보다 오래 못 보낸 알림은 버린다(낡은 경보가 몰려오지 않게)
OUTBOX_FLUSH_MAX = 10  # 한 번에 다시 보내는 건수(429 를 부르지 않게)


class TelegramNotifier:
    """outbox 가 있으면 보내지 못한 청크를 거기 적어 두고 `flush_outbox` 가 다시 보낸다(전체 검토
    2026-10-27: 429·네트워크 오류면 [주문 상태 미확인]·[중요 공시] 같은 알림이 그대로 사라졌다)."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        http: httpx.Client | None = None,
        outbox: Path | None = None,
        record: Callable[[dict], None] | None = None,
    ) -> None:
        quiet_httpx()
        self._record = record
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._http = http or httpx.Client(timeout=10)
        self._outbox = outbox

    def _post(self, chunk: str, reply_markup: dict | None = None) -> bool:
        data = {"chat_id": self._chat_id, "text": chunk, "parse_mode": "HTML"}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        try:
            resp = self._http.post(self._url, data=data)
        except httpx.HTTPError as e:
            logger.warning("텔레그램 전송 실패: %s", type(e).__name__)
            return False
        if resp.status_code != 200:
            logger.warning("텔레그램 전송 거부: HTTP %s", resp.status_code)
            return False
        return True

    def send(self, text: str, *, escape: bool = True) -> bool:
        """4,000자 단위로 나눠 보낸다(기본 HTML 이스케이프). 전송 실패분은 outbox 에 보관하고
        보관에 성공했으면 True(재전송은 `flush_outbox` 몫 — 호출부가 다시 보내면 중복, 2-T1),
        보관함이 없거나 보관도 실패하면 False + 경고."""
        return self._deliver(Message(text), escape=escape)

    def send_message(self, msg: Message) -> bool:
        """종류·계좌를 붙여 보내고 기록한다. buttons 는 첫 청크의 인라인 키보드로 붙인다.
        callback_data 가 64바이트를 넘으면 아무것도 보내지 않고 ValueError."""
        _check_buttons(msg.buttons)
        return self._deliver(msg, escape=True)

    def _deliver(self, msg: Message, *, escape: bool) -> bool:
        text = msg.text
        if not text or not text.strip():
            return True
        chunks = _chunks(text, MAX_LEN, escape=escape)
        markup = _markup(msg.buttons)
        delivered = True
        result = True
        for i, chunk in enumerate(chunks):
            first_markup = markup if i == 0 else None
            if not self._post(chunk, first_markup):
                delivered = False
                # 첫 청크가 실패했을 때만 버튼을 보관한다(이미 나간 청크에 버튼이 붙어 있음)
                result = self._queue(chunks[i:], msg, reply_markup=first_markup)
                break
        self._emit(msg, delivered)
        return result

    def _emit(self, msg: Message, ok: bool) -> None:
        """기록 훅. 실제 전달 여부(ok)를 남기고, 훅 오류는 발신에 영향을 주지 않는다."""
        if self._record is None:
            return
        try:
            self._record(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "kind": msg.kind,
                    "account_id": msg.account_id,
                    "text": msg.text,
                    "channel": "telegram",
                    "ok": ok,
                }
            )
        except Exception as e:  # noqa: BLE001 — 기록이 발신을 깨면 안 된다
            logger.warning("알림 기록 실패: %s", type(e).__name__)

    def _queue(
        self, chunks: list[str], msg: Message | None = None, *, reply_markup: dict | None = None
    ) -> bool:
        """보관 성공이면 True. reply_markup 은 첫 보관 청크에 달아 재전송 때도 버튼을 유지한다."""
        if self._outbox is None:
            return False
        import uuid

        from trader.kernel.platform.filelock import locked

        try:
            self._outbox.parent.mkdir(parents=True, exist_ok=True)
            with locked(self._outbox.with_name(self._outbox.name + ".lock"), timeout=30.0):
                with self._outbox.open("a", encoding="utf-8") as f:
                    at = datetime.now(UTC).isoformat()
                    for n, c in enumerate(chunks):
                        row = {"id": uuid.uuid4().hex, "at": at, "chunk": c}
                        if n == 0 and reply_markup:
                            row["reply_markup"] = reply_markup
                        if msg is not None:
                            # 기록은 메시지당 1건: 첫 청크만 원문(text)을 달고 나머지는 기록 생략
                            row["kind"] = msg.kind
                            row["account_id"] = msg.account_id
                            if n == 0:
                                row["text"] = msg.text
                            else:
                                row["no_record"] = True
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except (OSError, TimeoutError) as e:
            logger.warning("알림 보관 실패: %s", type(e).__name__)
            return False
        return True

    def _read_outbox(self) -> list[dict]:
        out = []
        if not self._outbox or not self._outbox.exists():
            return out
        for line in self._outbox.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def flush_outbox(self, *, now: datetime | None = None) -> dict:
        """보관된 청크를 오래된 순으로 다시 보낸다(최대 OUTBOX_FLUSH_MAX). 첫 실패에서 멈춘다.
        파일은 보낸 뒤에야 지운다 — 재전송 도중 데몬이 죽어도 남은 항목이 사라지지 않게(적어도 한
        번 전달, 드물게 중복). 네트워크 전송은 잠금 밖, 24시간 넘은 것은 버린다(Codex 리뷰)."""
        if self._outbox is None or not self._outbox.exists():
            return {"sent": 0, "left": 0}
        from trader.kernel.platform.filelock import locked

        now = now or datetime.now(UTC)
        lock = self._outbox.with_name(self._outbox.name + ".lock")
        with locked(lock, timeout=5.0):
            items = self._read_outbox()
        sent_ids: set[str] = set()
        stale_ids: set[str] = set()
        for it in items:
            key = str(it.get("id") or it.get("at"))
            try:
                old = now - datetime.fromisoformat(str(it.get("at"))) > OUTBOX_TTL
            except ValueError:
                old = True
            if old:
                stale_ids.add(key)
                continue
            if len(sent_ids) >= OUTBOX_FLUSH_MAX:
                break
            markup = it.get("reply_markup")
            if not self._post(
                str(it.get("chunk") or ""), markup if isinstance(markup, dict) else None
            ):
                break
            sent_ids.add(key)
            # 늦게라도 나간 알림은 새 기록으로 남긴다(원래 ok=False 행은 그대로 — 이력)
            if not it.get("no_record"):
                self._emit(
                    Message(
                        str(it.get("text") or it.get("chunk") or ""),
                        kind=str(it.get("kind") or "info"),
                        account_id=it.get("account_id"),
                    ),
                    True,
                )
        gone = sent_ids | stale_ids
        with locked(lock, timeout=30.0):  # 그사이 새로 보관된 항목은 그대로 둔다
            keep = [k for k in self._read_outbox() if str(k.get("id") or k.get("at")) not in gone]
            if keep:
                self._outbox.write_text(
                    "".join(json.dumps(k, ensure_ascii=False) + "\n" for k in keep),
                    encoding="utf-8",
                )
            else:
                self._outbox.unlink(missing_ok=True)
        return {"sent": len(sent_ids), "left": len(keep), "dropped": len(stale_ids)}


def make_notifier(
    secrets: Secrets,
    *,
    http: httpx.Client | None = None,
    enabled: bool = True,
    record: Callable[[dict], None] | None = None,
) -> Notifier:
    """enabled=False 면 시크릿이 있어도 NullNotifier(설정의 알림 끔 스위치)."""
    if enabled and secrets.TELEGRAM_BOT_TOKEN and secrets.TELEGRAM_CHAT_ID:
        from trader.kernel.platform import paths

        return TelegramNotifier(
            secrets.TELEGRAM_BOT_TOKEN,
            secrets.TELEGRAM_CHAT_ID,
            http=http,
            outbox=paths.state_dir() / "notify_outbox.jsonl",
            record=record,
        )
    return NullNotifier()


# --- 수신 (승인 명령, Phase 4) ------------------------------------------------------

# 알려진 차이: 옛 정규식은 명령 이름을 열거해 모르는 이름이면 None 이었다. 새 디스패처는 기능이
# 명령을 등록하므로 여기서는 이름 형식만 보고, 모르는 이름의 거부(도움말)는
# commands.poll_once 가 한다.
_COMMAND_RE = re.compile(r"^/([a-z_]+)(?:@\w+)?(?:\s+(.+?))?\s*$")


@dataclass
class Update:
    update_id: int
    text: str
    chat_id: str
    sender_id: str = ""
    # 보낸 사람을 믿을 수 있는가: 1:1 채팅이거나 허용 목록(TELEGRAM_ALLOWED_USERS)에 있는 사람.
    # 그룹 채팅에서 허용 목록 없이 온 명령은 읽기·안전 명령만 처리한다(전체 검토 2026-10-27)
    trusted: bool = True
    # 인라인 버튼 누름(callback_query)이면 "callback": data=callback_data, callback_id 로 응답하고
    # message_id·message_text(버튼이 달린 원 메시지)로 결과를 덧붙여 고친다
    kind: Literal["message", "callback"] = "message"
    data: str = ""
    callback_id: str = ""
    message_id: int | None = None
    message_text: str = ""


def parse_command(text: str) -> tuple[str, str | None] | None:
    """`/이름 [인자]` 형식만 인식한다(이름 집합 검사는 디스패처 몫)."""
    m = _COMMAND_RE.match(text.strip())
    if not m:
        return None
    return m.group(1), m.group(2)


class TelegramReceiver:
    """getUpdates 1회 폴링. 설정된 chat_id 의 메시지 텍스트·버튼 콜백만 돌려준다."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        http: httpx.Client | None = None,
        allowed_users: set[str] | None = None,
    ) -> None:
        quiet_httpx()
        self._base = f"https://api.telegram.org/bot{token}/"
        self._url = self._base + "getUpdates"
        self._chat_id = str(chat_id)
        self._allowed = {str(u) for u in (allowed_users or set())}
        self._http = http or httpx.Client(timeout=15)
        # 마지막 poll 원본 결과(걸러진 것 포함)의 최대 update_id. 호출자가 오프셋에 반영한다.
        # 다른 채팅 업데이트만 100건 쌓이면 getUpdates 가 같은 묶음만 돌려줘 명령이 영원히 안 온다.
        self.last_update_id: int | None = None

    def poll(self, offset: int | None = None) -> list[Update]:
        params: dict[str, str] = {
            "timeout": "0",
            "allowed_updates": '["message", "callback_query"]',
        }
        if offset is not None:
            params["offset"] = str(offset)
        try:
            resp = self._http.get(self._url, params=params)
        except httpx.HTTPError as e:
            logger.warning("텔레그램 수신 실패: %s", type(e).__name__)
            return []
        if resp.status_code != 200:
            logger.warning("텔레그램 수신 거부: HTTP %s", resp.status_code)
            return []
        try:
            body = resp.json()
        except ValueError:
            return []
        out: list[Update] = []
        self.last_update_id = None
        for item in body.get("result") or []:
            try:
                uid = int(item["update_id"])
            except (KeyError, TypeError, ValueError):
                continue
            self.last_update_id = max(self.last_update_id or uid, uid)
            cq = item.get("callback_query")
            if isinstance(cq, dict):
                u = self._callback(uid, cq)
                if u is not None:
                    out.append(u)
                continue
            msg = item.get("message") or {}
            chat_obj = msg.get("chat") or {}
            chat = str(chat_obj.get("id", ""))
            text = msg.get("text")
            if chat != self._chat_id or not isinstance(text, str):
                continue
            sender = str((msg.get("from") or {}).get("id", ""))
            out.append(
                Update(
                    update_id=uid,
                    text=text,
                    chat_id=chat,
                    sender_id=sender,
                    trusted=self._trusted(sender, chat_obj),
                )
            )
        return out

    def _trusted(self, sender: str, chat_obj: dict) -> bool:
        if self._allowed:
            return sender in self._allowed
        # 종류가 빠진 응답은 1:1 로 간주하지 않는다(Codex 보완 — 보수적 기본값)
        return chat_obj.get("type") == "private"

    def _callback(self, uid: int, cq: dict) -> Update | None:
        """callback_query → Update(kind="callback"). 다른 채팅·data 없는 콜백은 None."""
        msg = cq.get("message") or {}
        chat_obj = msg.get("chat") or {}
        chat = str(chat_obj.get("id", ""))
        data = cq.get("data")
        if chat != self._chat_id or not isinstance(data, str):
            return None
        sender = str((cq.get("from") or {}).get("id", ""))
        try:
            message_id: int | None = int(msg["message_id"])
        except (KeyError, TypeError, ValueError):
            message_id = None
        text = msg.get("text")
        return Update(
            update_id=uid,
            text="",
            chat_id=chat,
            sender_id=sender,
            trusted=self._trusted(sender, chat_obj),
            kind="callback",
            data=data,
            callback_id=str(cq.get("id", "")),
            message_id=message_id,
            message_text=text if isinstance(text, str) else "",
        )

    def _call(self, method: str, data: dict[str, str]) -> bool:
        """봇 API POST 1회(발신 `_post` 와 같은 경로·오류 처리).
        URL 에 토큰이 있어 URL·예외 메시지는 로그에 남기지 않는다."""
        try:
            resp = self._http.post(self._base + method, data=data)
        except httpx.HTTPError as e:
            logger.warning("텔레그램 %s 실패: %s", method, type(e).__name__)
            return False
        if resp.status_code != 200:
            logger.warning("텔레그램 %s 거부: HTTP %s", method, resp.status_code)
            return False
        return True

    def answer_callback(self, callback_id: str, text: str) -> bool:
        """버튼 누름에 응답한다(answerCallbackQuery — 누른 사람 화면의 짧은 알림, 200자)."""
        return self._call(
            "answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]}
        )

    def edit_message(self, chat_id: str, message_id: int, text: str) -> bool:
        """버튼이 달린 메시지를 평문으로 고친다(editMessageText). reply_markup 을 보내지 않으므로
        버튼은 사라진다 — 같은 버튼을 두 번 누르지 않게."""
        return self._call(
            "editMessageText",
            {"chat_id": str(chat_id), "message_id": str(message_id), "text": text[:MAX_LEN]},
        )


def make_receiver(
    secrets: Secrets, *, http: httpx.Client | None = None, enabled: bool = True
) -> TelegramReceiver | None:
    if enabled and secrets.TELEGRAM_BOT_TOKEN and secrets.TELEGRAM_CHAT_ID:
        allowed = {
            x.strip() for x in (secrets.TELEGRAM_ALLOWED_USERS or "").split(",") if x.strip()
        }
        return TelegramReceiver(
            secrets.TELEGRAM_BOT_TOKEN, secrets.TELEGRAM_CHAT_ID, http=http, allowed_users=allowed
        )
    return None
