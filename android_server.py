import asyncio
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from catalog import catalog_data
from game import Engine, GameError

app_runtime = None


# HTTP 工作线程只处理传输；通过 server.runtime 回到规则引擎线程，不依赖构造函数闭包。
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, status, data, mime="application/json"):
        raw = json.dumps(data, ensure_ascii=False).encode() if mime == "application/json" else data
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def do_HEAD(self):
        self.do_GET()

    def allowed(self):
        host = "127.0.0.1:%s" % self.server.server_port
        return self.headers.get("Host") == host and self.headers.get("Origin", "http://" + host) == "http://" + host

    def do_GET(self):
        if not self.allowed():
            self.reply(403, {"detail": "仅允许本机应用访问。"})
            return
        runtime = self.server.runtime
        path = urlsplit(self.path).path
        if path == "/api/events":
            self.events()
        elif path.startswith("/api/speech_audio/"):
            audio = None
            started = False
            try:
                _, _, _, game_id, event_id = path.split("/")
                audio = runtime.engine.speech_audio(game_id, int(event_id))
                chunk = runtime.call(audio.__anext__())
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                started = True
                while True:
                    self.wfile.write(("%x\r\n" % len(chunk)).encode() + chunk + b"\r\n")
                    self.wfile.flush()
                    try:
                        chunk = runtime.call(audio.__anext__())
                    except StopAsyncIteration:
                        self.wfile.write(b"0\r\n\r\n")
                        break
            except (GameError, ValueError) as error:
                if not started:
                    self.reply(400, {"detail": str(error)})
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                if audio:
                    runtime.call(audio.aclose())
        elif path.startswith("/api/"):
            self.request_api(path[5:], {})
        else:
            file = (runtime.web / unquote(path).lstrip("/")).resolve()
            if not file.is_relative_to(runtime.web):
                self.reply(403, {"detail": "无效路径。"})
                return
            if not file.is_file():
                file = runtime.web / "index.html"
            self.reply(200, file.read_bytes(), mimetypes.guess_type(str(file))[0] or "application/octet-stream")

    def do_POST(self):
        if not self.allowed():
            self.reply(403, {"detail": "仅允许本机应用访问。"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 16384:
                raise ValueError()
            value = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(value, dict):
                raise ValueError()
            self.request_api(urlsplit(self.path).path.removeprefix("/api/"), value)
        except (ValueError, TypeError):
            self.reply(400, {"detail": "请求格式无效。"})

    def request_api(self, path, value):
        try:
            runtime = self.server.runtime
            data = runtime.call(runtime.dispatch(path, value, self.command))
            self.reply(200, data)
        except GameError as error:
            self.reply(400, {"detail": str(error)})
        except Exception:
            self.reply(500, {"detail": "本地服务异常，请重新打开 App。"})

    def events(self):
        runtime = self.server.runtime
        q, item = runtime.call(runtime.subscribe())
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            while True:
                raw = ("data: " + json.dumps(item, ensure_ascii=False) + "\n\n") if item is not None else ": heartbeat\n\n"
                self.wfile.write(raw.encode())
                self.wfile.flush()
                try:
                    item = runtime.call(asyncio.wait_for(q.get(), 15))
                except asyncio.TimeoutError:
                    item = None
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            runtime.loop.call_soon_threadsafe(runtime.engine.listeners.discard, q)


# 安卓进程内运行规则引擎，SQLite 创建、访问与 AI 调度固定在同一事件线程。
class Runtime:
    def __init__(self, data, web):
        self.web = Path(web).resolve()
        self.ready = threading.Event()
        self.error = None
        threading.Thread(target=self.run_engine, args=(data,), daemon=True).start()
        self.ready.wait()
        if self.error:
            raise self.error
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.runtime = self
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def run_engine(self, data):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.engine = Engine(data)
        except Exception as error:
            self.error = error
        self.ready.set()
        if self.error is None:
            self.loop.run_forever()

    def call(self, coroutine):
        # 不能直接在 HTTP 工作线程访问 SQLite；阻塞到事件线程完成本次操作。
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result()

    async def subscribe(self):
        q = asyncio.Queue(maxsize=512)
        self.engine.listeners.add(q)
        return q, {"type": "state", "data": json.loads(json.dumps(self.engine.view(), ensure_ascii=False))}

    async def dispatch(self, path, values, method):
        e = self.engine
        if method in ("GET", "HEAD"):
            if path == "catalog":
                return catalog_data()
            if path == "settings":
                return e.settings()
            if path == "state":
                return e.view()
            if path == "health":
                return {"ok": True}
            raise GameError("接口不存在。")
        if path == "settings":
            if any(not isinstance(v, str) for v in values.values()):
                raise GameError("设置格式无效。")
            if values.get("base_url") and not values["base_url"].startswith("https://"):
                raise GameError("API 地址必须使用 https://。")
            e.configure(values)
            return e.settings()
        if path == "new":
            n, m, s = values.get("count", 10), values.get("mode", "classic"), values.get("human_seat", 1)
            await e.pause()
            e.new(n, m, s, values.get("random_gods") is True, values.get("board_token"), values.get("selected_gods"), values.get("human_count", 1))
            await e.broadcast()
        elif path == "board":
            return e.preview_board(values.get("count"), values.get("mode"), values.get("random_gods") is True, values.get("selected_gods"))
        elif path == "takeover":
            if values.get("prepare"):
                await e.prepare_takeover()
            else:
                await e.takeover(values.get("token"))
        elif path == "action":
            await e.submit(values)
        elif path == "handoff":
            await e.handoff(values.get("reveal") is True)
        elif path == "discussion":
            await e.discuss(values)
        elif path == "pause":
            await e.pause()
        elif path == "clear_history":
            await e.clear_history()
        elif path == "resume":
            await e.resume()
        elif path == "explode":
            await e.explode(values.get("target"))
        elif path == "duel":
            await e.duel(values.get("target"))
        elif path == "voice":
            await e.voice(values.get("enabled") is True)
        elif path == "speech_done":
            await e.speech_done(values.get("event_id"))
        else:
            raise GameError("接口不存在。")
        # 在线程内序列化，避免 HTTP 线程读取正被修改的对局。
        return json.loads(json.dumps(e.view(), ensure_ascii=False))


def start(data, web):
    global app_runtime
    if app_runtime is None:
        app_runtime = Runtime(data, web)
    return app_runtime.http.server_port


def pause():
    if app_runtime:
        app_runtime.call(app_runtime.engine.pause())
