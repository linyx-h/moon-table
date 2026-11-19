import asyncio
import copy
import json
import logging
import math
import random
import re
import secrets
import sqlite3
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from collections import Counter
from urllib.parse import urlsplit

import httpx
import edge_tts

from catalog import ROLES, MODES, board, board_options, rules
from role_skills import ROLE_SKILLS

NAMES = ["你", "松果", "阿栗", "晚风", "石榴", "麦芽", "山雀", "小满", "灰盐", "柚子", "眠鱼", "红豆", "青苔", "烛火", "星野"]
PERSONAS = [
    "直觉型，短句，敢先给可修正的判断", "温和坚定，擅长比较两套说法并争取支持", "爽快机灵，喜欢用反问或小试探打开局面",
    "审慎但不消极，找出最关键的一处差异再表态", "活泼有锋芒，能接梗，也会认真辩护", "沉稳，擅长从投票和行动收益判断同伴关系",
    "独立，有理由就反对多数，判断错了也肯改", "细心，记得别人之前的承诺，抓住变化追问", "简洁直接，给立场也说最关键的理由",
    "灵活，愿意承担适度风险，用身份故事和试探争取主动", "耐心，替被误解的人解释，也能指出其真正的问题", "果断，擅长比较今天投谁的收益并拉票",
    "敏锐，沿着别人回答中的细节继续追问", "沉着，善于藏身份，但关键投票与争论不缺席",
]
SPEECH_KINDS = {"speech", "free", "last", "campaign", "debate"}
NIGHT_SKILLS = {"guard", "dream", "charm", "inspect", "potion", "peek", "curse", "hunt", "sculpt", "aura", "mute", "tomb"}
REQUIRED_TARGETS = {"inspect", "dream", "charm", "sculpt"}
API_INTERVAL = 1.7


class GameError(Exception):
    pass


# 将模型台词中的圈内术语改为日常说法，与角色策略和技能结算分开处理。
def plain_text(text):
    for term, wording in {
        "警徽流": "查验计划", "撕毁警徽": "不再设警长", "争警徽": "竞选警长", "警徽": "警长资格",
        "金水": "预言家查验过的好人", "银水": "女巫救过的人", "查杀": "查验结果是狼人",
        "改变站边": "改变判断", "改站边": "改变判断", "站边": "支持", "票型": "每个人的投票", "狼坑": "怀疑的狼人名单",
        "表水": "解释自己为什么是好人", "对跳": "声称同一个身份", "悍跳": "冒充特殊角色",
        "倒钩": "投给狼队友来骗取信任", "抗推位": "可能被投票赶走的目标", "抗推": "被投票赶走",
        "自刀": "故意袭击自己或狼队友", "上警": "竞选警长", "归票": "建议大家投给谁",
        "铁好人": "确定的好人", "外置位": "其他玩家", "冲锋": "帮狼队友带头投票",
        "划水": "没有说出具体判断", "盘逻辑": "分析理由", "做身份": "取得信任",
        "抿身份": "猜身份", "卖队友": "出卖狼队友",
    }.items():
        text = text.replace(term, wording)
    return text


# 单局裁判：负责私有视角、串行行动与存档，AI 只消费本人可见信息。
class Engine:
    def __init__(self, data_dir):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.root / "games.sqlite3"))
        self.db.execute("CREATE TABLE IF NOT EXISTS games (id TEXT PRIMARY KEY, updated REAL, state TEXT)")
        self.db.commit()
        self.config_path = self.root / "settings.json"
        saved = json.loads(self.config_path.read_text()) if self.config_path.exists() else {}
        self.config = {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "api_key": "", "user_api": True}
        # 公开版只使用玩家明确配置的接口，不继承旧版随包提供的凭据。
        if saved.get("user_api") is True:
            self.config.update({k: saved[k] for k in ("base_url", "model", "api_key") if isinstance(saved.get(k), str)})
        latest = self.db.execute("SELECT state FROM games ORDER BY updated DESC LIMIT 1").fetchone()
        # g 是当前整局的裁判状态；界面和 AI 只能使用 view()/context() 过滤后的数据。
        self.g = json.loads(latest[0]) if latest else None
        if self.g and self.g.get("speech_revision") != 1:
            self.g.update(read_aloud=True, speech_revision=1)
        if self.g and len(self.g.get("humans", [])) > 1:
            self.g["human"] = None
        if self.g and not self.g["winner"]:
            if self.g.get("discussion_until"):
                self.g["discussion_seconds"] = max(0, self.g["discussion_until"] - time.time())
                self.g["discussion_until"] = None
            self.g["paused"] = True
            self.g["error"] = ""
            self.save()
        self.task = None
        self.live = None
        self.speech_cache = None
        self.listeners = set()
        self.previews = {}
        self.request_at = self.g.get("api_next_at", 0) if self.g else 0
        self.wait_reason = ""
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(45, connect=8))

    def save(self):
        if self.g:
            self.g["revision"] = self.g.get("revision", 0) + 1
            self.db.execute("INSERT OR REPLACE INTO games VALUES (?, ?, ?)", (self.g["id"], time.time(), json.dumps(self.g, ensure_ascii=False)))
            self.db.commit()

    def settings(self):
        return {k: self.config[k] for k in ("base_url", "model")} | {"configured": bool(self.config["api_key"])}

    def configure(self, values):
        candidate = {**self.config, **{k: values[k].strip() for k in ("base_url", "model", "api_key") if k in values}}
        url = urlsplit(candidate["base_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise GameError("API 地址必须是 HTTPS 基础地址，不能包含密钥、账号或查询参数。")
        if not candidate["model"] or len(candidate["model"]) > 128:
            raise GameError("请填写有效的模型名称。")
        if any(ord(c) < 32 or ord(c) > 126 for c in candidate["api_key"]):
            raise GameError("API Key 不能包含换行或非英文字符。")
        if candidate["base_url"] != self.config["base_url"] and "api_key" not in values:
            raise GameError("切换接口时请重新填写对应的 API Key。")
        if not candidate["api_key"]:
            raise GameError("请填写你自己的 API Key。")
        self.config = candidate
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False))
        self.config_path.chmod(0o600)

    def alive(self):
        return [p["seat"] for p in self.g["players"] if p["alive"]]

    def player(self, seat):
        return self.g["players"][seat - 1]

    def wolves(self):
        return [s for s in self.alive() if ROLES[self.player(s)["role"]]["camp"] == "wolf"]

    def public(self, text, kind="system", seat=None, stage=None):
        self.g["events"].append({"id": len(self.g["events"]) + 1, "day": self.g["day"], "phase": self.g["phase"], "kind": kind, "seat": seat, "text": text})
        if kind == "host":
            self.g["host"] = {**self.g["events"][-1], "stage": stage}

    def private(self, seat, text, **facts):
        self.player(seat)["private"].append({"day": self.g["day"], "text": text, **facts})

    def preview_board(self, count, mode, random_gods=False, selected_gods=None):
        if type(count) is not int or not 6 <= count <= 15 or not isinstance(mode, str) or mode not in MODES or count < MODES[mode]["min"]:
            raise GameError("人数或板子无效。")
        try:
            cards = board(count, mode, random_gods, selected_gods)
        except ValueError as error:
            raise GameError(str(error)) from error
        value = {"count": count, "mode": mode, "random_gods": random_gods, "selected_gods": (selected_gods or [])[:],
                 "roles": cards, "token": secrets.token_hex(16)}
        self.previews[value["token"]] = value
        if len(self.previews) > 8:
            del self.previews[next(iter(self.previews))]
        return {**value, "rules": rules(count, mode), **board_options(count, mode)}

    def humans(self):
        return self.g.get("humans", [self.g["human"]])

    def shared(self):
        return len(self.humans()) > 1

    def new(self, count, mode, human_seat, random_gods=False, board_token=None, selected_gods=None, human_count=1):
        if not self.config["api_key"]:
            raise GameError("请先在接口设置中填写你自己的 API Key。")
        preview = self.previews.get(board_token) if board_token else self.preview_board(count, mode, random_gods, selected_gods)
        if not preview or (preview["count"], preview["mode"], preview["random_gods"], preview["selected_gods"]) != (count, mode, random_gods, selected_gods or []):
            raise GameError("配牌预览已失效，请重新抽取后开局。")
        if type(human_seat) is not int or not 1 <= human_seat <= count:
            raise GameError("座位无效。")
        if type(human_count) is not int or human_count not in (1, 2):
            raise GameError("请选择单人或双人模式。")
        humans = [(human_seat - 1 + i) % count + 1 for i in range(human_count)]
        cards = preview["roles"][:]
        public_cards = sorted(cards)
        random.shuffle(cards)
        self.discard_speech()
        self.g = {
            "id": secrets.token_hex(8), "count": count, "mode": mode, "day": 1, "phase": "night", "paused": False,
            "winner": None, "error": "", "human": human_seat if human_count == 1 else None, "humans": humans,
            "queue": [], "waiting": None, "read_aloud": True, "speech_revision": 1,
            "sheriff": None, "election_done": count < 9, "night": {}, "guard_last": None,
            "cards": public_cards, "random_gods": random_gods, "selected_gods": preview["selected_gods"], "gods_disabled": False,
            "api_next_at": self.request_at, "api_wait": None, "takeover": None, "last_lynched": None,
            "votes": {}, "candidates": [], "runoff": False, "election_runoff": False, "last_ballot": None,
            "players": [{"seat": i + 1, "name": ("你" if human_count == 1 else "玩家%s" % (humans.index(i + 1) + 1)) if i + 1 in humans else ("松柏" if i == 0 else NAMES[i]), "role": r, "alive": True,
                         "revealed": False, "can_vote": True, "antidote": True, "poison": True, "private": [],
                         "persona": PERSONAS[i % len(PERSONAS)], "plan": ""} for i, r in enumerate(cards)],
            "events": [],
        }
        self.public("座位已就绪。身份独立发放，天黑请闭眼。")
        self.enqueue(*(self.action("deal", s) for s in humans))
        self.start_night()
        self.save()
        self.kick()

    def action(self, kind, seat=None, **extra):
        return {"kind": kind, "seat": seat, **extra}

    def enqueue(self, *items):
        self.g["queue"].extend(items)

    def start_night(self):
        self.g["phase"] = "night"
        self.g["night"] = {"wolf_votes": {}, "wolf_chat": [], "kill": None, "guard": None, "save": False, "poison": None, "caught": False, "peekers": [],
                           "dream": None, "dream_previous": self.g.get("dream_last"), "charm": None, "curse": None, "hunt": None, "mute": None}
        self.g["dream_last"] = None
        self.enqueue(self.action("host", text="第 %s 夜，所有玩家请闭眼。" % self.g["day"], stage="closed"))
        if self.shared():
            # 每轮按固定真人座位传递手机，非行动者也经过遮挡页，避免传递次序泄露神职。
            batches = [
                ("准备", {"dreamer": "dream", "guard": "guard", "gargoyle": "sculpt", "girl": "peek"}),
                ("商议", {r: "wolf_chat" for r in ("wolf", "wolfking", "whitewolf", "wolfbeauty")}),
                ("狼队投票", {r: "wolf" for r in ("wolf", "wolfking", "whitewolf", "wolfbeauty")}),
                ("技能", {"wolfbeauty": "charm", "seer": "inspect", "fox": "aura", "gravekeeper": "tomb",
                         "witch": "potion", "raven": "curse", "demonhunter": "hunt", "silencer": "mute"}),
            ]
            for label, mapping in batches:
                self.enqueue(self.action("host", text="夜间%s轮。依座位传递手机，其他玩家请闭眼。" % label, stage="shared"))
                for s in sorted(self.alive(), key=lambda s: (s not in self.humans(), s)):
                    kind = mapping.get(self.player(s)["role"])
                    if label == "狼队投票" and self.player(s)["role"] == "gargoyle" and self.night_wolves() == self.wolves():
                        kind = "wolf"
                    if (kind == "hunt" and self.g["day"] == 1 or kind in NIGHT_SKILLS and not self.has_skill(s)
                        or kind == "aura" and self.player(s).get("fox_spent")
                        or kind == "potion" and not self.player(s)["antidote"] and not self.player(s)["poison"]):
                        kind = None
                    if s in self.humans():
                        self.enqueue(self.action(kind or "night_pass", s))
                    elif kind:
                        self.enqueue(self.action(kind, s))
                if label == "狼队投票":
                    self.enqueue(self.action("wolf_resolve"))
            self.enqueue(self.action("host", text="夜间行动结束，天亮请睁眼。", stage="dawn"), self.action("dawn"))
            return
        for role, kind in (("dreamer", "dream"), ("guard", "guard"), ("gargoyle", "sculpt"), ("wolf", "wolf"), ("wolfbeauty", "charm"),
                           ("seer", "inspect"), ("fox", "aura"), ("gravekeeper", "tomb"),
                           ("witch", "potion"), ("raven", "curse"), ("demonhunter", "hunt"), ("silencer", "mute")):
            if kind == "hunt" and self.g["day"] == 1:
                continue
            if role != "wolf" and role not in self.public_cards():
                continue
            # 按公开配牌喊角色，即使该身份已死亡，也不通过主持提示泄露死亡身份。
            label = "狼人" if role == "wolf" else ROLES[role]["name"]
            self.enqueue(self.action("host", text="%s请睁眼，%s。" % (label, "商议今晚的袭击" if role == "wolf" else "进行本夜行动"), stage=kind))
            if role == "wolf":
                self.enqueue(*(self.action("peek", s) for s in self.alive() if self.player(s)["role"] == "girl"))
            seats = self.night_wolves() if role == "wolf" else [s for s in self.alive() if self.player(s)["role"] == role]
            if role == "wolf":
                self.enqueue(*(self.action("wolf_chat", s) for s in seats if self.player(s)["role"] != "gargoyle"))
            for s in seats:
                self.enqueue(self.action(kind, s))
            if role == "wolf":
                self.enqueue(self.action("wolf_resolve"))
            self.enqueue(self.action("host", text="%s请闭眼。" % label, stage="closed"))
        self.enqueue(self.action("host", text="夜间行动结束，天亮请睁眼。", stage="dawn"))
        self.enqueue(self.action("dawn"))

    def public_cards(self):
        return self.g.get("cards") or sorted(p["role"] for p in self.g["players"])

    def night_wolves(self):
        pack = [s for s in self.wolves() if self.player(s)["role"] != "gargoyle"]
        return pack or self.wolves()

    def has_skill(self, seat):
        return not self.g.get("gods_disabled") or ROLES[self.player(seat)["role"]]["camp"] == "wolf"

    def neighbors(self, seat):
        alive = self.alive()
        index = alive.index(seat)
        return list(dict.fromkeys([alive[(index - 1) % len(alive)], alive[(index + 1) % len(alive)]]))

    def view(self, seat=None):
        if not self.g:
            return None
        locked = seat is None and self.shared() and self.g["human"] is None
        seat = seat or self.g["human"]
        me = self.player(seat) if seat else {"role": "villager", "private": [], "alive": False, "antidote": False, "poison": False}
        end = bool(self.g["winner"])
        wolf_view = not locked and ROLES[me["role"]]["camp"] == "wolf" and me["role"] != "gargoyle"
        learned = {item["target"]: item["result"] for item in me["private"] if item.get("kind") == "sculpt"}
        inspected = {item["target"]: "wolf" if item["result"] == "狼人" else "good"
                     for item in me["private"] if item.get("kind") == "inspect"}
        peeked = {s for item in me["private"] if item.get("kind") == "peek" for s in item.get("targets", [])}
        night_vision = (self.g["phase"] == "night" and not self.g["paused"] and me["alive"] and me["role"] == "girl"
                        and self.has_skill(seat) and seat in self.g["night"].get("peekers", [])
                        and (self.g.get("host") or {}).get("stage") == "wolf")
        players = []
        for p in self.g["players"]:
            known = end or p["seat"] == seat or p["revealed"] or p["seat"] in learned or (wolf_view and ROLES[p["role"]]["camp"] == "wolf" and p["role"] != "gargoyle")
            players.append({"seat": p["seat"], "name": p["name"], "alive": p["alive"], "can_vote": p["can_vote"],
                            "role": p["role"] if known else None, "sheriff": p["seat"] == self.g["sheriff"],
                            "known_camp": inspected.get(p["seat"], "wolf" if p["seat"] in peeked else None),
                            "muted": p["seat"] == self.g["night"].get("mute") and self.g["phase"] in ("day", "discussion")})
        pending = self.g["waiting"]
        waiting = pending if pending and (pending["kind"] in ("playback", "discussion") or not locked and pending["seat"] == seat) else None
        night_chat = self.g["night"].get("wolf_chat", []) if wolf_view else []
        return {"id": self.g["id"], "revision": self.g.get("revision", 0), "count": self.g["count"], "mode": self.g["mode"], "day": self.g["day"],
                "phase": self.g["phase"], "paused": self.g["paused"], "error": self.g["error"], "winner": self.g["winner"],
                "human": seat or 0, "human_count": len(self.humans()), "humans": self.humans(),
                "locked": locked, "handoff": pending["seat"] if locked and pending and pending["kind"] not in ("playback", "discussion") else None,
                "players": players, "events": self.g["events"], "private": me["private"],
                "wolf_chat": night_chat, "waiting": waiting, "live": self.live, "rules": rules(self.g["count"], self.g["mode"]),
                "my_role": "" if locked else me["role"], "potions": {"antidote": me["antidote"], "poison": me["poison"]},
                "can_duel": me["role"] == "knight" and me["alive"] and self.has_skill(seat) and not me.get("duel_used"),
                "last_ballot": self.g.get("last_ballot"), "read_aloud": self.g.get("read_aloud", False),
                "cards": self.public_cards(), "random_gods": self.g.get("random_gods", False),
                "skill_disabled": not self.has_skill(seat) if seat else False, "api_wait": self.g.get("api_wait"), "host": self.g.get("host"),
                "night_vision": night_vision, "discussion_until": self.g.get("discussion_until"),
                "discussion_seconds": self.g.get("discussion_seconds"),
                "can_takeover": self.can_takeover(), "takeover_choices": list((self.g.get("takeover") or {}).get("offers", {}))}

    def can_takeover(self):
        if not self.g or self.shared() or self.g["winner"] or self.player(self.g["human"])["alive"]:
            return False
        pending = any(a.get("seat") == self.g["human"] and a["kind"] in ("last", "badge", "shoot") for a in self.g["queue"])
        return not pending and any(p["alive"] and not p["revealed"] for p in self.g["players"])

    async def prepare_takeover(self):
        if not self.can_takeover():
            raise GameError("请先完成遗言、开枪或选择下一位警长；游戏结束或没有未翻牌角色时不能接管。")
        if self.g.get("takeover"):
            return
        was_running = not self.g["paused"]
        await self.pause()
        seats = [s for s in self.alive() if not self.player(s)["revealed"]]
        random.shuffle(seats)
        # 背面牌只使用随机令牌，不向选择界面发送座位、姓名或身份。
        self.g["takeover"] = {"offers": {secrets.token_hex(16): s for s in seats}, "resume": was_running}
        self.save()
        await self.broadcast()

    async def takeover(self, token=None):
        offer = self.g.get("takeover") if self.g else None
        if not offer:
            raise GameError("当前没有接管选择。")
        if token is not None:
            seat = offer["offers"].get(token)
            if not seat or not self.player(seat)["alive"] or self.g["winner"]:
                raise GameError("背面牌已失效，请重新选择。")
            previous = self.g["human"]
            self.player(previous)["name"] = "松柏" if previous == 1 else NAMES[previous - 1]
            self.g["human"] = seat
            self.g["humans"] = [seat]
            self.player(seat)["name"] = "你"
            for action in self.g["queue"]:
                if action["kind"] == "playback":
                    action["seat"] = seat
            if self.g["waiting"] and self.g["waiting"]["kind"] == "playback":
                self.g["waiting"]["seat"] = seat
            self.public("真人已从 %s 号转为接管 %s 号。身份与技能状态保持原样。" % (previous, seat))
        self.g["takeover"] = None
        self.save()
        if offer["resume"]:
            await self.resume()
        else:
            await self.broadcast()

    async def broadcast(self, kind="state", payload=None):
        message = {"type": kind, "data": payload if payload is not None else self.view()}
        for q in list(self.listeners):
            if q.full():
                self.listeners.discard(q)
            else:
                q.put_nowait(message)

    def kick(self):
        if self.g and not self.g["paused"] and not self.g["winner"] and not self.g["waiting"] and (not self.task or self.task.done()):
            self.task = asyncio.create_task(self.run())

    async def pause(self):
        if not self.g:
            return
        self.g["paused"] = True
        if self.g.get("discussion_until"):
            self.g["discussion_seconds"] = max(0, self.g["discussion_until"] - time.time())
            self.g["discussion_until"] = None
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.speech_cache:
            audio = self.speech_cache[1]
            if not audio.done() or audio.cancelled() or audio.exception() is not None:
                self.discard_speech()
        self.live = None
        self.g["api_wait"] = None
        self.save()
        await self.broadcast()

    async def resume(self):
        if self.g:
            if not self.config["api_key"]:
                raise GameError("请先在接口设置中填写你自己的 API Key。")
            if self.g.get("takeover"):
                raise GameError("请先确认接管或选择继续观战。")
            self.g["paused"] = False
            self.g["error"] = ""
            if self.g["waiting"] and self.g["waiting"]["kind"] == "discussion":
                self.g["waiting"] = None
            self.save()
            self.kick()
            await self.broadcast()

    async def clear_history(self):
        await self.pause()
        self.g = None
        self.live = None
        self.discard_speech()
        self.previews.clear()
        self.db.execute("DELETE FROM games")
        self.db.commit()
        self.db.execute("VACUUM")
        await self.broadcast()

    async def discuss(self, value):
        if not self.g or self.g["paused"] or self.g["winner"] or not any(a["kind"] == "discussion_open" for a in self.g["queue"]):
            raise GameError("当前不在自由讨论阶段。")
        if value.get("finish"):
            if self.task and not self.task.done():
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
            self.discard_speech()
            self.live = None
            self.g["queue"] = [a for a in self.g["queue"] if a["kind"] not in ("free", "playback", "discussion_open")]
            self.g["waiting"] = None
            self.g["discussion_until"] = None
            self.public("自由讨论结束，进入独立投票。", "host", stage="vote")
        else:
            seat = value.get("seat")
            if type(seat) is not int or seat not in self.humans() or seat not in self.alive() or seat == self.g["night"].get("mute"):
                raise GameError("请选择可以发言的真人玩家。")
            a = self.action("free", seat, questioner=seat)
            self.validate(a, value)
            if not str(value.get("text", "")).strip():
                raise GameError("请输入发言。")
            self.apply(a, value)
            target = value.get("target")
            if target and target not in self.humans() and seat not in self.g.get("discussion_questions", []) and self.g["waiting"] and self.g["waiting"]["kind"] == "discussion":
                self.g.setdefault("discussion_questions", []).append(seat)
                if self.task and not self.task.done():
                    self.task.cancel()
                    try:
                        await self.task
                    except asyncio.CancelledError:
                        pass
                self.g["waiting"] = None
                self.g["queue"].insert(0, self.action("free", target, questioner=seat))
        self.save()
        await self.broadcast()
        self.kick()

    def targets(self, a):
        kind, seat = a["kind"], a.get("seat")
        alive = self.alive()
        if kind in ("wolf", "wolf_chat"):
            if self.player(seat)["role"] == "gargoyle":
                return [s for s in alive if s != seat]
            return [s for s in alive if self.player(s)["role"] != "wolfbeauty"]
        if kind == "guard":
            return [s for s in alive if s != self.g["guard_last"] and self.player(s)["role"] != "girl"]
        if kind == "charm":
            return [s for s in alive if s not in (seat, self.g.get("charm_last"))]
        if kind in ("curse", "mute", "aura"):
            return alive
        if kind == "potion" and not self.player(seat)["poison"]:
            return []
        if kind in ("inspect", "sculpt", "potion", "shoot", "explode", "dream", "hunt", "duel", "free"):
            return [s for s in alive if s != seat]
        if kind == "badge":
            return alive
        if kind in ("elect_vote", "vote"):
            candidates = a.get("targets", alive)
            return [s for s in candidates if s in alive and s != seat]
        return []

    def request(self, a):
        kind, seat = a["kind"], a["seat"]
        descriptions = {
            "guard": "今晚守护谁？可不守护，不可连续守护同一人。",
            "wolf": "与狼队商议袭击目标。可故意袭击普通狼队友，尝试让女巫救下他来骗取信任；狼美人不能成为这种目标。",
            "wolf_chat": "先与队友商议袭击谁和白天的分工。这里只提议，全部商议完成后另行投票。",
            "deal": "记住你的身份与座位。确认后自动遮屏交接；两位玩家确认后开始首夜。" if self.shared() else "这是你的身份牌。记住身份与技能，确认后开始首夜。",
            "review": "查看本次结果与自己的线索；看完后遮住屏幕，再交接手机。",
            "night_pass": "本轮没有你的主动技能。请遮住屏幕后交给下一位，其他玩家保持闭眼。",
            "peek": "狼人正在睁眼。要冒险偷看本夜行动的狼队吗？偷看期间画面亮起，有 20% 被发现风险。",
            "inspect": "今晚查验哪位玩家？",
            "potion": "选择不使用药、救人或毒人。每晚最多一瓶，不可自救。",
            "speech": "轮到你发言。结合公开信息说说你的判断。",
            "free": "自由补充：可简短回应，也可跳过。",
            "last": "请留下遗言。",
            "candidacy": "是否竞选警长？",
            "campaign": "请发表警长竞选发言。",
            "elect_vote": "投票选出警长，也可弃票。",
            "direction": "选择本轮从你左侧或右侧开始发言。",
            "vote": "选择你想投票赶走的玩家，也可不投票。",
            "shoot": "你可以开枪带走一位玩家，也可放弃。",
            "badge": "选择下一位警长，或结束本局的警长职位。",
            "debate": "平票辩论：解释为什么应该留下你。",
            "dream": "选择今晚的梦游者。可抵挡夜间伤害；连续两晚选择同一人会让其死亡。",
            "charm": "今晚魅惑谁？不能连续魅惑同一人。你出局时可牵制对方。",
            "curse": "诅咒谁？其今天放逐得票增加 0.5，也可不诅咒。",
            "hunt": "狩猎谁？猎中狼人则对方出局，猎中好人则你出局，也可不狩猎。",
            "playback": "正在朗读上一位玩家的发言；听完后自动进入下一位。",
            "sculpt": "查验另一人的具体身份；这条结果只有石像鬼知道。",
            "aura": "查验目标及其存活邻座中是否有狼，也可暂不查验。",
            "mute": "指定次日圆桌发言与质询被禁言的人，也可不使用。",
        }
        r = {**a, "targets": self.targets(a), "prompt": descriptions.get(kind, kind)}
        if kind == "wolf" and self.player(seat)["role"] == "gargoyle":
            r["prompt"] = "其他狼人已全部出局，选择今晚的独立袭击目标。"
        if a.get("questioner"):
            r["prompt"] = "回应 %s 号刚才点名你的问题，正面解释矛盾。" % a["questioner"]
        if kind == "potion":
            p = self.player(seat)
            victim = self.g["night"]["kill"] if p["antidote"] else None
            r.update({"victim": victim, "can_save": p["antidote"] and victim is not None and victim != seat, "can_poison": p["poison"]})
        if kind in ("speech", "free", "campaign", "debate"):
            r["can_explode"] = self.player(seat)["role"] in ("wolf", "whitewolf", "wolfking")
            r["explode_targets"] = [s for s in self.alive() if s != seat]
            r["whitewolf"] = self.player(seat)["role"] == "whitewolf"
        return r

    def context(self, a):
        seat = a["seat"]
        v = self.view(seat)
        p = self.player(seat)
        cards = self.public_cards()
        events = self.g["events"][-100:]
        ballot = self.g.get("last_ballot") or {}
        my_vote = {"类型": ballot.get("label"), "天": ballot.get("day"), "目标": ballot["choices"][str(seat)]} if str(seat) in ballot.get("choices", {}) else None
        # 每次只从当前玩家的可见信息构建请求，不传递裁判状态。
        return {
            "自己": {"座位": seat, "身份": ROLES[p["role"]]["name"], "技能": ROLES[p["role"]]["skill"], "性格": PERSONAS[(seat - 1) % len(PERSONAS)]},
            "规则": {k: value for k, value in rules(self.g["count"], self.g["mode"]).items() if k != "board_note"},
            "公开板子": {ROLES[r]["name"]: {"数量": cards.count(r), "技能": ROLES[r]["skill"]} for r in dict.fromkeys(cards)}, "第几天": self.g["day"],
            "公开阶段": self.g["phase"],
            "本轮尚未轮到发言（不是沉默或回避）": [item["seat"] for item in self.g["queue"]
                if a["kind"] in SPEECH_KINDS and item["kind"] == a["kind"] and item.get("seat") != seat],
            "本人最近公开投票": my_vote,
            "公开座位": v["players"], "公开发言与事件": events,
            "仅你知道": p["private"], "我的上次策略（可修正）": p.get("plan", ""),
            "本人真实查验": [item["text"] for item in p["private"] if item["text"].startswith("查验 ")],
            "本人确认的技能事实": [item for item in p["private"] if item.get("kind") in ("inspect", "sculpt", "aura", "tomb", "peek")],
            "技能可用": self.has_skill(seat) and not p.get("fox_spent", False),
            "可决斗": v["can_duel"] and self.g["phase"] in ("day", "discussion"),
            "我最近公开说过": [e["text"] for e in events if e["kind"] == "speech" and e["seat"] == seat][-2:],
            "刚刚其他玩家说过（接话参考，不要复述）": [e for e in events if e["kind"] == "speech" and e["seat"] != seat][-3:],
            "公开线索索引": self.clues(), "狼队夜间商议": v["wolf_chat"], "当前行动": self.request(a),
        }

    def clues(self):
        claims, ballots, flows = {}, [], {}
        role_names = "|".join(re.escape(r["name"]) for r in ROLES.values())
        claim_pattern = r"我(?:\d+\s*号(?:玩家)?)?(?:是|就是|真是|这张牌是|跳|身份是|拿的是)?(?:\d+\s*号(?:玩家)?[，,：: ]*)?(?:真|普通)?(" + role_names + r")"
        for e in self.g["events"]:
            if e["kind"] == "speech":
                claimed = list(re.finditer(claim_pattern, e["text"]))
                if claimed:
                    claims[str(e["seat"])] = {"声称身份": claimed[-1].group(1), "原话": e["text"], "天": e["day"]}
                own_statement = e["text"][claimed[-1].end():] if claimed and claimed[-1].group(1) == "预言家" else ""
                if not own_statement:
                    marker = re.search(r"我的警徽流|查验计划|接下来查验", e["text"])
                    if marker:
                        own_statement = e["text"][marker.start():]
                flow = re.search(r"(?:警徽流|查验计划|接下来查验)([^。！？\n]*)", own_statement)
                if flow:
                    targets = [int(s) for s in re.findall(r"\d+", re.split(r"[，,]", flow.group(1))[0])[:2]]
                    if targets:
                        flows[str(e["seat"])] = {"天": e["day"], "计划目标": targets, "原话": e["text"]}
            elif e["kind"] == "votes":
                ballots.append({"天": e["day"], "原始票型": e["text"]})
        return {"身份声明（未经验证）": claims, "公开验人计划（未经验证）": flows, "最近票型": ballots[-4:]}

    def ai_prompt(self, a):
        speech = a["kind"] in SPEECH_KINDS
        role = self.player(a["seat"])["role"]
        wolf = ROLES[role]["camp"] == "wolf"
        instruction = "你是%s号玩家，真实身份是%s；所有台词和行动都以%s号自己的位置为准。" % (
            a["seat"], ROLES[self.player(a["seat"])["role"]]["name"], a["seat"])
        cards = self.public_cards()
        instruction += "本局配牌只有：" + "、".join("%s张%s" % (cards.count(r), ROLES[r]["name"]) for r in dict.fromkeys(cards)) + "。不要安排不存在的神职或以其技能作为行动理由。"
        if self.g["count"] < 9:
            instruction += "本局不设警长，不要提议竞选警长或安排警长查验计划。"
        instruction += (
            "你在认真打一局狼人杀，目标是用信息、语言博弈和投票帮助自己的阵营赢。不是礼貌聊天或轮流总结，不能把思考外包给预言家。"
            "用符合本人性格的日常中文口语，不用圈内术语、总结格式和客套话。"
            "只知道提供给你的信息。本人技能结果是事实，别人的身份声明可能是谎言，推测不是确认；不能虚构别人说过的话或已公开的夜间目标。"
            "以本人事实、身份故事矛盾和实际投票判断；沉默、语气、座位相邻、发言顺序与票多都不能单独判狼，对真人和AI标准相同。"
            "本局轮流发言，尚未轮到的玩家没有机会说话，不能说他沉默、藏着或不肯表态；首夜没有位置规律，不能杜撰神位集中区。"
            "信息不足时也能比较选择、提出假设或主动试探，不需要等到铁证才推理；明确区分暂时倾向和确定事实。跟票一次不是罪证，但持续回避立场、投票与解释不符可以追问。"
            "不要审讯式命令、挖苦或预设对方有问题，不说“你先把xx说透”“你为啥跟着”这类无端施压的话；性格只影响表达习惯，不要求每轮争辩。"
            "保持本人查验、身份故事、票意与承诺连贯，改变立场解释新依据。被质询时回应原话；诈身份后回收应说明。提到本人用我，不评价或质问自己。"
            "查验每夜一次，白天不能加验；首夜之前没有白天发言，随机首验不算毛病。预言家只知道好人/狼人，不能验出神职。"
            "获好人查验、被救或接任警长都不赋予查验能力，也无法独立证明别人结果；质疑者不算冒充预言家，不要求普通玩家补报查验。"
            "技能失效不能新增技能结果。紧迫局争取收敛票意，不重复穷举可能；别让单句语气覆盖本人确切技能证据。"
            "plan只记简短策略摘要：当前立场、关键依据、这次想争取的回应或行动；公开text必须表达有用的判断、理由或试探，隐藏未公开身份与骗术目的。"
        )
        if wolf and role != "gargoyle":
            instruction += (
                "目标是狼队胜利。可冒充特殊角色、编造技能故事、带偏怀疑、争取别人投错人、必要时投队友换信任；不能知道未提供的真实身份。"
                "夜间商议结合现有局面决定是否需要身份冒充，也可潜伏引导、合作拉票、切割换信任或利用好人已有分歧；先谈收益再分工，不默认必须有人冒充预言家。后行动者补充或修正，不全队抢同一身份或只等信息。队友的我指该消息座位，称队友用座位号。"
                "白天身份故事连贯，冒充预言家也每夜只报一次假验人，兑现前次查验计划，新夜补新结果；私下不能把伪装当事实或把非队友当狼队。"
                "刀法比较杀神、留可被投错的好人、自刀骗解药或空刀配合身份故事的收益；自刀须有女巫可能救人的依据并接受死亡风险，不能知道女巫未公开的药量，不为表演强行自刀。白狼王寻找带走可信特殊角色的机会。"
                "商议先提案，最终投票读完整队友意见再定；target与台词的袭击目标一致，不能误填负责冒充的队友。方案矛盾时可以反对真人。"
            )
        elif not wolf:
            instruction += (
                "好人要主动找狼、保护信息源并让票有用。可通过问题让对方先给立场或承诺，再用后续回答检验；也可诈身份、暗示神职挡刀。身份试探不是系统真实技能，记住目的与回收时机，不能污染自己的真实判断。"
                "多人声称同一身份时比较各自结果、动机和投票关系，给倾向并说明什么会让你改判；没有对跳也不自动相信最先声称者。允许错判、辩护、结盟和有依据的反驳。"
            )
        # 每局每座位的阅读顺序固定，减少首项偏好，不替模型随机决定打法。
        skills = ROLE_SKILLS[role]
        skills = random.Random("%s:%s:%s" % (self.g["id"], a["seat"], role)).sample(skills, len(skills))
        instruction += "\n【本人角色 Skill：%s】\n" % ROLES[role]["name"] + "\n".join(skills)
        instruction += (
            "\n指南是可选思路，不是台词或每局必做的任务；只考虑本桌存在的角色和当前允许的行动。"
            "关键选择时比较适用打法的收益、代价与退出条件，也可自拟更符合局势的办法；不要照抄指南、机械轮换或为花样牺牲胜率。"
            "结合此前立场与当前回应推进；若诈身份或设试探，在私下plan保留目的和回收条件，下一轮追踪回应，不重复同一套开场。\n"
        )
        if speech:
            instruction += (
                "普通发言2到4句、最多140字，自由补充最多80字；需要几句就说几句。先读本轮争论与指向自己的问题，优先正面回答，再推进一个有用的点。"
                "先自己决定这轮要争取谁、反对谁或让谁回答什么，依据是什么，再开口。每次推进一种：带理由的判断、具体问题、回应质疑、可检验的承诺、身份试探、拉票或新技能信息。认同须带自己的新增判断，不复读别人整套分析。"
                "他人的查验只能作为对方的说法，不能复述成自己的查验。自己必须公布的新技能结果仍需准确说明，不为避免重复而省略关键事实。"
                "开场没有发言可分析时，可以自己开话题或做一个有目的的试探；后置位要对已有争论作判断，投票前收敛票意。不能全篇只说信息不够、认同某人、等后面的人。"
                "已有争议时，先给自己对眼前问题的解释或票意，接住具体回应；不要把追问预言家下夜查谁当通用接话，更不能用索要未来计划代替今天的判断。"
                "问后面的人要具体且能回答，如比较已有两套说法、解释自己选谁、给可检验的承诺；不把每个问题都变成逼人交身份，不为互动而硬找罪名。"
                "禁止用没验人能力、先不指人、等预言家报数后看谁跟票来凑发言，也不能换种说法重复上一位的催神职或追问。神职说法也需要判断，跟不上其票不自动等于狼。"
                "没有新证据时，可从刚才的主张挑一个真正影响投票的选择，自己给不同解释、补充适用条件或做具体试探；开场也可合理藏身份或有目的地穿神职身份。不要每句都质疑，自己查验计划与投票承诺可以直接说。"
                "调用speak：plan为最多100字的私下策略摘要；text是直接对桌上玩家说的台词，不泄露自己的骗术目的。"
                "正常发言explode=false、duel=false、target=null。"
            )
        else:
            instruction += (
                "调用 act 工具完成当前行动。"
                "守护/查验/袭击/投票/开枪/警徽/摄梦/魅惑/诅咒/狩猎/狐狸范围查验/禁言用target。女巫choice为save/poison/pass；毒人用target。"
                "偷看choice为peek/pass；竞选为yes/no；方向为left/right。"
                "text最多35字。target只可从当前行动targets中选择；无目标的行动一律target=null。"
                "只有当前行动允许的选项有效。夜间理由仅自己或狼队可见。"
                "plan记录身份故事、主要怀疑/信任对象与下一步目标，最多100字，仅你自己保存；随新证据修正。"
                "投票结合自己的发言承诺，若改票要有证据；不要每轮无理由弃票。"
            )
        instruction += "引用别人时保留原意并说明是谁声称的；报告自己的技能结果要说清实际对象与结果，未来计划不能冒充已经发生的行动。"
        return instruction

    async def ai_request(self, a, payload):
        url = self.config["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": "Bearer " + self.config["api_key"]}
        while True:
            delay = self.request_at - time.time()
            if delay > 0:
                self.g["api_wait"] = {"until": self.request_at, "reason": self.wait_reason or "平滑请求节奏"}
                self.save()
                await self.broadcast()
                await asyncio.sleep(delay)
            self.g["api_wait"] = None
            self.wait_reason = ""
            self.request_at = time.time() + API_INTERVAL
            self.g["api_next_at"] = self.request_at
            self.save()
            await self.broadcast()
            async with self.client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code == 429:
                    await response.aread()
                    try:
                        detail = response.json().get("error", {})
                        message = str(detail.get("message", "") if isinstance(detail, dict) else detail)
                    except (ValueError, AttributeError):
                        message = ""
                    message = message.replace(self.config["api_key"], "[redacted]")[:500]
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        seconds = float(retry_after)
                    except ValueError:
                        try:
                            seconds = parsedate_to_datetime(retry_after).timestamp() - time.time()
                        except (ValueError, TypeError, OverflowError):
                            seconds = 60
                    if not math.isfinite(seconds) or seconds <= 0:
                        seconds = 60
                    # 保留队首行动；限流只等待重试，不消费行动或代替 AI 决策。
                    self.request_at = time.time() + max(API_INTERVAL, seconds)
                    self.g["api_next_at"] = self.request_at
                    self.wait_reason = "AI 文本接口限流（HTTP 429），等待额度恢复后重试"
                    if "免费用户" in message:
                        self.wait_reason = "当前 API Key 的额度受限，请在接口设置核对账户额度"
                    logging.getLogger(__name__).warning(
                        "AI HTTP 429 model=%s action=%s seat=%s retry_after=%.1fs request_id=%s detail=%s",
                        self.config["model"], a["kind"], a.get("seat"), seconds,
                        response.headers.get("x-request-id", response.headers.get("request-id", "")), message)
                    self.live = None
                    continue
                response.raise_for_status()
                await response.aread()
                calls = response.json()["choices"][0]["message"].get("tool_calls", [])
                if not calls:
                    raise GameError("AI 未返回技能调用。请点击继续重新请求。")
                function = calls[0]["function"]
                return function["name"], function["arguments"]

    async def ai(self, a):
        speech = a["kind"] in SPEECH_KINDS
        context = self.context(a)
        request = context["当前行动"]
        can_explode = speech and request.get("can_explode", False)
        can_duel = speech and context["可决斗"]
        instruction = self.ai_prompt(a)
        targets = request["targets"] + ([] if a["kind"] in REQUIRED_TARGETS else [None])
        if speech:
            special_target = can_duel or (can_explode and self.player(a["seat"])["role"] == "whitewolf")
            targets = self.targets(self.action("duel", a["seat"])) + [None] if special_target else [None]
        if can_explode:
            instruction += "仅在自爆收益明确高于继续伪装时explode=true；普通狼不能带人，白狼王必须选带走目标。"
        if can_duel:
            instruction += "有足够狼证据时可duel=true并选择目标，证据不足优先保留决斗。"
        properties = {
            "plan": {"type": "string", "description": "最多100字的策略摘要：具体对象与依据、另一种解释、本次要影响的选择或回应；保留自己的伪装及待兑现承诺。不能只写等待神职信息。"},
            "text": {"type": "string", "description": "直接向玩家说的台词，最多140字、自由补充最多80字。回应问题，以带依据的判断、具体提问、身份试探或票意推进局面；不能只认同和等别人。" if speech else "用日常说法说明行动理由，最多35字；狼队夜间可写分工。"},
            "target": {"type": ["integer", "null"], "enum": targets, "description": "本次提议或投票袭击的座位，必须与text中刀谁一致；不是商议对话的队友，也不是负责伪装的队友。" if a["kind"] in ("wolf", "wolf_chat") else "目标座位号，没有目标时为null。"},
        }
        if speech:
            properties.update(explode={"type": "boolean", "enum": [False, True] if can_explode else [False]},
                              duel={"type": "boolean", "enum": [False, True] if can_duel else [False]})
        else:
            choices = {"peek": ["peek", "pass"], "candidacy": ["yes", "no"], "direction": ["left", "right"]}.get(a["kind"], ["act"])
            if a["kind"] == "potion":
                choices = ["pass"] + (["save"] if request["can_save"] else []) + (["poison"] if request["can_poison"] else [])
            properties["choice"] = {"type": "string", "enum": choices}
        name = "speak" if speech else "act"
        function = {"name": name, "description": "完成本次公开发言或身份行动",
                    "parameters": {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}}
        payload = {
            "model": self.config["model"], "messages": [{"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
            "temperature": 0.85 if speech else 0.4, "max_tokens": 4096,
            "stream": False,
            "tools": [{"type": "function", "function": function}], "tool_choice": {"type": "function", "function": {"name": name}},
        }
        if urlsplit(self.config["base_url"]).hostname == "api.agnes-ai.cn":
            payload["chat_template_kwargs"] = {"enable_thinking": True}
        if speech:
            self.live = {"seat": a["seat"], "text": "", "kind": a["kind"]}
        for attempt in range(2):
            tool_name, tool_args = await self.ai_request(a, payload)
            if tool_name != name:
                raise GameError("AI 未返回有效行动，请继续重试。")
            value = json.loads(tool_args)
            if isinstance(value.get("text"), str):
                value["text"] = plain_text(value["text"])
            issue = self.speech_issue(a, value.get("text", "")) if speech and not value.get("duel") and not value.get("explode") else ""
            if not issue:
                break
            self.discard_speech()
            if attempt:
                raise GameError("AI 台词仍未有效回应对局，请点击继续重新请求。")
            payload["messages"].extend([
                {"role": "assistant", "tool_calls": [{"id": "speech_revision", "type": "function", "function": {"name": name, "arguments": tool_args}}]},
                {"role": "tool", "tool_call_id": "speech_revision", "content": issue + "。结合当前发言给带依据的判断或一个能回答的具体问题，正面回应别人对你的质疑；保留真实技能信息，不复述检查意见，不以等别人收尾。"},
            ])
        plan = str(value.pop("plan", ""))[:120]
        if not speech:
            self.validate(a, value)
            result = value
        elif value.get("duel") is True:
            target = value.get("target")
            if not can_duel or type(target) is not int or target not in targets:
                raise GameError("骑士返回无效决斗目标，请继续重试。")
            result = {"duel": True, "target": target}
        elif value.get("explode") is True:
            target = value.get("target")
            if not can_explode or (self.player(a["seat"])["role"] == "whitewolf" and (type(target) is not int or target not in targets)):
                raise GameError("白狼王返回无效目标，请继续重试。")
            result = {"explode": True, "target": target}
        else:
            if not isinstance(value.get("text"), str):
                raise GameError("AI 未返回有效台词，请继续重新请求。")
            result = {"text": value["text"].strip()}
            self.validate(a, result)
            if self.g.get("read_aloud") and result["text"]:
                self.prepare_speech(a["seat"], result["text"])
        # 合法结果才写入本人策略；公开发言和技能事实由裁判在 apply() 中分别提交。
        self.player(a["seat"])["plan"] = plan
        return result

    def speech_issue(self, a, text):
        if not isinstance(text, str):
            return ""
        recent = [e for e in self.g["events"][-100:] if e["kind"] == "speech" and e["seat"] != a["seat"]][-3:]
        normalized = re.sub(r"\W", "", text)
        if normalized and any(normalized == re.sub(r"\W", "", e["text"]) for e in recent):
            return "台词与前面玩家完全重复，没有新回应"
        return ""

    def validate(self, a, value):
        kind = a["kind"]
        target = value.get("target")
        choice = value.get("choice", "pass")
        req = self.request(a)
        if not self.has_skill(a["seat"]) and (kind in NIGHT_SKILLS or kind == "shoot"):
            raise GameError("长老出局后，你的特殊技能已经失效。")
        if target is not None and (not isinstance(target, int) or isinstance(target, bool) or target not in req["targets"]):
            raise GameError("行动目标无效，请重新选择。")
        if kind in REQUIRED_TARGETS and target is None:
            raise GameError("本次技能需要选择一位目标。")
        if kind == "potion":
            if choice not in ("pass", "save", "poison"):
                raise GameError("请选择用药方式。")
            if choice == "save" and not req["can_save"]:
                raise GameError("本晚不能使用解药。")
            if choice == "poison" and (not req["can_poison"] or target is None):
                raise GameError("请选择有效毒药目标。")
        if kind in ("peek", "candidacy", "direction"):
            legal = {"peek": ("peek", "pass"), "candidacy": ("yes", "no"), "direction": ("left", "right")}[kind]
            if choice not in legal:
                raise GameError("请选择有效选项。")
        if kind in SPEECH_KINDS - {"free"} and not str(value.get("text", "")).strip():
            raise GameError("请输入发言。")
        if len(str(value.get("text", ""))) > 2000:
            raise GameError("发言请控制在 2000 字以内。")

    async def submit(self, value):
        if not self.g or not self.g["waiting"]:
            raise GameError("当前没有等待你的行动。")
        if self.g["paused"]:
            raise GameError("请先继续对局。")
        if self.g["waiting"]["kind"] == "playback":
            raise GameError("请等待朗读结束，或点击下一位。")
        a = self.g["queue"][0]
        if a["seat"] != self.g["human"]:
            raise GameError("当前不是你的行动。")
        self.validate(a, value)
        self.g["waiting"] = None
        self.g["queue"].pop(0)
        self.apply(a, value)
        if self.shared():
            if a["kind"] in {"inspect", "sculpt", "aura", "tomb", "peek", "potion"} and not self.g["winner"]:
                self.g["queue"].insert(0, self.action("review", a["seat"]))
                self.g["waiting"] = self.request(self.g["queue"][0])
            else:
                self.g["human"] = None
        self.save()
        await self.broadcast()
        self.kick()

    async def handoff(self, reveal=False):
        if not self.g or not self.shared():
            raise GameError("当前不是同屏多人模式。")
        w = self.g["waiting"]
        if reveal:
            if not w or w["kind"] == "playback" or self.g["human"] is not None or self.g["paused"]:
                raise GameError("当前没有待交接的玩家。")
            self.g["human"] = w["seat"]
        else:
            if w and w["seat"] == self.g["human"] and not self.g["paused"] and w["kind"] in ("deal", "review", "night_pass"):
                self.g["queue"].pop(0)
                self.g["waiting"] = None
            self.g["human"] = None
        self.save()
        await self.broadcast()
        self.kick()

    async def voice(self, enabled):
        if not self.g:
            return
        self.g["read_aloud"] = enabled
        if not enabled:
            self.discard_speech()
            self.g["queue"] = [a for a in self.g["queue"] if a["kind"] != "playback"]
            if self.g["waiting"] and self.g["waiting"]["kind"] == "playback":
                self.g["waiting"] = None
        self.save()
        await self.broadcast()
        self.kick()

    async def speech_done(self, event_id):
        w = self.g["waiting"] if self.g else None
        if w and w["kind"] == "playback" and w["event_id"] == event_id and not self.g["paused"]:
            self.discard_speech()
            self.g["queue"].pop(0)
            self.g["waiting"] = None
            self.save()
            await self.broadcast()
            self.kick()

    def discard_speech(self):
        if self.speech_cache:
            self.speech_cache[1].cancel()
            self.speech_cache = None

    def prepare_speech(self, seat, text):
        key = (self.g["id"], seat, text.strip())
        if not self.speech_cache or self.speech_cache[0] != key:
            self.discard_speech()
            audio, ready = bytearray(), asyncio.Event()
            task = asyncio.create_task(self.synthesize_speech(seat, key[2], audio, ready))

            def completed(t):
                # 异步完成回调必须唤醒音频读取者，并取走未被播放流程接收的任务异常。
                ready.set()
                if not t.cancelled():
                    t.exception()

            task.add_done_callback(completed)
            self.speech_cache = (key, task, audio, ready)
        return self.speech_cache[1]

    async def synthesize_speech(self, seat, text, audio, ready):
        voice = "zh-CN-YunxiaNeural" if seat % 2 else "zh-CN-XiaoxiaoNeural"
        try:
            async for chunk in edge_tts.Communicate(text, voice, connect_timeout=8, receive_timeout=20).stream():
                if chunk["type"] == "audio":
                    audio.extend(chunk["data"])
                    ready.set()
        except Exception as error:
            logging.getLogger(__name__).warning("Edge TTS failed seat=%s error=%s status=%s",
                                               seat, type(error).__name__, getattr(error, "status", ""))
            raise GameError("Edge 语音服务暂不可用，可点击跳过朗读；这与 AI 文本额度无关。") from error
        if not audio:
            raise GameError("语音为空，请点击跳过朗读。")
        return bytes(audio)

    async def speech_audio(self, game_id, event_id):
        w = self.g.get("waiting") if self.g else None
        if not self.g or self.g["id"] != game_id or self.g["paused"] or not w or w["kind"] != "playback" or w["event_id"] != event_id:
            raise GameError("这条发言已结束。")
        event = next(e for e in self.g["events"] if e["id"] == event_id)
        task = self.prepare_speech(event["seat"], event["text"])
        audio, ready = self.speech_cache[2:]
        offset = 0
        while True:
            if not self.g or self.g["id"] != game_id or self.g["paused"] or self.g["waiting"] != w:
                raise GameError("这条发言已结束。")
            if offset < len(audio):
                chunk, offset = bytes(audio[offset:]), len(audio)
                yield chunk
            elif task.done():
                await asyncio.shield(task)
                break
            else:
                ready.clear()
                await ready.wait()

    def wolf_wins(self, alive):
        good = [p for p in alive if ROLES[p["role"]]["camp"] == "good"]
        villagers = [p for p in good if p["role"] == "villager"]
        return not good or (self.g["count"] >= 9 and self.g["mode"] != "allgods" and (not villagers or len(villagers) == len(good)))

    def check_win(self, wolf_first=False):
        if self.g["winner"]:
            return True
        alive = [self.player(s) for s in self.alive()]
        wolves = [p for p in alive if ROLES[p["role"]]["camp"] == "wolf"]
        winner = "wolf" if wolf_first else "good" if not wolves else "wolf" if self.wolf_wins(alive) else None
        if winner:
            self.g["winner"] = winner
            self.g["phase"] = "ended"
            self.g["queue"] = []
            self.g["waiting"] = None
            self.public("好人阵营获胜！所有狼人已出局。" if winner == "good" else "狼人阵营获胜！已达成本局胜利条件。", "victory")
            return True
        return False

    def death(self, seat, cause):
        p = self.player(seat)
        if not p["alive"]:
            return []
        if cause == "lynch" and p["role"] == "idiot" and not p["revealed"] and self.has_skill(seat):
            p["revealed"] = True
            p["can_vote"] = False
            self.public("%s 号翻开白痴身份，免于本次放逐，失去投票权。" % seat)
            return []
        p["alive"] = False
        if p["role"] == "elder" and cause in ("lynch", "poison", "shot"):
            self.g["gods_disabled"] = True
            self.public("长老被投票赶走、毒杀或枪杀，好人从现在起失去特殊技能；计算胜负时仍属于原来的角色类别。")
        pending = []
        if self.g["sheriff"] == seat:
            pending.append(self.action("badge", seat))
        gun = (p["role"] == "hunter" and cause in ("kill", "lynch", "shot")) or (p["role"] == "wolfking" and cause in ("lynch", "shot"))
        if gun and self.has_skill(seat):
            p["revealed"] = True
            self.public("%s 号亮出 %s 身份。" % (seat, ROLES[p["role"]]["name"]))
            pending.append(self.action("shoot", seat))
        charm = self.g["night"].get("charm")
        if p["role"] == "wolfbeauty" and cause != "duel" and charm and self.player(charm)["alive"]:
            self.g["night"]["charm"] = None
            if cause not in ("poison", "kill", "dream", "hunted"):
                p["revealed"] = True
                self.public("%s 号狼美人出局，%s 号随之殉情，不能发动开枪技能。" % (seat, charm))
            pending.extend(self.death(charm, "charm"))
        if cause in ("lynch", "charm", "duel_failure") or (cause in ("kill", "poison", "dream", "hunted") and self.g["day"] == 1):
            pending.append(self.action("last", seat))
        return pending

    def start_day(self, start=None, direction=1):
        self.g["phase"] = "day"
        start = start or random.choice(self.alive())
        seats = [(start - 1 + i * direction) % self.g["count"] + 1 for i in range(self.g["count"])]
        seats = [s for s in seats if self.player(s)["alive"]]
        sheriff = self.g["sheriff"]
        if sheriff in seats:
            seats.remove(sheriff)
            seats.append(sheriff)
        self.g["day_speech_start"] = len(self.g["events"])
        self.public("本轮按 %s 号开始，%s依次发言。" % (seats[0], "顺时针" if direction == 1 else "逆时针"))
        self.enqueue(*(self.action("speech", s) for s in seats if s != self.g["night"].get("mute")))
        self.enqueue(self.action("discussion"))

    def vote_totals(self, weighted=False):
        counts = {}
        for voter, target in self.g["votes"].items():
            if target is not None:
                weight = 1.5 if weighted and int(voter) == self.g["sheriff"] else 1
                counts[target] = counts.get(target, 0) + weight
        curse = self.g["night"].get("curse")
        candidates = self.g.get("runoff_targets", self.alive()) if self.g["runoff"] else self.alive()
        if weighted and curse in candidates and curse in self.alive():
            counts[curse] = counts.get(curse, 0) + 0.5
        return counts

    def tally(self, weighted=False):
        counts = self.vote_totals(weighted)
        if not counts:
            return []
        maximum = max(counts.values())
        return [int(s) for s, n in counts.items() if n == maximum]

    def apply(self, a, v):
        # 这里只提交已校验的结果；真实技能信息写入本人线索，声明不能改写实际身份。
        kind, seat = a["kind"], a.get("seat")
        target, choice = v.get("target"), v.get("choice", "pass")
        n = self.g["night"]
        if kind in SPEECH_KINDS:
            text = str(v.get("text", "")).strip()
            if text:
                self.public(text, "speech", seat)
                if kind == "free" and seat in self.humans() and not a.get("questioner") and target is not None:
                    self.g["queue"].insert(0, self.action("free", target, questioner=seat))
                if self.g.get("read_aloud") and seat not in self.humans():
                    self.g["queue"].insert(0, self.action("playback", self.humans()[0], event_id=self.g["events"][-1]["id"]))
        elif kind == "guard":
            n["guard"] = target
            self.g["guard_last"] = target
            self.private(seat, "今晚守护 %s。" % (str(target) + " 号" if target else "无人"))
        elif kind in ("dream", "charm", "curse", "mute"):
            n[kind] = target
            if kind in ("dream", "charm"):
                self.g[kind + "_last"] = target
            self.private(seat, "今晚%s：%s。" % ({"dream": "摄梦", "charm": "魅惑", "curse": "诅咒", "mute": "禁言"}[kind], str(target) + " 号" if target else "不使用"))
        elif kind == "hunt":
            n["hunt"] = {"seat": seat, "target": target} if target else None
            self.private(seat, "今晚狩猎 %s。" % (str(target) + " 号" if target else "不使用"))
        elif kind in ("wolf", "wolf_chat"):
            if kind == "wolf":
                n["wolf_votes"][str(seat)] = target
            line = {"seat": seat, "text": str(v.get("text", "")), "target": target}
            n["wolf_chat"].append(line)
            self.private(seat, "今晚%s %s。" % ("投票袭击" if kind == "wolf" else "提议袭击", str(target) + " 号" if target else "无人"))
        elif kind == "peek":
            if choice == "peek":
                n.setdefault("peekers", []).append(seat)
                pack = self.night_wolves()
                self.private(seat, "偷看到本夜行动的狼人阵营：%s 号。具体狼职未知。" % "、".join(map(str, pack)),
                             kind="peek", targets=pack, result="狼人")
            else:
                self.private(seat, "今晚没有偷看。")
        elif kind == "inspect":
            result = "狼人" if target in self.wolves() else "好人"
            self.private(seat, "查验 %s 号：%s。" % (target, result), kind=kind, target=target, result=result)
        elif kind == "sculpt":
            result = self.player(target)["role"]
            self.private(seat, "查验 %s 号：%s。" % (target, ROLES[result]["name"]), kind=kind, target=target, result=result)
        elif kind == "aura":
            if target is None:
                self.private(seat, "今晚未使用狐狸查验。")
            else:
                group = list(dict.fromkeys([target] + self.neighbors(target)))
                result = "有狼" if any(s in self.wolves() for s in group) else "全部好人"
                self.player(seat)["fox_spent"] = result == "全部好人"
                self.private(seat, "狐狸查验 %s 号：%s。%s" % ("、".join(map(str, group)), result, "查验能力已用完。" if result == "全部好人" else "查验能力保留。"),
                             kind=kind, targets=group, result=result)
        elif kind == "tomb":
            victim = self.g.get("last_lynched")
            if victim:
                self.private(seat, "守墓结果：昨日放逐 %s 号，属于%s。" % (victim["seat"], victim["camp"]),
                             kind=kind, target=victim["seat"], result=victim["camp"])
        elif kind == "potion":
            if choice == "save":
                n["save"] = True
                self.player(seat)["antidote"] = False
            if choice == "poison":
                n["poison"] = target
                self.player(seat)["poison"] = False
            self.private(seat, "今晚%s。" % ("救了 %s 号" % n["kill"] if choice == "save" else "毒了 %s 号" % target if choice == "poison" else "没有用药"))
        elif kind == "candidacy":
            if choice == "yes":
                self.g["candidates"].append(seat)
        elif kind in ("vote", "elect_vote"):
            self.g["votes"][str(seat)] = target
            self.private(seat, "你%s：%s。" % ("投警长票" if kind == "elect_vote" else "投放逐票", str(target) + " 号" if target else "弃票"))
        elif kind == "direction":
            start = (seat - 1 + (1 if choice == "right" else -1)) % self.g["count"] + 1
            self.start_day(start, 1 if choice == "right" else -1)
        elif kind == "badge":
            self.g["sheriff"] = target
            self.public("%s 号选择 %s 号接任警长。" % (seat, target) if target else "%s 号决定不再设警长。" % seat)
        elif kind == "shoot":
            pending = []
            if target:
                self.public("%s 号开枪带走 %s 号。" % (seat, target))
                pending = self.death(target, "shot")
            else:
                self.public("%s 号放弃开枪。" % seat)
            if not self.check_win():
                self.g["queue"][0:0] = pending

    def automatic(self, a):
        kind = a["kind"]
        if kind == "host":
            self.public(a["text"], "host", stage=a["stage"])
        elif kind == "wolf_resolve":
            votes = self.g["night"]["wolf_votes"]
            counts = {}
            for t in votes.values():
                if t is not None:
                    counts[t] = counts.get(t, 0) + 1
            if counts:
                top = max(counts.values())
                self.g["night"]["kill"] = random.choice([s for s, c in counts.items() if c == top])
            target = self.g["night"]["kill"]
            self.g["night"]["wolf_chat"].append({"seat": None, "target": target,
                "text": "狼队投票：" + "，".join("%s→%s" % (s, t if t else "空刀") for s, t in votes.items()) +
                        "。最高票袭击%s；最高票平票时随机决定。" % (str(target) + "号" if target else "无人")})
            for seat in self.g["night"].get("peekers", []):
                if random.random() < 0.2:
                    self.g["night"].update(kill=seat, caught=True)
                    self.private(seat, "偷看被发现！狼人将袭击你。")
                    for s in self.night_wolves():
                        self.private(s, "%s 号小女孩偷看被发现，今晚袭击改为她。" % seat)
        elif kind == "dawn":
            if not self.g["election_done"]:
                self.g["phase"] = "election"
                self.g["candidates"] = []
                self.enqueue(*(self.action("candidacy", s) for s in self.alive()), self.action("campaign_start"))
                self.public("首夜已结束，先进行警长竞选。昨夜死亡将在竞选结束后公布。")
            else:
                self.resolve_night()
        elif kind == "campaign_start":
            c = self.g["candidates"]
            self.public("警长候选人：" + ("、".join("%s 号" % s for s in c) if c else "无人参加竞选"))
            self.enqueue(*(self.action("campaign", s) for s in c), self.action("election_vote_start"))
        elif kind == "election_vote_start":
            c = self.g["candidates"]
            if len(c) <= 1:
                self.finish_election(c[0] if c else None)
            else:
                self.g["votes"] = {}
                electorate = [s for s in self.alive() if s not in self.g.get("initial_candidates", c)]
                self.enqueue(*(self.action("elect_vote", s, targets=c) for s in electorate), self.action("election_result"))
        elif kind == "election_result":
            self.reveal_votes("警长投票")
            tied = self.tally()
            if len(tied) == 1:
                self.finish_election(tied[0])
            elif tied and not self.g["election_runoff"]:
                self.g["initial_candidates"] = self.g["candidates"][:]
                self.g["candidates"] = tied
                self.g["election_runoff"] = True
                self.public("警长票平票，候选人再发言。")
                self.enqueue(*(self.action("campaign", s) for s in tied), self.action("election_vote_start"))
            else:
                self.finish_election(None)
        elif kind == "day_start":
            if self.check_win():
                return
            self.g["phase"] = "day"
            sheriff = self.g["sheriff"]
            if sheriff and self.player(sheriff)["alive"]:
                self.enqueue(self.action("direction", sheriff))
            else:
                self.start_day()
        elif kind == "discussion":
            self.g["phase"] = "discussion"
            events = self.g["events"][self.g.get("day_speech_start", 0):]
            mentions = Counter(int(s) for e in events if e["kind"] == "speech" for s in re.findall(r"(\d+)\s*号", e["text"]) if int(s) != e["seat"])
            claims = self.clues()["身份声明（未经验证）"]
            counts = Counter(c["声称身份"] for s, c in claims.items() if int(s) in self.alive() and c["声称身份"] != "村民")
            focus = [int(s) for s, c in claims.items() if c["声称身份"] != "村民" and counts[c["声称身份"]] > 1]
            focus += [s for s, n in mentions.most_common() if n >= 2]
            humans = self.humans()
            muted = self.g["night"].get("mute")
            seats = list(dict.fromkeys(s for s in focus if s in self.alive() and s not in humans and s != muted))[:4]
            self.public("进入焦点质询：%s。回应具体矛盾即可；真人可点名追问一次。" % ("、".join("%s 号" % s for s in seats) if seats else "本轮无额外回应"))
            self.g["discussion_seconds"] = 90
            self.g["discussion_until"] = None
            self.g["discussion_questions"] = []
            self.enqueue(*(self.action("free", s) for s in seats), self.action("discussion_open"), self.action("vote_start"))
        elif kind == "vote_start":
            self.g["phase"] = "vote"
            self.g["votes"] = {}
            self.g["runoff"] = False
            self.g["last_lynched"] = None
            self.public("发言结束，所有存活且有投票权的玩家同时选择放逐对象。")
            self.enqueue(*(self.action("vote", s) for s in self.alive() if self.player(s)["can_vote"]), self.action("vote_result"))
        elif kind == "vote_result":
            self.reveal_votes("放逐投票")
            tied = self.tally(True)
            if len(tied) == 1:
                self.public("%s 号被投票放逐。" % tied[0])
                pending = self.death(tied[0], "lynch")
                if not self.player(tied[0])["alive"]:
                    self.g["last_lynched"] = {"seat": tied[0], "camp": "狼人" if ROLES[self.player(tied[0])["role"]]["camp"] == "wolf" else "好人"}
                if not self.check_win():
                    self.enqueue(*pending, self.action("next_night"))
            elif tied and not self.g["runoff"]:
                self.g["runoff"] = True
                self.g["runoff_targets"] = tied
                self.g["votes"] = {}
                self.public("最高票平票，进入候选人辩论与第二轮投票。")
                self.enqueue(*(self.action("debate", s) for s in tied))
                self.enqueue(*(self.action("vote", s, targets=tied) for s in self.alive() if s not in tied and self.player(s)["can_vote"]))
                self.enqueue(self.action("vote_result"))
            else:
                self.public("本轮无人被放逐。" + ("二次投票仍平票。" if tied else "所有玩家弃票。"))
                self.enqueue(self.action("next_night"))
        elif kind == "next_night":
            if not self.check_win():
                self.g["day"] += 1
                self.start_night()

    def reveal_votes(self, label):
        parts = ["%s→%s" % (s, t if t else "弃票") for s, t in self.g["votes"].items()]
        self.public(label + "：" + "，".join(parts), "votes")
        self.g["last_ballot"] = {"label": label, "day": self.g["day"], "choices": copy.deepcopy(self.g["votes"]),
                                 "totals": self.vote_totals(label == "放逐投票")}

    def finish_election(self, seat):
        self.g["sheriff"] = seat
        self.g["election_done"] = True
        self.public("%s 号当选警长。" % seat if seat else "本局没有警长。")
        self.resolve_night()

    def resolve_night(self):
        n = self.g["night"]
        deaths = {}
        kill = n["kill"]
        guarded = kill is not None and kill == n["guard"] and not n["caught"]
        saved = n["save"]
        killed = kill is not None and (guarded == saved) and kill != n.get("dream")
        if killed and self.player(kill)["role"] == "elder" and self.has_skill(kill) and not self.player(kill).get("elder_hit"):
            self.player(kill)["elder_hit"] = True
            self.private(kill, "长老抵挡了首次实际狼刀，后续狼刀不再免疫。")
            killed = False
        if killed:
            deaths[kill] = "kill"
        if n["poison"] is not None and self.player(n["poison"])["role"] != "demonhunter":
            deaths[n["poison"]] = "poison"
        dream = n.get("dream")
        if dream:
            deaths.pop(dream, None)
            if dream == n.get("dream_previous"):
                deaths[dream] = "dream"
            if any(self.player(s)["role"] == "dreamer" for s in deaths):
                deaths[dream] = "dream"
        hunt = n.get("hunt")
        if hunt:
            victim = hunt["target"] if hunt["target"] in self.wolves() else hunt["seat"]
            if victim != dream:
                deaths[victim] = "hunted"
        charm = n.get("charm")
        if charm and any(self.player(s)["role"] == "wolfbeauty" for s in deaths):
            deaths[charm] = "charm"
        deaths = {s: cause for s, cause in deaths.items() if self.player(s)["alive"]}
        wolf_first = killed and self.wolf_wins([self.player(s) for s in self.alive() if s != kill])
        self.g["phase"] = "day"
        self.public("天亮请睁眼。昨晚" + "、".join("%s 号" % s for s in sorted(deaths)) + "玩家出局。" if deaths else "天亮请睁眼，昨晚是平安夜。", "host", stage="day")
        if n.get("curse") in self.alive() and n.get("curse") not in deaths:
            self.public("%s 号被乌鸦标记，今日放逐得票额外增加 0.5。" % n["curse"])
        pending = []
        for s, cause in deaths.items():
            pending.extend(self.death(s, cause))
        if "bear" in self.public_cards():
            bears = [s for s in self.alive() if self.player(s)["role"] == "bear" and self.has_skill(s)]
            roars = any(neighbor in self.wolves() for s in bears for neighbor in self.neighbors(s))
            self.public("熊咆哮了：驯熊师的存活邻座中有狼人。" if roars else "熊很安静。")
        if n.get("mute") in self.alive():
            self.public("%s 号今天跳过圆桌发言与焦点质询，仍可投票。" % n["mute"])
        if not self.check_win(wolf_first):
            self.enqueue(*pending, self.action("day_start"))

    async def duel(self, target):
        seat = self.g["human"] if self.g else None
        if not seat or self.g["paused"] or self.g["winner"] or self.g["phase"] not in ("day", "discussion"):
            raise GameError("只能在白天发言或质询阶段决斗。")
        p = self.player(seat)
        if not p["alive"] or p["role"] != "knight" or not self.has_skill(seat) or p.get("duel_used"):
            raise GameError("当前不能发动骑士决斗。")
        if type(target) is not int or target not in self.targets(self.action("duel", seat)):
            raise GameError("请选择有效决斗目标。")
        await self.pause()
        self.do_duel(seat, target)
        if self.shared():
            self.g["human"] = None
        await self.resume()

    def do_duel(self, seat, target):
        p = self.player(seat)
        p["revealed"] = True
        p["duel_used"] = True
        hit = target in self.wolves()
        self.public("%s 号骑士翻牌决斗 %s 号，%s。" % (seat, target, "对方是狼人，立即进入黑夜" if hit else "对方是好人，骑士出局，白天继续"))
        if hit:
            self.player(target)["revealed"] = True
            self.g["queue"] = []
            self.g["waiting"] = None
            pending = self.death(target, "duel")
            if not self.check_win():
                self.enqueue(*pending, self.action("next_night"))
        else:
            pending = self.death(seat, "duel_failure")
            self.g["queue"] = [a for a in self.g["queue"] if a.get("seat") != seat]
            if self.g["waiting"] and self.g["waiting"]["seat"] == seat:
                self.g["waiting"] = None
            if not self.check_win():
                self.g["queue"][0:0] = pending

    async def explode(self, target):
        if not self.g or self.g["paused"] or self.g["winner"] or self.g["phase"] not in ("day", "discussion", "election"):
            raise GameError("只能在白天发言或竞选阶段自爆。")
        seat = self.g["human"]
        if not seat:
            raise GameError("请先领取当前玩家的手机视角。")
        p = self.player(seat)
        if not p["alive"] or p["role"] not in ("wolf", "wolfking", "whitewolf"):
            raise GameError("当前身份不能自爆。")
        if p["role"] == "whitewolf" and (target not in self.alive() or target == seat):
            raise GameError("白狼王需要选择带走目标。")
        await self.pause()
        self.do_explode(seat, target)
        if self.shared():
            self.g["human"] = None
        await self.resume()

    def do_explode(self, seat, target=None):
        p = self.player(seat)
        self.g["queue"] = []
        self.g["waiting"] = None
        self.public("%s 号狼人自爆，白天立即结束。" % seat)
        p["revealed"] = True
        pending = self.death(seat, "explode")
        if p["role"] == "whitewolf" and target:
            self.public("白狼王带走 %s 号。" % target)
            pending.extend(self.death(target, "white_explode"))
        if self.check_win():
            return
        if not self.g["election_done"]:
            self.g["election_done"] = True
            self.public("竞选被自爆中断，本局没有警长。")
            self.resolve_night()
            self.g["queue"] = [a for a in self.g["queue"] if a["kind"] != "day_start"]
        if not self.g["winner"]:
            self.enqueue(*pending, self.action("next_night"))

    async def run(self):
        try:
            while self.g and not self.g["paused"] and not self.g["winner"] and self.g["queue"]:
                # 队首行动成功提交后才移除；限流、暂停和请求失败都保留这个断点。
                a = self.g["queue"][0]
                seat = a.get("seat")
                if a["kind"] == "discussion_open":
                    if not self.g.get("discussion_until"):
                        self.g["discussion_until"] = time.time() + self.g.get("discussion_seconds", 90)
                        self.public("自由讨论开始，限时 90 秒。可随时点击直接投票。", "host", stage="discussion")
                    if self.shared():
                        self.g["human"] = None
                    self.g["waiting"] = {"kind": "discussion", "seat": 0, "prompt": "自由讨论 · 选择发言玩家", "targets": self.alive()}
                    self.save()
                    await self.broadcast()
                    await asyncio.sleep(max(0, self.g["discussion_until"] - time.time()))
                    self.g["queue"].pop(0)
                    self.g["waiting"] = None
                    self.g["discussion_until"] = None
                    self.public("讨论时间到，进入独立投票。", "host", stage="vote")
                elif seat is None:
                    self.g["queue"].pop(0)
                    self.automatic(a)
                elif not self.player(seat)["alive"] and a["kind"] not in ("badge", "shoot", "last", "playback"):
                    self.g["queue"].pop(0)
                elif (a["kind"] in NIGHT_SKILLS or a["kind"] == "shoot") and not self.has_skill(seat):
                    self.g["queue"].pop(0)
                elif a["kind"] == "aura" and self.player(seat).get("fox_spent"):
                    self.g["queue"].pop(0)
                elif a["kind"] == "tomb" and not (self.shared() and seat in self.humans()):
                    self.g["queue"].pop(0)
                    self.apply(a, {})
                elif a["kind"] == "potion" and not self.player(seat)["antidote"] and not self.player(seat)["poison"]:
                    self.g["queue"].pop(0)
                elif a["kind"] == "playback" or seat in self.humans():
                    self.g["waiting"] = self.request(a)
                    self.save()
                    await self.broadcast()
                    return
                else:
                    result = await self.ai(a)
                    self.live = None
                    if result.get("duel"):
                        self.do_duel(seat, result["target"])
                    elif result.get("explode"):
                        self.do_explode(seat, result.get("target"))
                    else:
                        self.g["queue"].pop(0)
                        self.apply(a, result)
                self.save()
                await self.broadcast()
                if a["kind"] == "host":
                    # 留出包内主持人 MP3 的完整播放时间。
                    await asyncio.sleep(3.5 if a["stage"] == "closed" else 5 if a["stage"] != "shared" else .8)
        except asyncio.CancelledError:
            self.discard_speech()
            raise
        except Exception as e:
            self.discard_speech()
            self.live = None
            self.g["paused"] = True
            if isinstance(e, GameError):
                self.g["error"] = str(e)
            elif isinstance(e, httpx.HTTPStatusError):
                self.g["error"] = "AI 接口返回 HTTP %s。请检查密钥、模型和账户额度后继续。" % e.response.status_code
            elif isinstance(e, httpx.TimeoutException):
                self.g["error"] = "AI 请求超时，对局已保存。点击继续重新请求。"
            else:
                self.g["error"] = "AI 请求或响应异常（%s），对局已保存，请继续重试。" % type(e).__name__
            self.save()
            await self.broadcast()
