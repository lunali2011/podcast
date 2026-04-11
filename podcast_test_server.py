import asyncio
import cgi
import io
import json
import logging
import os
import re
import shutil
import ssl
import struct
import subprocess
import threading
import time
import unicodedata
import uuid

import certifi
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from typing import Callable, List
from urllib.parse import urlparse

import websockets


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output" / "generated"
MP3_DIR = ROOT / "mp3"
ALBUMS_FILE = ROOT / "albums.json"
PUBLISH_LOCK = threading.Lock()
COVER_GRADIENTS = [
    "linear-gradient(135deg, #5f73ff 0%, #5674d9 42%, #1f7dc2 100%)",
    "linear-gradient(135deg, #ff7a59 0%, #ff5e9c 50%, #b85cff 100%)",
    "linear-gradient(135deg, #34d399 0%, #06b6d4 50%, #2563eb 100%)",
    "linear-gradient(135deg, #facc15 0%, #f97316 50%, #ef4444 100%)",
    "linear-gradient(135deg, #a855f7 0%, #6366f1 50%, #0ea5e9 100%)",
    "linear-gradient(135deg, #f472b6 0%, #c084fc 50%, #818cf8 100%)",
]
HOST = "127.0.0.1"
PORT = 8765
ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sami/podcasttts"
RESOURCE_ID = "volc.service_type.10050"
APP_KEY = "aGjiRDfUWi"
DEFAULT_SPEAKERS = [
    "zh_male_dayixiansheng_v2_saturn_bigtts",
    "zh_female_mizaitongxue_v2_saturn_bigtts",
]
JOBS_FILE = ROOT / "output" / "jobs.json"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("podcast-test-server")

JOB_QUEUE: Queue[str] = Queue()
JOB_LOCK = threading.Lock()
JOB_CANCEL_FLAGS: dict[str, threading.Event] = {}
JOBS: dict[str, dict] = {}


class JobCancelledError(RuntimeError):
    pass


def load_env_file() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def read_jobs_file() -> dict[str, dict]:
    if not JOBS_FILE.exists():
        return {}
    try:
        payload = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def save_jobs_file() -> None:
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with JOB_LOCK:
        serializable = {
            job_id: {
                key: value
                for key, value in job.items()
                if key not in {"text", "cancel_event"}
            }
            for job_id, job in JOBS.items()
        }
    JOBS_FILE.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")


def list_jobs() -> list[dict]:
    with JOB_LOCK:
        items = list(JOBS.values())
    sanitized = [
        {key: value for key, value in item.items() if key != "text"}
        for item in items
    ]
    return sorted(sanitized, key=lambda item: item.get("createdAt", ""), reverse=True)


def update_job(job_id: str, **changes) -> dict:
    with JOB_LOCK:
        job = JOBS[job_id]
        job.update(changes)
        job["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        snapshot = dict(job)
    save_jobs_file()
    return snapshot


def create_job(title: str, intro: str, text: str) -> dict:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    job_id = f"job-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    cancel_event = threading.Event()
    job = {
        "id": job_id,
        "title": title.strip() or "未命名专辑",
        "intro": intro.strip(),
        "text": text,
        "status": "queued",
        "progress": 2,
        "message": "已加入队列",
        "createdAt": now,
        "updatedAt": now,
        "album": None,
        "error": None,
    }
    with JOB_LOCK:
        JOBS[job_id] = job
        JOB_CANCEL_FLAGS[job_id] = cancel_event
    save_jobs_file()
    JOB_QUEUE.put(job_id)
    return {key: value for key, value in job.items() if key != "text"}


def dismiss_job(job_id: str) -> bool:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return False
        if job.get("status") in {"queued", "running"}:
            return False
        JOBS.pop(job_id, None)
        JOB_CANCEL_FLAGS.pop(job_id, None)
    save_jobs_file()
    return True


def cancel_job(job_id: str) -> dict | None:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        cancel_event = JOB_CANCEL_FLAGS.get(job_id)
        if not job:
            return None
        if cancel_event:
            cancel_event.set()
        if job["status"] == "queued":
            job["status"] = "cancelled"
            job["progress"] = 0
            job["message"] = "已取消"
            job["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        elif job["status"] == "running":
            job["message"] = "正在取消"
            job["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        snapshot = {key: value for key, value in job.items() if key != "text"}
    save_jobs_file()
    return snapshot


class MsgType(IntEnum):
    Invalid = 0
    FullClientRequest = 0b1
    AudioOnlyClient = 0b10
    FullServerResponse = 0b1001
    AudioOnlyServer = 0b1011
    FrontEndResultServer = 0b1100
    Error = 0b1111


class MsgTypeFlagBits(IntEnum):
    NoSeq = 0
    PositiveSeq = 0b1
    LastNoSeq = 0b10
    NegativeSeq = 0b11
    WithEvent = 0b100


class VersionBits(IntEnum):
    Version1 = 1


class HeaderSizeBits(IntEnum):
    HeaderSize4 = 1


class SerializationBits(IntEnum):
    Raw = 0
    JSON = 0b1


class CompressionBits(IntEnum):
    None_ = 0


class EventType(IntEnum):
    None_ = 0
    StartConnection = 1
    FinishConnection = 2
    ConnectionStarted = 50
    ConnectionFinished = 52
    StartSession = 100
    FinishSession = 102
    SessionStarted = 150
    SessionFinished = 152
    UsageResponse = 154
    PodcastRoundStart = 360
    PodcastRoundResponse = 361
    PodcastRoundEnd = 362
    PodcastEnd = 363


@dataclass
class Message:
    version: VersionBits = VersionBits.Version1
    header_size: HeaderSizeBits = HeaderSizeBits.HeaderSize4
    type: MsgType = MsgType.Invalid
    flag: MsgTypeFlagBits = MsgTypeFlagBits.NoSeq
    serialization: SerializationBits = SerializationBits.JSON
    compression: CompressionBits = CompressionBits.None_
    event: EventType = EventType.None_
    session_id: str = ""
    connect_id: str = ""
    sequence: int = 0
    error_code: int = 0
    payload: bytes = b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "Message":
        if len(data) < 3:
            raise ValueError("data too short")
        msg_type = MsgType(data[1] >> 4)
        flag = MsgTypeFlagBits(data[1] & 0b00001111)
        msg = cls(type=msg_type, flag=flag)
        msg.unmarshal(data)
        return msg

    def marshal(self) -> bytes:
        buffer = io.BytesIO()
        header = [
            (self.version << 4) | self.header_size,
            (self.type << 4) | self.flag,
            (self.serialization << 4) | self.compression,
            0,
        ]
        buffer.write(bytes(header))
        if self.flag == MsgTypeFlagBits.WithEvent:
            buffer.write(struct.pack(">i", self.event))
            if self.event not in [
                EventType.StartConnection,
                EventType.FinishConnection,
                EventType.ConnectionStarted,
            ]:
                session_id_bytes = self.session_id.encode("utf-8")
                buffer.write(struct.pack(">I", len(session_id_bytes)))
                buffer.write(session_id_bytes)
        size = len(self.payload)
        buffer.write(struct.pack(">I", size))
        buffer.write(self.payload)
        return buffer.getvalue()

    def unmarshal(self, data: bytes) -> None:
        buffer = io.BytesIO(data)
        version_and_header_size = buffer.read(1)[0]
        self.version = VersionBits(version_and_header_size >> 4)
        self.header_size = HeaderSizeBits(version_and_header_size & 0b00001111)
        buffer.read(1)
        serialization_compression = buffer.read(1)[0]
        self.serialization = SerializationBits(serialization_compression >> 4)
        self.compression = CompressionBits(serialization_compression & 0b00001111)
        buffer.read(1)

        if self.type in [MsgType.FullClientRequest, MsgType.FullServerResponse, MsgType.AudioOnlyClient, MsgType.AudioOnlyServer]:
            if self.flag == MsgTypeFlagBits.WithEvent:
                self.event = EventType(struct.unpack(">i", buffer.read(4))[0])
                if self.event not in [
                    EventType.StartConnection,
                    EventType.FinishConnection,
                    EventType.ConnectionStarted,
                    EventType.ConnectionFinished,
                ]:
                    session_len = struct.unpack(">I", buffer.read(4))[0]
                    if session_len:
                        self.session_id = buffer.read(session_len).decode("utf-8")
                if self.event in [EventType.ConnectionStarted, EventType.ConnectionFinished]:
                    connect_len = struct.unpack(">I", buffer.read(4))[0]
                    if connect_len:
                        self.connect_id = buffer.read(connect_len).decode("utf-8")
        elif self.type == MsgType.Error:
            self.error_code = struct.unpack(">I", buffer.read(4))[0]

        size = struct.unpack(">I", buffer.read(4))[0]
        if size:
            self.payload = buffer.read(size)


async def receive_message(websocket) -> Message:
    data = await websocket.recv()
    if isinstance(data, str):
        raise ValueError(f"unexpected text message: {data}")
    msg = Message.from_bytes(data)
    logger.info("received event=%s type=%s", msg.event, msg.type)
    return msg


async def wait_for_event(websocket, msg_type: MsgType, event_type: EventType) -> Message:
    while True:
        msg = await receive_message(websocket)
        if msg.type == msg_type and msg.event == event_type:
            return msg
        raise ValueError(f"unexpected message: {msg.type}/{msg.event}")


async def start_connection(websocket) -> None:
    msg = Message(type=MsgType.FullClientRequest, flag=MsgTypeFlagBits.WithEvent)
    msg.event = EventType.StartConnection
    msg.payload = b"{}"
    await websocket.send(msg.marshal())


async def finish_connection(websocket) -> None:
    msg = Message(type=MsgType.FullClientRequest, flag=MsgTypeFlagBits.WithEvent)
    msg.event = EventType.FinishConnection
    msg.payload = b"{}"
    await websocket.send(msg.marshal())


async def start_session(websocket, payload: bytes, session_id: str) -> None:
    msg = Message(type=MsgType.FullClientRequest, flag=MsgTypeFlagBits.WithEvent)
    msg.event = EventType.StartSession
    msg.session_id = session_id
    msg.payload = payload
    await websocket.send(msg.marshal())


async def finish_session(websocket, session_id: str) -> None:
    msg = Message(type=MsgType.FullClientRequest, flag=MsgTypeFlagBits.WithEvent)
    msg.event = EventType.FinishSession
    msg.session_id = session_id
    msg.payload = b"{}"
    await websocket.send(msg.marshal())


MAX_EPISODE_CHARS = 1100
MIN_EPISODE_CHARS = 450


def clean_text_block(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_into_paragraphs(text: str) -> list[str]:
    cleaned = clean_text_block(text)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
    if paragraphs:
        return paragraphs
    sentences = re.split(r"(?<=[。！？!?；;])", cleaned)
    return [item.strip() for item in sentences if item.strip()]


def build_episode_title(index: int, segment: str, album_title: str) -> str:
    sentences = re.split(r"(?<=[。！？!?])", segment)
    for sentence in sentences:
        plain = sentence.strip().strip("。！？!?")
        if 6 <= len(plain) <= 22:
            return f"第{index}集 · {plain}"
    headline = segment.replace("\n", " ").strip()
    headline = headline[:18].rstrip("，。；、 ")
    if not headline:
        headline = f"{album_title}片段"
    return f"第{index}集 · {headline}"


def build_episode_description(segment: str) -> str:
    summary = segment.replace("\n", " ").strip()
    summary = re.sub(r"\s+", " ", summary)
    if len(summary) <= 42:
        return summary
    return f"{summary[:42].rstrip('，。；、 ')}..."


def format_duration_label(total_seconds: int | float) -> str:
    if not total_seconds:
        return "8分钟内"
    mins = int(total_seconds // 60)
    secs = int(round(total_seconds % 60))
    if secs == 60:
        mins += 1
        secs = 0
    return f"{mins}:{str(secs).zfill(2)}"


def run_job(job_id: str) -> None:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        cancel_event = JOB_CANCEL_FLAGS.get(job_id)
        if not job:
            return
        text = job.get("text", "")
        title = job.get("title", "")
        intro = job.get("intro", "")

    if cancel_event and cancel_event.is_set():
        update_job(job_id, status="cancelled", progress=0, message="已取消")
        return

    update_job(job_id, status="running", progress=5, message="准备生成")

    def progress_callback(progress: int, message: str) -> None:
        update_job(job_id, progress=progress, message=message)

    try:
        result = asyncio.run(
            generate_album(
                text,
                title,
                intro,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
            )
        )
    except JobCancelledError:
        update_job(job_id, status="cancelled", progress=0, message="已取消")
        return
    except Exception as exc:
        logger.exception("album generation failed for %s", job_id)
        update_job(job_id, status="failed", progress=0, message="生成失败", error=str(exc))
        return

    update_job(
        job_id,
        status="succeeded",
        progress=100,
        message="专辑已生成",
        album=result.get("album"),
        error=None,
    )


def worker_loop() -> None:
    while True:
        job_id = JOB_QUEUE.get()
        try:
            run_job(job_id)
        finally:
            JOB_QUEUE.task_done()


def plan_album(text: str, title: str, intro: str) -> dict:
    paragraphs = split_into_paragraphs(text)
    if not paragraphs:
        raise RuntimeError("没有可用内容，暂时无法生成专辑")

    episode_texts: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
        if len(candidate) <= MAX_EPISODE_CHARS:
            buffer = candidate
            continue
        if buffer:
            episode_texts.append(buffer)
            buffer = paragraph
        else:
            chunks = [paragraph[i:i + MAX_EPISODE_CHARS] for i in range(0, len(paragraph), MAX_EPISODE_CHARS)]
            episode_texts.extend(chunk.strip() for chunk in chunks if chunk.strip())
            buffer = ""
    if buffer:
        episode_texts.append(buffer)

    merged: list[str] = []
    for segment in episode_texts:
        if merged and len(segment) < MIN_EPISODE_CHARS and len(merged[-1]) + len(segment) <= MAX_EPISODE_CHARS:
            merged[-1] = f"{merged[-1]}\n\n{segment}".strip()
        else:
            merged.append(segment)
    episode_texts = merged or episode_texts

    album_title = title.strip() or "新播客专辑"
    album_intro = intro.strip() or "把一段内容拆成更适合通勤和跑步时收听的多集播客。"
    episodes = []
    for index, segment in enumerate(episode_texts, start=1):
        episodes.append(
            {
                "id": f"ep-{index:02d}",
                "index": index,
                "title": build_episode_title(index, segment, album_title),
                "description": build_episode_description(segment),
                "sourceText": segment,
                "targetDurationLabel": "8分钟内",
            }
        )

    return {
        "title": album_title,
        "description": album_intro,
        "episodeCount": len(episodes),
        "episodes": episodes,
    }


async def generate_episode_audio(
    text: str,
    job_dir: Path,
    file_prefix: str,
    cancel_event: threading.Event | None = None,
) -> dict:
    appid = os.environ.get("VOLC_APP_ID", "").strip()
    access_token = os.environ.get("VOLC_ACCESS_TOKEN", "").strip()
    if not appid or not access_token:
        raise RuntimeError("服务端未配置 VOLC_APP_ID / VOLC_ACCESS_TOKEN")

    connect_id = str(uuid.uuid4())
    headers = {
        "X-Api-App-Id": appid,
        "X-Api-App-Key": APP_KEY,
        "X-Api-Access-Key": access_token,
        "X-Api-Resource-Id": RESOURCE_ID,
        "X-Api-Connect-Id": connect_id,
    }

    req_params = {
        "input_id": f"luna-{int(time.time())}",
        "input_text": text,
        "action": 0,
        "use_head_music": False,
        "use_tail_music": False,
        "input_info": {
            "return_audio_url": False,
            "only_nlp_text": False,
        },
        "speaker_info": {
            "random_order": True,
            "speakers": DEFAULT_SPEAKERS,
        },
        "audio_config": {
            "format": "mp3",
            "sample_rate": 24000,
            "speech_rate": 0,
        },
    }

    rounds = []
    combined_audio = bytearray()

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    async with websockets.connect(ENDPOINT, extra_headers=headers, ssl=ssl_context) as websocket:
        await start_connection(websocket)
        await wait_for_event(websocket, MsgType.FullServerResponse, EventType.ConnectionStarted)

        session_id = str(uuid.uuid4())
        await start_session(websocket, json.dumps(req_params).encode("utf-8"), session_id)
        await wait_for_event(websocket, MsgType.FullServerResponse, EventType.SessionStarted)
        await finish_session(websocket, session_id)

        current_round = None
        current_audio = bytearray()

        while True:
            if cancel_event and cancel_event.is_set():
                raise JobCancelledError("任务已取消")
            try:
                msg = await asyncio.wait_for(receive_message(websocket), timeout=8)
            except asyncio.TimeoutError:
                # Some runs keep streaming usage events without a clean terminal event.
                # If we've already collected audio, return what we have instead of hanging.
                if rounds or combined_audio:
                    logger.info("podcast stream idle timeout reached; finishing with collected rounds")
                    break
                raise RuntimeError("生成超时，请稍后再试")
            if msg.type == MsgType.Error:
                raise RuntimeError(msg.payload.decode("utf-8", "ignore"))

            if msg.type == MsgType.AudioOnlyServer and msg.event == EventType.PodcastRoundResponse:
                current_audio.extend(msg.payload)
                continue

            if msg.type == MsgType.FullServerResponse and msg.event == EventType.PodcastRoundStart:
                data = json.loads(msg.payload.decode("utf-8"))
                current_round = {
                    "round_id": data.get("round_id"),
                    "speaker": data.get("speaker") or "music",
                    "text": data.get("text") or "",
                    "audio_duration": None,
                    "audio_url": None,
                }
                continue

            if msg.type == MsgType.FullServerResponse and msg.event == EventType.PodcastRoundEnd:
                data = json.loads(msg.payload.decode("utf-8"))
                if current_round is not None:
                    current_round["audio_duration"] = data.get("audio_duration")
                    filename = f"{file_prefix}-{current_round['speaker']}_{current_round['round_id']}.mp3"
                    file_path = job_dir / filename
                    if current_audio:
                        file_path.write_bytes(current_audio)
                        combined_audio.extend(current_audio)
                        current_round["audio_url"] = filename
                    rounds.append(current_round)
                current_round = None
                current_audio = bytearray()
                continue

            if msg.type == MsgType.FullServerResponse and msg.event == EventType.PodcastEnd:
                break

            if msg.type == MsgType.FullServerResponse and msg.event == EventType.SessionFinished:
                break

        await finish_connection(websocket)

    final_url = None
    if combined_audio:
        final_path = job_dir / f"{file_prefix}-podcast-final.mp3"
        final_path.write_bytes(combined_audio)
        final_url = final_path.name

    return {
        "audioUrl": final_url,
        "rounds": rounds,
    }


async def generate_album(
    text: str,
    title: str,
    intro: str,
    progress_callback: Callable[[int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    plan = plan_album(text, title, intro)
    job_id = f"job-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    generated_episodes = []
    total_duration = 0
    total_episodes = len(plan["episodes"])

    if progress_callback:
        progress_callback(8, "正在规划专辑")

    for episode in plan["episodes"]:
        if cancel_event and cancel_event.is_set():
            raise JobCancelledError("任务已取消")
        if progress_callback:
            base = 10 + int(((episode["index"] - 1) / max(total_episodes, 1)) * 80)
            progress_callback(base, f"正在生成第 {episode['index']} 集")
        generated = await generate_episode_audio(
            episode["sourceText"],
            job_dir,
            episode["id"],
            cancel_event=cancel_event,
        )
        rounds = [round_data for round_data in generated.get("rounds", []) if round_data.get("text")]
        duration = sum((round_data.get("audio_duration") or 0) for round_data in rounds)
        total_duration += duration
        generated_episodes.append(
            {
                "id": episode["id"],
                "index": episode["index"],
                "title": episode["title"],
                "description": episode["description"],
                "durationLabel": format_duration_label(duration),
                "audioUrl": f"/output/generated/{job_id}/{generated['audioUrl']}" if generated.get("audioUrl") else None,
                "duration": duration,
                "rounds": [
                    {
                        **round_data,
                        "audio_url": f"/output/generated/{job_id}/{round_data['audio_url']}" if round_data.get("audio_url") else None,
                    }
                    for round_data in rounds
                ],
            }
        )
        if progress_callback:
            base = 10 + int((episode["index"] / max(total_episodes, 1)) * 80)
            progress_callback(base, f"已完成第 {episode['index']} 集")

    if progress_callback:
        progress_callback(100, "专辑生成完成")
    return {
        "jobId": job_id,
        "album": {
            "title": plan["title"],
            "description": plan["description"],
            "episodeCount": len(generated_episodes),
            "episodes": generated_episodes,
            "totalDuration": total_duration,
        },
    }


def slugify_album(title: str) -> str:
    if not title:
        title = "album"
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    if not cleaned:
        # 中文标题：用 hash 兜底
        cleaned = f"album-{uuid.uuid4().hex[:8]}"
    return cleaned[:48]


def load_albums() -> dict:
    if not ALBUMS_FILE.exists():
        return {"albums": []}
    try:
        data = json.loads(ALBUMS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"albums": []}
    if not isinstance(data, dict) or not isinstance(data.get("albums"), list):
        return {"albums": []}
    return data


def save_albums(data: dict) -> None:
    ALBUMS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def total_duration_label(total_seconds: int | float) -> str:
    if not total_seconds or total_seconds <= 0:
        return "约 5 分钟"
    minutes = max(1, round(total_seconds / 60))
    return f"约 {minutes} 分钟"


def make_card_description(description: str) -> str:
    if not description:
        return ""
    text = description.strip()
    if len(text) <= 60:
        return text
    return f"{text[:60].rstrip('，。；、 ')}..."


def publish_album_from_job(job_id: str, options: dict) -> dict:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if job:
            job = dict(job)
    if not job:
        raise ValueError("任务不存在")
    if job.get("status") != "succeeded":
        raise ValueError("任务尚未完成，无法发布")
    album = job.get("album")
    if not album or not album.get("episodes"):
        raise ValueError("任务没有可发布的专辑")

    title = (options.get("title") or album.get("title") or "未命名专辑").strip()
    description = (options.get("description") or album.get("description") or "").strip()
    subtitle = (options.get("subtitle") or "").strip()
    card_description = (options.get("cardDescription") or make_card_description(description)).strip()
    tags_input = options.get("tags") or []
    if isinstance(tags_input, str):
        tags = [t.strip() for t in re.split(r"[,，\s]+", tags_input) if t.strip()]
    else:
        tags = [str(t).strip() for t in tags_input if str(t).strip()]
    if not tags:
        tags = ["AI 生成"]

    with PUBLISH_LOCK:
        existing = load_albums()
        existing_slugs = {a.get("slug") for a in existing.get("albums", [])}
        existing_ids = {a.get("id") for a in existing.get("albums", [])}

        base_slug = slugify_album(title)
        slug = base_slug
        suffix = 2
        while slug in existing_slugs:
            slug = f"{base_slug}-{suffix}"
            suffix += 1

        album_id = f"album-{slug}"
        suffix = 2
        while album_id in existing_ids:
            album_id = f"album-{slug}-{suffix}"
            suffix += 1

        target_dir = MP3_DIR / slug
        target_dir.mkdir(parents=True, exist_ok=True)

        published_episodes = []
        total_duration = 0
        for episode in album.get("episodes", []):
            audio_url = episode.get("audioUrl") or ""
            # audioUrl looks like /output/generated/{job_id}/{file}
            if not audio_url:
                continue
            relative = audio_url.lstrip("/")
            src_path = ROOT / relative
            if not src_path.exists():
                raise FileNotFoundError(f"找不到音频文件: {src_path}")
            index_str = str(episode.get("index") or len(published_episodes) + 1).zfill(2)
            dest_name = f"{index_str}.mp3"
            dest_path = target_dir / dest_name
            shutil.copy2(src_path, dest_path)

            duration = episode.get("duration") or 0
            total_duration += duration
            published_episodes.append(
                {
                    "id": f"ep-{slug}-{index_str}",
                    "index": index_str,
                    "title": episode.get("title") or "",
                    "description": episode.get("description") or "",
                    "audioUrl": f"./mp3/{slug}/{dest_name}",
                    "durationLabel": episode.get("durationLabel") or format_duration_label(duration),
                    "rounds": [],
                }
            )

        if not published_episodes:
            raise ValueError("没有可发布的剧集音频")

        gradient_index = len(existing.get("albums", [])) % len(COVER_GRADIENTS)
        new_album = {
            "id": album_id,
            "slug": slug,
            "title": title,
            "subtitle": subtitle,
            "description": description,
            "cardDescription": card_description,
            "tags": tags,
            "coverGradient": COVER_GRADIENTS[gradient_index],
            "totalDurationLabel": total_duration_label(total_duration),
            "episodeCount": len(published_episodes),
            "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sourceJobId": job_id,
            "episodes": published_episodes,
        }

        existing.setdefault("albums", []).insert(0, new_album)
        save_albums(existing)

    with JOB_LOCK:
        live = JOBS.get(job_id)
        if live is not None:
            live["publishedAlbumId"] = album_id
            live["publishedSlug"] = slug
    save_jobs_file()

    return new_album


def run_git_sync(message: str) -> dict:
    cmds = [
        ["git", "add", "albums.json", "mp3"],
        ["git", "commit", "-m", message],
        ["git", "push", "origin", "main"],
    ]
    log = []
    for cmd in cmds:
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        log.append(
            {
                "cmd": " ".join(cmd),
                "code": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
        )
        if result.returncode != 0:
            # commit step is allowed to fail with "nothing to commit"
            if cmd[1] == "commit" and "nothing to commit" in (result.stdout + result.stderr).lower():
                continue
            raise RuntimeError(f"{' '.join(cmd)} 失败：{result.stderr.strip() or result.stdout.strip()}")
    return {"log": log}


class PodcastTestHandler(SimpleHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.split("/")[3]
            if dismiss_job(job_id):
                self._send_json(200, {"ok": True})
            else:
                self._send_json(409, {"error": "任务不存在或仍在运行"})
            return
        self.send_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/extract-text":
            self._handle_extract_text()
            return
        if parsed.path == "/api/jobs":
            self._handle_create_job()
            return
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
            job_id = parsed.path.split("/")[3]
            self._handle_cancel_job(job_id)
            return
        if parsed.path == "/api/publish":
            self._handle_publish()
            return
        if parsed.path == "/api/sync":
            self._handle_sync()
            return
        if parsed.path != "/api/album/generate":
            self.send_error(404, "Not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        text = (payload.get("text") or "").strip()
        title = (payload.get("title") or "").strip()
        intro = (payload.get("intro") or "").strip()

        if not text:
            self._send_json(400, {"error": "text 必填"})
            return

        try:
            result = asyncio.run(generate_album(text, title, intro))
        except Exception as exc:
            logger.exception("album generation failed")
            self._send_json(500, {"error": str(exc)})
            return

        self._send_json(200, result)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/jobs":
            self._send_json(200, {"jobs": list_jobs()})
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.split("/")[3]
            with JOB_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._send_json(404, {"error": "任务不存在"})
                return
            self._send_json(200, {key: value for key, value in job.items() if key != "text"})
            return
        super().do_GET()

    def _handle_extract_text(self):
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            },
        )

        if "file" not in form:
            self._send_json(400, {"error": "缺少上传文件"})
            return

        uploaded = form["file"]
        filename = os.path.basename(uploaded.filename or "")
        if not filename:
            self._send_json(400, {"error": "文件名无效"})
            return

        suffix = Path(filename).suffix.lower()
        file_bytes = uploaded.file.read()

        try:
            if suffix == ".txt":
                text = file_bytes.decode("utf-8")
            elif suffix == ".pdf":
                temp_dir = ROOT / "output" / "tmp"
                temp_dir.mkdir(parents=True, exist_ok=True)
                pdf_path = temp_dir / f"{uuid.uuid4().hex}.pdf"
                pdf_path.write_bytes(file_bytes)
                try:
                    result = subprocess.run(
                        ["pdftotext", str(pdf_path), "-"],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    text = result.stdout
                finally:
                    pdf_path.unlink(missing_ok=True)
            else:
                self._send_json(400, {"error": "目前只支持 txt 和 pdf"})
                return
        except Exception as exc:
            logger.exception("extract text failed")
            self._send_json(500, {"error": f"提取文本失败：{exc}"})
            return

        cleaned = "\n".join(line.rstrip() for line in text.splitlines()).strip()
        if not cleaned:
            self._send_json(400, {"error": "文件里没有提取到可用文本"})
            return

        self._send_json(200, {"text": cleaned, "filename": filename})

    def _handle_create_job(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        text = (payload.get("text") or "").strip()
        title = (payload.get("title") or "").strip()
        intro = (payload.get("intro") or "").strip()
        if not text:
            self._send_json(400, {"error": "text 必填"})
            return
        job = create_job(title, intro, text)
        self._send_json(202, {"job": job})

    def _handle_cancel_job(self, job_id: str):
        job = cancel_job(job_id)
        if not job:
            self._send_json(404, {"error": "任务不存在"})
            return
        self._send_json(200, {"job": job})

    def _handle_publish(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "请求体不是合法 JSON"})
            return
        job_id = (payload.get("jobId") or payload.get("job_id") or "").strip()
        if not job_id:
            self._send_json(400, {"error": "jobId 必填"})
            return
        try:
            album = publish_album_from_job(job_id, payload)
        except FileNotFoundError as exc:
            self._send_json(500, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception as exc:
            logger.exception("publish failed")
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, {"album": album})

    def _handle_sync(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}
        message = (payload.get("message") or "").strip()
        if not message:
            message = f"Publish album {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        try:
            result = run_git_sync(message)
        except RuntimeError as exc:
            self._send_json(500, {"error": str(exc)})
            return
        except Exception as exc:
            logger.exception("git sync failed")
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, result)

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    os.chdir(ROOT)
    load_env_file()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JOBS.update(read_jobs_file())
    threading.Thread(target=worker_loop, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), PodcastTestHandler)
    print(f"Podcast test server running at http://{HOST}:{PORT}")
    server.serve_forever()
