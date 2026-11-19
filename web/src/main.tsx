import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type Role = { name: string; camp: string; skill: string; color: string; icon: string };
type Player = { seat: number; name: string; alive: boolean; can_vote: boolean; role: string | null; sheriff: boolean; muted?: boolean; known_camp?: string | null };
type Event = { id: number; day: number; phase: string; kind: string; seat: number | null; text: string };
type Waiting = { kind: string; seat: number; prompt: string; targets: number[]; event_id?: number; victim?: number; can_save?: boolean; can_poison?: boolean; can_explode?: boolean; whitewolf?: boolean };
type Clue = { day: number; text: string; kind?: string; target?: number; targets?: number[]; result?: string };
// 后端只有一份对局视图；单人、双人使用相同结构，避免再拼接独立的共享状态类型。
type Game = {
  id: string; day: number; phase: string; paused: boolean; error: string; winner: string | null;
  human: number; human_count: number; humans: number[]; locked: boolean; handoff: number | null;
  revision: number; count: number; mode: string; my_role: string;
  players: Player[]; events: Event[]; private: Clue[];
  wolf_chat: { seat: number; text: string; target: number }[];
  waiting: Waiting | null; live: { seat: number; text: string; kind: string } | null;
  rules: Rules; potions: { antidote: boolean; poison: boolean }; can_duel: boolean; read_aloud: boolean;
  last_ballot: { label: string; day: number; choices: Record<string, number | null>; totals: Record<string, number> } | null;
  cards: string[]; random_gods: boolean; skill_disabled: boolean; api_wait: {until: number; reason: string} | null;
  can_takeover: boolean; takeover_choices: string[]; host: (Event & {stage: string}) | null;
  night_vision: boolean; discussion_until?: number; discussion_seconds?: number;
};
type Mode = { name: string; note: string; min: number };
type Rules = { win: string; discussion: string; night: string; sheriff_rule: string; board_note: string; sheriff: boolean };
type Board = { count: number; mode: string; roles: string[]; rules: Rules; token?: string; selected_gods?: string[]; fixed_gods: string[]; god_slots: number; selectable_gods: string[] };
type Settings = { base_url: string; model: string; configured: boolean };

declare global {
  interface Window {
    moonVoice?: (v: { text?: string; recording?: boolean; error?: string }) => void;
    MoonAndroid?: { voice: (action: string) => void };
  }
}

async function api(path: string, value?: object) {
  const r = await fetch("/api/" + path, value === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || "请求失败");
  return data;
}

function stopSpeech() {
  if (speechSource) {
    speechSource.pause();
    speechSource.removeAttribute("src");
    speechSource.load();
  }
  speechSource = null;
}

const COLORS = ["#c78f71", "#a4b88a", "#cbad79", "#b39fc9", "#d59aa0", "#83b6b2", "#c7a365", "#98a8c5", "#b3bb8b", "#c58eab", "#91b2c5", "#caa57f", "#ad9fc7", "#b9c482", "#d28e76"];
const REQUIRED_TARGETS = new Set(["inspect", "dream", "charm", "sculpt"]);
const SEAT_ACTIONS: Record<string, { title: string; mark: string; verb: string; skip?: string; hint: string }> = {
  inspect: { title: "预言家 · 星盘查验", mark: "✦", verb: "查验阵营", hint: "点座位查验，只揭晓狼人或好人阵营。" },
  sculpt: { title: "石像鬼 · 身份洞察", mark: "◆", verb: "查验身份", hint: "点座位，查验结果只向你翻牌。" },
  shoot: { title: "最后一枪", mark: "⌖", verb: "开枪带走", skip: "放弃开枪", hint: "点座位瞄准；确认后开枪，不能撤回。" },
  potion: { title: "女巫 · 双瓶药剂", mark: "⚗", verb: "使用药剂", skip: "今晚留药", hint: "先选药瓶，再点目标；解药只能救今晚被狼人袭击的人，每晚最多一瓶。" },
  guard: { title: "守卫 · 守护结界", mark: "◇", verb: "守护", skip: "今晚不守护", hint: "点座位守护；不能连续守护同一人，不能守小女孩。" },
  dream: { title: "摄梦人 · 梦境连线", mark: "☾", verb: "牵入梦境", hint: "点座位让其梦游；连续两夜选择同一人会让其死亡。" },
  charm: { title: "狼美人 · 魅惑印记", mark: "❧", verb: "魅惑", hint: "点座位魅惑；不能连续魅惑同一人。" },
  curse: { title: "乌鸦 · 半票诅咒", mark: "♧", verb: "施加诅咒", skip: "不诅咒", hint: "点座位留下印记，次日放逐票额外加 0.5。" },
  hunt: { title: "猎魔人 · 夜间狩猎", mark: "⌖", verb: "狩猎", skip: "今晚不狩猎", hint: "命中狼人则对方出局；命中好人则你出局。" },
  aura: { title: "狐狸 · 邻座感知", mark: "◈", verb: "查验范围", skip: "保留查验", hint: "点座位查验其及存活邻座；全好人后失去技能。" },
  mute: { title: "禁言长老 · 缄默封印", mark: "∅", verb: "施加禁言", skip: "不禁言", hint: "点座位封印其次日圆桌发言与质询，不影响投票。" },
  wolf: { title: "狼队 · 今夜袭击", mark: "◭", verb: "确认袭击", skip: "今晚不袭击", hint: "点座位选择袭击目标，商议只向狼队展示。" },
  duel: { title: "骑士 · 翻牌决斗", mark: "⚔", verb: "翻牌决斗", hint: "点座位决斗；狼人出局并入夜，好人则骑士出局。" },
  explode: { title: "狼人 · 自爆", mark: "✷", verb: "确认自爆", hint: "亮明身份并结束白天；白狼王需点座位选择带走目标。" },
  vote: { title: "投票赶走可疑玩家", mark: "☑", verb: "投给", skip: "不投票", hint: "点座位投票，全部投完后才公开每个人投给谁。" },
  elect_vote: { title: "警长选举", mark: "♜", verb: "投警长票", skip: "弃票", hint: "点候选座位投票。" },
  badge: { title: "选择下一位警长", mark: "♜", verb: "交接警长资格", skip: "不再设警长", hint: "点选仍在场的玩家接任警长，也可让本局不再设警长。" },
  peek: { title: "小女孩 · 掀开眼罩", mark: "◉", verb: "冒险偷看", skip: "保持闭眼", hint: "偷看狼队座位，有 20% 被发现风险。" },
};
const AUTO_SKILLS: Record<string, string> = {
  bear: "黎明自动检查邻座，主持人公布熊叫；无需点选。",
  gravekeeper: "第二夜起自动获知昨日放逐者阵营，结果记入你的线索。",
  elder: "抵挡首次狼人袭击自动生效；被投票赶走、毒杀或枪杀会让好人失去特殊技能。",
  idiot: "首次被放逐时自动翻牌免死，之后失去投票权。",
  villager: "没有主动身份技能；发言和投票时参与推理。",
};

function countCards(cards: string[]) {
  const counts: Record<string, number> = {};
  for (const role of cards) counts[role] = (counts[role] || 0) + 1;
  return counts;
}

function VillageScene({ night }: { night: boolean }) {
  return <svg className="village-art" viewBox="0 0 1000 650" preserveAspectRatio={night ? "xMidYMid slice" : "none"} shapeRendering="crispEdges" aria-hidden="true">
    <defs>
      <linearGradient id="village-sky" x2="0" y2="1">
        <stop stopColor="#83bccb" /><stop offset="1" stopColor="#e9ebcd" />
      </linearGradient>
      <pattern id="village-tiles" width="32" height="18" patternUnits="userSpaceOnUse" patternTransform="scale(.75)">
        <path fill="#a66248" d="M0 0h32v18H0z" />
        <path fill="#ca8a60" d="M1 1h14v6H1zM17 1h14v6H17zM0 10h7v6H0zM9 10h14v6H9zM25 10h7v6h-7z" />
        <path fill="#e0a573" d="M1 1h14v2H1zM17 1h14v2H17zM9 10h14v2H9z" />
        <path fill="#834e3e" d="M0 8h32v2H0zM0 17h32v1H0z" />
      </pattern>
      <pattern id="village-stone" width="48" height="28" patternUnits="userSpaceOnUse" patternTransform="scale(.75)">
        <path fill="#d9caac" d="M0 0h48v28H0z" />
        <path fill="#f0e0bf" d="M1 1h21v11H1zM25 1h21v11H25zM0 15h10v11H0zM13 15h22v11H13zM38 15h10v11H38z" />
        <path fill="#c4b597" d="M4 10h16v2H4zM27 10h17v2H27zM16 24h17v2H16z" />
      </pattern>
      <g id="village-leaves">
        <path fill="#426e60" d="M8 18h10V7h17V0h18v6h18v12h9v24h-9v13H53v7H26v-7H10V44H0V27h8z" />
        <path fill="#64866a" d="M10 18h10V8h17V3h15v7h17v11h8v15H60v10H41v9H19V42H5V28h5z" />
        <path fill="#82996d" d="M20 12h18V6h13v9h12v13H49v9H30V27H13v-9h7z" />
        <path fill="#a6af7c" d="M24 12h13v6H24zM40 8h9v5h-9zM52 19h9v5h-9zM17 25h9v5h-9z" />
        <path fill="#c3bd86" d="M27 12h5v3h-5zM42 9h5v3h-5z" />
        <path fill="#54765d" d="M7 35h9v7H7zM26 43h11v6H26zM54 32h12v8H54zM44 50h8v5h-8z" />
      </g>
      <g id="village-tree">
        <path fill="#756951" d="M51 58h12v132H51zM40 95h11v7H40zM34 84h7v15h-7zM63 109h13v7H63zM73 95h7v19h-7z" />
        <path fill="#a28d65" d="M52 83h4v104h-4z" />
        <use href="#village-leaves" x="1" y="37" /><use href="#village-leaves" x="43" y="43" />
        <use href="#village-leaves" x="27" y="0" /><use href="#village-leaves" x="7" y="13" />
        <use href="#village-leaves" x="45" y="18" /><use href="#village-leaves" x="23" y="37" />
      </g>
      <g id="village-window">
        <path fill="#947a54" d="M0 0h32v39H0z" /><path fill="#385b61" d="M4 4h24v29H4z" />
        <path fill="#78989a" d="M6 5h9v12H6zM18 5h8v8h-8z" />
        <path fill="#ead8b0" d="M15 3h3v31h-3zM3 19h26v3H3z" />
        <path fill="#705744" d="M-9 1h7v35h-7zM34 1h7v35h-7z" />
        <path fill="#ac936a" d="M-8 5h5v2h-5zM-8 14h5v2h-5zM35 5h5v2h-5zM35 14h5v2h-5z" />
        <path fill="#b99a70" d="M-4 37h40v5H-4z" />
      </g>
      <g id="village-cottage">
        <path fill="#b8a789" d="M9 88h176v141H9z" /><path fill="url(#village-stone)" d="M9 94h146v127H9z" />
        <path fill="#ccb992" d="M21 81v-9h9V60h12V48h12V36h13V24h16V12h13v12h13v12h13v12h13v12h13v12h9v9z" />
        <path fill="#765844" d="M83 24h8v60h-8zM28 81h128v8H28zM11 96h8v125h-8zM145 96h9v125h-9zM11 149h143v7H11zM50 63h7v21h-7zM121 63h7v21h-7z" />
        <path fill="url(#village-tiles)" d="M0 91v-9h10V69h13V55h13V42h13V29h14V16h14V3h24v10h15v13h14v13h14v13h14v13h14v13h14v13h9v8h-26v-9h-12V77h-13V64h-13V51h-13V38h-13V25H91v-9H80v11H67v13H54v13H41v13H28v13H15v12z" />
        <path fill="#efba7c" d="M77 3h24v4H77zM64 16h13v4H64zM51 29h13v4H51zM38 42h13v4H38zM25 55h13v4H25zM12 69h13v4H12z" />
        <path fill="#845b43" d="M156 90h31v7h-31z" />
        <path fill="#ceb894" d="M139 8h18v38h-18z" /><path fill="#96785b" d="M136 7h24v6h-24zM150 13h7v31h-7z" />
        <use href="#village-window" x="32" y="104" /><use href="#village-window" x="101" y="104" />
        <path fill="#c2a47a" d="M64 219v-55h6v-9h10v-5h18v5h10v9h6v55z" />
        <path fill="#5f594b" d="M72 219v-51h6v-8h22v8h6v51z" />
        <path fill="#856b4e" d="M75 217v-47h7v-6h17v6h4v47z" />
        <path fill="#b19668" d="M82 170h3v45h-3zM94 166h2v49h-2zM73 190h31v3H73zM98 195h3v3h-3z" />
        <path fill="#eee0bd" d="M63 219h52v6H63z" /><path fill="#bcaa88" d="M58 225h62v6H58z" />
        <path fill="#819071" d="M13 200h9v-10h13v4h10v13H34v14H13zM123 182h11v-8h14v13h6v25h-12v9h-19z" />
        <path fill="#a7ae79" d="M15 196h9v7h-9zM132 179h10v8h-10zM124 201h7v7h-7z" />
        <path fill="#d2a26f" d="M26 196h4v4h-4zM133 191h4v4h-4z" />
      </g>
      <g id="village-planter">
        <path fill="#827c5e" opacity=".3" d="M-10 59h81v10h-81z" />
        <path fill="#ad7753" d="M5 34h48v12h-4v19H11V46H5z" /><path fill="#d29c66" d="M9 39h15v21H14V45H9z" />
        <path fill="#795a41" d="M7 34h44v5H7zM13 62h34v4H13z" />
        <use href="#village-leaves" transform="translate(-2 -8) scale(.78 .7)" />
        <path fill="#d79b77" d="M10 15h6v6h-6zM36 5h6v6h-6zM43 24h6v6h-6z" />
        <path fill="#edd7a3" d="M24 18h5v5h-5zM8 29h5v5H8zM32 30h5v5h-5z" />
      </g>
    </defs>
    <g className="village-day">
      <path fill="url(#village-sky)" d="M0 0h1000v650H0z" />
      <path fill="#f5edcd" opacity=".5" d="M834 22h47v8h12v44h-12v8h-47v-8h-12V30h12z" />
      <path fill="#fff2ca" d="M843 31h29v7h7v27h-7v7h-29v-7h-7V38h7z" />
      <path fill="#e0e9db" d="M32 50h18V36h36V22h30v8h25v14h19v12h30v17H32zM600 63h19V49h27V35h24v8h27v13h24v18h-121zM382 23h23V13h40v9h27v14h-90z" />
      <path fill="#fff7df" d="M40 49h23V36h24V25h27v9h23v15h18v10H40zM615 62h22V49h14V39h16v10h24v12h16v5h-92z" />
      <path fill="#9bbdb4" d="M0 160v-14h35v-17h42v-15h41v-9h28v13h35v17h37v11h43v-19h31v-14h35v-12h27v-9h30v12h30v12h33v17h32v8h47v-17h42v-13h39V94h36v13h37v18h38v13h38v-20h28v-12h41V90h36v14h38v20h40v14h51v22z" />
      <path fill="#7ea394" d="M0 181v-13h44v-15h58v11h38v16h50v-21h31v-12h28v13h44v11h40v-21h34v-17h36v13h39v15h50v20h29v-13h51v-22h42v15h49v-24h32v-12h24v19h38v20h61v-12h49v-15h30v15h39v-10h32v16h48v25z" />
      <path fill="#a3b39a" d="M0 180h1000v159H0z" />
      {[215, 284, 376, 648, 735, 821].map((x, i) => <use key={x} href="#village-cottage" transform={`translate(${x} ${158 + i % 3 * 8}) scale(.36)`} />)}
      <path fill="#91a287" d="M0 242h1000v94H0z" />
      <use href="#village-cottage" transform="translate(55 62) scale(1.13)" />
      <use href="#village-cottage" transform="translate(726 88) scale(1.08)" />
      <use href="#village-cottage" transform="translate(257 144) scale(.62)" />
      <use href="#village-cottage" transform="translate(613 152) scale(.57)" />
      <g transform="translate(578 78)">
        <path fill="url(#village-stone)" d="M0 34h35v111H0z" /><path fill="#b4a68b" d="M28 34h7v111h-7z" />
        <path fill="#a8694b" d="M-6 35v-8h7v-9h7V9h6V0h7v9h6v9h7v9h7v8z" />
        <path fill="#e0ab74" d="M14 0h7v6h-7zM8 9h6v8H8zM1 18h7v7H1z" />
        <path fill="#74634b" d="M16 -12h3V0h-3zM12 -8h11v3H12z" />
        <path fill="#547272" d="M6 47h8v21H6zM22 47h7v21h-7z" />
        <path fill="#baac8d" d="M0 77h35v4H0zM0 127h35v4H0z" />
      </g>
      <use href="#village-tree" transform="translate(-38 67) scale(1.45)" />
      <use href="#village-tree" transform="translate(220 153) scale(.64)" />
      <use href="#village-tree" transform="translate(853 94) scale(1.15)" />
      <use href="#village-tree" transform="translate(934 25) scale(1.5)" />
      <path fill="#7a8c70" d="M0 280h168v9h66v21h512v-10h89v-18h165v368H0z" />
      <path fill="#d6cdb4" d="M258 275h426l64 45 164 330H74l134-327z" />
      <path fill="#eee0bf" opacity=".3" d="M628 288h52l53 46 150 316H563z" />
      <path fill="#c0b79e" opacity=".45" d="M144 451h39v3h-39zM818 410h32v3h-32zM96 603h54v3H96zM854 584h38v3h-38z" />
      <path fill="#747c60" opacity=".25" d="M0 312h240v14h-40v28h-46v42h-47v42H0zM809 328h191v102h-104v-29h-31v-34h-56z" />
      <path fill="url(#village-stone)" d="M0 276h203v45H0zM799 287h201v45H799z" />
      <path fill="#eee0bf" d="M0 269h210v9H0zM792 280h208v9H792z" />
      <path fill="#b1a387" d="M0 316h203v6H0zM799 327h201v6H799z" />
      <path fill="url(#village-stone)" d="M0 262h23v58H0zM179 262h26v58h-26zM796 273h26v58h-26zM977 273h23v58h-23z" />
      <path fill="#f0e2c1" d="M0 257h27v7H0zM176 257h32v7h-32zM792 268h34v7h-34zM974 268h26v7h-26z" />
      <use href="#village-planter" transform="translate(32 234) scale(.85)" />
      <use href="#village-planter" transform="translate(892 245) scale(.85)" />
      <use href="#village-planter" transform="translate(110 466) scale(1.15)" />
      <use href="#village-planter" transform="translate(844 512) scale(1.25)" />
      <use href="#village-planter" transform="translate(-21 570) scale(1.75)" />
      <use href="#village-planter" transform="translate(944 595) scale(1.55)" />
      <path fill="#a5ad7b" d="M52 399h4v10h-4zM58 405h7v4h-7zM936 458h4v12h-4zM942 464h6v5h-6zM199 586h4v11h-4zM206 591h5v5h-5z" />
      <path fill="#e7cf9a" d="M33 430h4v4h-4zM954 503h5v5h-5zM72 554h5v5h-5z" />
    </g>
    <g className="village-night">
    <path className="sky-fill" d="M0 0h1000v650H0z" />
    <path className="cloud-fill" d="M50 62h30V48h74v14h35v24H50zM720 90h37V70h80v20h55v26H720zM390 36h30V24h63v12h24v17H390z" />
    <circle className="sun-fill" cx="865" cy="56" r="30" />
    <path className="hill-fill" d="M0 158 110 110 220 170 340 95 480 160 650 100 790 160 910 102 1000 150V650H0z" />
    <path className="grass-fill" d="M0 236 260 215 420 234 680 204 1000 236V650H0z" />
    <path className="lane-fill" d="M395 222h180l190 428H155z" />
    <path fill="#f4e6c4" d="M52 137h161v126H52zM779 154h165v120H779z" />
    <path fill="#ad7150" d="M36 136v-20h20V96h24V76h105v20h24v20h20v20zM760 154v-20h22v-20h20V94h117v20h19v20h22v20z" />
    <path fill="#664733" d="M51 144h162v13H51zM59 136h12v125H59zM193 136h12v125h-12zM779 162h165v12H779zM797 154h12v120h-12zM918 154h12v120h-12z" />
    <path fill="#476274" d="M83 170h29v35H83zM159 170h29v35h-29zM817 181h30v35h-30zM882 181h30v35h-30z" />
    <path fill="#755543" d="M121 206h27v57h-27zM850 221h28v53h-28z" />
    <path fill="#8fa393" d="M7 207V108h28v99zM220 223V144h25v79zM950 251V163h28v88z" />
    <path className="tree-fill" d="M0 81h12V56h31v25h15v29h14v47H0zM202 119h15V89h39v30h14v54h-68zM932 107h19V77h38v30h11v77h-68z" />
    <path fill="#e0c99c" opacity=".65" d="M323 306h55v8h-55zM623 354h68v8h-68zM226 467h80v10h-80zM663 534h93v9h-93zM380 578h78v10h-78z" />
    <path fill="#c69c65" d="M0 280h160v8H0zM840 294h160v8H840zM19 266h9v40h-9zM74 266h9v40h-9zM127 266h9v40h-9zM864 280h9v40h-9zM919 280h9v40h-9zM974 280h9v40h-9z" />
    <path fill="#f5d6a7" d="M30 465h7v7h-7zM110 541h7v7h-7zM890 480h7v7h-7zM932 580h7v7h-7z" />
    </g>
  </svg>;
}

let soundContext: AudioContext | null = null;
let hostSource: AudioBufferSourceNode | null = null;
let hostPlayback: Promise<void> = Promise.resolve();
let speechSource: HTMLAudioElement | null = null;
let hostVersion = 0;
const voiceBuffers = new Map<string, AudioBuffer>();
async function playHost(clips: string[]) {
  const version = ++hostVersion;
  if (!soundContext || soundContext.state !== "running" || speechSource) return;
  hostSource?.stop();
  for (const clip of clips) {
    if (localStorage.getItem("moon-sound") === "off") return;
    if (!voiceBuffers.has(clip)) {
      const response = await fetch(`/audio/${clip}.mp3`);
      if (!response.ok) return;
      voiceBuffers.set(clip, await soundContext.decodeAudioData(await response.arrayBuffer()));
    }
    if (version !== hostVersion || speechSource) return;
    const source = soundContext.createBufferSource();
    hostSource = source;
    source.buffer = voiceBuffers.get(clip)!;
    const gain = soundContext.createGain();
    gain.gain.value = .65;
    source.connect(gain); gain.connect(soundContext.destination);
    await new Promise<void>(resolve => { source.onended = () => resolve(); source.start(); });
    if (version !== hostVersion) return;
  }
}
function playSound(kind: string) {
  if (localStorage.getItem("moon-sound") === "off") return;
  const Audio = window.AudioContext || (window as any).webkitAudioContext;
  if (!Audio) return;
  soundContext ||= new Audio();
  soundContext!.resume().catch(() => {});
  const ctx = soundContext!;
  const notes = kind === "shot" ? [110, 65] : kind === "deal" ? [420, 620, 820] : [540, 680];
  notes.forEach((frequency, i) => {
    const osc = ctx.createOscillator(), gain = ctx.createGain(), at = ctx.currentTime + i * .08;
    osc.type = kind === "shot" ? "sawtooth" : "sine";
    osc.frequency.setValueAtTime(frequency, at);
    if (kind === "shot") osc.frequency.exponentialRampToValueAtTime(28, at + .12);
    gain.gain.setValueAtTime(0, at); gain.gain.linearRampToValueAtTime(kind === "shot" ? .1 : .07, at + .008);
    gain.gain.exponentialRampToValueAtTime(.001, at + .16);
    osc.connect(gain); gain.connect(ctx.destination); osc.start(at); osc.stop(at + .18);
  });
}

function WolfEmblem() {
  return <svg className="wolf-emblem" viewBox="0 0 128 128" aria-hidden="true">
    <circle cx="64" cy="62" r="48" fill="#d3b77e" opacity=".15" />
    <path fill="#ece3d2" d="M27 17 49 34 64 29 79 34 101 17 96 58 106 72 86 84 64 112 42 84 22 72 32 58Z" />
    <path fill="#8395b4" d="M27 17 49 34 38 55 32 58ZM101 17 79 34 90 55 96 58ZM22 72 49 65 56 87 42 84ZM106 72 79 65 72 87 86 84Z" />
    <path fill="#172239" d="M37 55 55 63 50 69 38 65ZM91 55 73 63 78 69 90 65ZM54 83 74 83 64 94ZM64 94 64 104 57 97ZM64 94 71 97 64 104Z" />
    <path fill="#e9b05d" d="M40 59 51 64 41 63ZM88 59 77 64 87 63Z" />
  </svg>;
}

function Portrait({ seat = 1, role, large = false }: { seat?: number; role?: string | null; large?: boolean }) {
  if (role) return <img className={"portrait role-portrait " + (large ? "large" : "")} src={`/roles/${role}${large ? "" : "-avatar"}.webp`} alt="" aria-hidden="true" />;
  const c = COLORS[(seat - 1) % COLORS.length];
  const hair = ["#574039", "#86573d", "#b38350", "#41414c", "#876451"][seat % 5];
  return <svg className={"portrait " + (large ? "large" : "")} viewBox="0 0 64 76" shapeRendering="crispEdges" aria-hidden="true">
    <path fill={hair} d="M16 10h32v4h6v38H10V14h6z" />
    {seat % 2 === 0 && <path fill={hair} d="M5 25h8v28H5zM51 25h8v28h-8z" />}
    <path fill="#f0c9a5" d="M14 27h36v20h-4v5H18v-5h-4zM9 30h5v10H9zM50 30h5v10h-5z" />
    <path fill="#ffddba" d="M18 25h27v20H18z" />
    <path fill={hair} d={seat % 3 === 0 ? "M13 19h39v12H36V20h-4v13H20v-6h-7z" : "M12 16h40v14H39v-6h-5v9H22v-7H12z"} />
    <path fill="#3b3338" d="M21 34h6v7h-6zM38 34h6v7h-6zM29 46h7v2h-7z" />
    <path fill="#fff9e9" d="M21 34h3v3h-3zM38 34h3v3h-3z" />
    <path fill="#e8a39a" d="M16 42h8v3h-8zM41 42h8v3h-8z" />
    <path fill={c} d="M18 53h28v4h6v13H12V57h6z" />
    <path fill="#f5dfb7" d="M25 53h14v5h-4v12h-6V58h-4z" />
    <path fill="#86684f" d="M14 70h14v6H14zM36 70h14v6H36zM15 65h34v3H15z" />
    {seat % 4 === 0 && <path fill={c} d="M16 10h32v9H16zM10 18h44v5H10z" />}
    {seat % 4 === 2 && <path fill={c} d="M46 16h11v7H46zM49 13h4v13h-4z" />}
  </svg>;
}

function Icon({ name }: { name: string }) {
  const paths: Record<string, React.ReactNode> = {
    moon: <path d="M19 14a8 8 0 0 1-9-9 8 8 0 1 0 9 9Z" />,
    book: <><path d="M12 5c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Z" /><path d="M12 5v14" /></>,
    settings: <><path d="m9 3 1 3h4l1-3 3 2-1 3 2 3 3 1-1 4-3-1-3 2-1 4h-4l-1-4-3-2-3 1-1-4 3-1 2-3-1-3Z" /><circle cx="12" cy="12" r="3" /></>,
    mic: <><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8" /></>,
    play: <path d="m8 4 13 8-13 8Z" />,
    pause: <><path d="M7 4v16M17 4v16" /></>,
    reset: <><path d="M4 10a8 8 0 1 1 1 8M4 4v6h6" /></>,
    home: <><path d="m3 10 9-7 9 7M5 9v12h14V9M9 21v-7h6v7" /></>,
    send: <><path d="m3 3 19 9-19 9 4-9Z" /><path d="M7 12h15" /></>,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    volume: <><path d="M3 9h4l5-5v16l-5-5H3ZM16 8c3 2 3 6 0 8M19 4c5 4 5 12 0 16" /></>,
    shield: <><path d="m12 2 8 4v7c0 4-8 9-8 9s-8-5-8-9V6Z" /><path d="m8 12 3 3 5-6" /></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="square" strokeLinejoin="round" className="icon" aria-hidden="true">{paths[name] || paths.moon}</svg>;
}

function RoleCard({ role, info, seat, selected = false, onSelect }: { role: string; info: Role; seat: number; selected?: boolean; onSelect?: () => void }) {
  const [flipped, setFlipped] = useState(false);
  const timer = useRef<number | null>(null);
  const origin = useRef<{x: number; y: number} | null>(null);
  const held = useRef(false);
  function stopHold() {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    origin.current = null;
  }
  useEffect(() => stopHold, []);
  return <button type="button" className={`role-card ${flipped ? "flipped" : ""} ${selected ? "picked" : ""}`}
    style={{ "--role-color": info.color } as React.CSSProperties} aria-label={`${onSelect ? "选择" : "翻看"}${info.name}身份牌`} aria-pressed={onSelect ? selected : flipped}
    onPointerDown={e => {
      if (e.button !== 0) return;
      held.current = false; origin.current = {x: e.clientX, y: e.clientY};
      timer.current = window.setTimeout(() => { held.current = true; setFlipped(v => !v); }, 500);
    }}
    onPointerMove={e => { if (origin.current && Math.hypot(e.clientX - origin.current.x, e.clientY - origin.current.y) > 10) stopHold(); }}
    onPointerUp={stopHold} onPointerCancel={stopHold} onPointerLeave={stopHold} onContextMenu={e => e.preventDefault()}
    onKeyDown={e => { if (e.key === "Enter" && e.shiftKey) { e.preventDefault(); setFlipped(v => !v); } }}
    onClick={() => { if (held.current) { held.current = false; return; } if (onSelect) onSelect(); else setFlipped(v => !v); }}>
    {onSelect && <span className="role-pick" aria-hidden="true">{selected ? "✓" : "+"}</span>}
    <span className="role-card-inner">
      <span className="role-card-front" aria-hidden={flipped}><small>{info.camp === "wolf" ? "狼人阵营" : role === "villager" ? "普通村民" : "有特殊技能的好人"}</small><span className="role-card-art"><Portrait role={role} seat={seat} large /></span><strong>{info.name}</strong><span className="role-card-hint">{onSelect ? "点击勾选 · 长按翻牌" : "点击或长按翻看技能"}</span></span>
      <span className="role-card-back" aria-hidden={!flipped}><small>身份与技能</small><strong>{info.name}</strong><span className="role-card-skill">{info.skill}</span><span className="role-card-hint">长按翻回 · Shift + Enter</span></span>
    </span>
  </button>;
}

function App() {
  const [game, setG] = useState<Game | null>(null);
  const [home, setHome] = useState(true);
  const g = home ? null : game;
  const [roles, setRoles] = useState<Record<string, Role>>({});
  const [boards, setBoards] = useState<Board[]>([]);
  const [modes, setModes] = useState<Record<string, Mode>>({});
  const [settings, setSettings] = useState<Settings>({ configured: false, base_url: "", model: "" });
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [count, setCount] = useState(10);
  const [humanCount, setHumanCount] = useState(1);
  const [mode, setMode] = useState("classic");
  const [modal, setModal] = useState<"new" | "guide" | "settings" | "takeover" | null>(null);
  const [randomGods, setRandomGods] = useState(false);
  const [selectedGods, setSelectedGods] = useState<string[]>([]);
  const [preview, setPreview] = useState<Board | null>(null);
  const [redraw, setRedraw] = useState(0);
  const [takeoverToken, setTakeoverToken] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now() / 1000);
  const [skill, setSkill] = useState<"duel" | "explode" | null>(null);
  const [feedback, setFeedback] = useState<{ kind: string; target: number | null; text: string; clue?: Clue } | null>(null);
  const [tab, setTab] = useState("public");
  const [text, setText] = useState("");
  const [speaker, setSpeaker] = useState(1);
  const [sound, setSound] = useState(localStorage.getItem("moon-sound") !== "off");
  const [target, setTarget] = useState<number | null>(null);
  const [choice, setChoice] = useState("pass");
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(true);
  const [recording, setRecording] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [key, setKey] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  const tableRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const spoken = useRef("");
  const speechSerial = useRef(0);
  const lastAudioEvent = useRef("");
  const lastShot = useRef("");
  const voicePrefix = useRef("");
  const waitingId = g?.waiting ? g.id + ":" + g.human + ":" + g.events.length + ":" + g.waiting.kind : "";
  const w = g?.waiting;
  const myRole = g ? roles[g.my_role] : null;
  const baseBoard = boards.find(b => b.count === count && b.mode === mode);
  const chosenBoard = randomGods ? preview?.count === count && preview.mode === mode && (preview.selected_gods || []).join(",") === selectedGods.join(",") ? preview : null : baseBoard;
  const waitSeconds = Math.max(0, Math.ceil((g?.api_wait?.until || 0) - now));
  const night = g?.phase === "night";
  const nightClosed = !!night && ((!w && !g?.night_vision && !feedback) || !!g?.paused);
  const humanAlive = g?.players.find(p => p.seat === g.human)?.alive;
  const discussion = w?.kind === "discussion";
  const speech = !!w && ["speech", "free", "last", "campaign", "debate", "discussion"].includes(w.kind);
  const discussionSeconds = Math.max(0, Math.ceil((g?.discussion_until || 0) - now));
  const actionKind = skill || w?.kind || "";
  const seatAction = actionKind === "wolf" && g?.my_role === "gargoyle"
    ? { ...SEAT_ACTIONS.wolf, title: "石像鬼 · 独立袭击", mark: "◆", hint: "其他狼人已出局；点座位袭击，理由仅你自己可见。" }
    : actionKind === "wolf_chat" ? { ...SEAT_ACTIONS.wolf, title: "狼队 · 商议提案", verb: "提交提议", skip: "只讨论分工", hint: "先读队友意见，提议袭击目标与白天分工；全部商议结束后再投票。" }
    : actionKind === "shoot" ? { ...SEAT_ACTIONS.shoot, title: `${myRole?.name || "猎人"} · 最后一枪` } : SEAT_ACTIONS[actionKind];
  const actionTargets = skill ? g?.players.filter(p => p.alive && p.seat !== g.human).map(p => p.seat) || []
    : w?.kind === "potion" ? choice === "save" && w.can_save && w.victim ? [w.victim] : choice === "poison" ? w.targets : [] : w?.targets || [];
  const selectedPlayer = g?.players.find(p => p.seat === target);
  const phaseNames: Record<string, string> = { night: "夜幕降临", election: "警长竞选", day: "圆桌发言", discussion: "自由讨论", vote: "放逐投票", ended: "终局揭晓" };

  async function run(path: string, value: object = {}) {
    if (["new", "resume", "voice"].includes(path)) {
      soundContext ||= new AudioContext();
      soundContext.resume().catch(() => {});
    }
    setBusy(true);
    try {
      const state: Game | null = await api(path, value);
      setG(prev => prev && state && prev.id === state.id && prev.revision > state.revision ? prev : state);
      if (path === "clear_history") setToast("所有历史对局和记录已清空。");
      return state;
    }
    catch (e) { setToast((e as Error).message); return null; }
    finally { setBusy(false); }
  }
  async function returnHome() {
    if (busy) return;
    if (await run("pause")) {
      setHome(true);
      setModal(null);
      setLogOpen(false);
    }
  }
  function skipSpeech() {
    spoken.current = "";
    stopSpeech();
    run("speech_done", { event_id: w?.event_id });
  }
  async function closeModal() {
    if (modal === "takeover" && !(await run("takeover"))) return;
    if (modal === "settings") {
      try { setSettings(await api("settings")); setKey(""); }
      catch (e) { setToast((e as Error).message); return; }
    }
    setModal(null);
  }
  useEffect(() => {
    api("catalog").then(v => { setRoles(v.roles); setBoards(v.boards); setModes(v.modes); }).catch(e => setToast(e.message));
    api("settings").then(s => { setSettings(s); setSettingsLoaded(true); if (!s.configured) setModal("settings"); }).catch(e => setToast(e.message));
    const es = new EventSource("/api/events");
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = event => {
      const v = JSON.parse(event.data);
      if (v.type === "state") setG(prev => prev && v.data && prev.id === v.data.id && prev.revision > v.data.revision ? prev : v.data);
      else if (v.type === "token") setG(prev => prev ? { ...prev, live: v.data } : prev);
    };
    return () => es.close();
  }, []);
  useEffect(() => { setText(""); setTarget(null); setChoice("pass"); }, [waitingId]);
  useEffect(() => { setSkill(null); setFeedback(null); setTab("public"); setLogOpen(false); }, [g?.id, g?.human, g?.phase]);
  useEffect(() => { setHumanCount(v => Math.min(v, count)); }, [count]);
  useEffect(() => {
    if (g?.paused || g?.winner || (skill === "duel" && (!g?.can_duel || !["day", "discussion"].includes(g.phase))) ||
      (skill === "explode" && (!humanAlive || !g || !["day", "discussion", "election"].includes(g.phase)))) setSkill(null);
  }, [g?.paused, g?.winner, g?.can_duel, humanAlive, g?.phase, skill]);
  useEffect(() => { if (modes[mode] && count < modes[mode].min) { setMode("classic"); setSelectedGods([]); } }, [count, mode, modes]);
  useEffect(() => {
    if (modal !== "new" || !randomGods || !baseBoard) return;
    let cancelled = false;
    setPreview(null);
    api("board", {count, mode, random_gods: true, selected_gods: selectedGods}).then(value => { if (!cancelled) setPreview(value); }).catch(e => { if (!cancelled) setToast(e.message); });
    return () => { cancelled = true; };
  }, [modal, count, mode, randomGods, selectedGods.join(","), redraw, !!baseBoard]);
  useEffect(() => {
    if (!g?.api_wait && !g?.discussion_until) return;
    setNow(Date.now() / 1000);
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [g?.api_wait?.until, g?.discussion_until]);
  useEffect(() => {
    const eligible = g?.humans.filter(s => g.players[s - 1].alive && !g.players[s - 1].muted) || [];
    if (eligible.length && !eligible.includes(speaker)) setSpeaker(eligible[0]);
  }, [g?.id, g?.players, speaker]);
  useEffect(() => {
    const last = g?.host;
    if (!g || !last || !sound || g.paused) return;
    const id = g.id + ":" + last.id;
    if (lastAudioEvent.current === id) return;
    lastAudioEvent.current = id;
    if (last.kind === "host" && (!g.human_count || g.human_count === 1 || !night)) {
      const stage = last.stage;
      const clips = stage === "day" ? /平安夜/.test(last.text) ? ["peace"]
        : ["death", ...Array.from(last.text.matchAll(/(\d+)\s*号/g), m => m[1]), "tail"]
        : ["closed", "dawn", "guard", "dream", "sculpt", "wolf", "inspect", "potion", "charm", "curse", "hunt", "aura", "mute", "tomb"].includes(stage) ? [stage] : [];
      hostPlayback = playHost(clips).catch(() => {});
    }
  }, [g?.host?.id, g?.paused, sound]);
  useEffect(() => {
    if (!g || g.paused || !sound) { hostVersion++; hostSource?.stop(); lastAudioEvent.current = ""; }
  }, [g?.id, g?.paused, sound]);
  useEffect(() => {
    const shot = [...(g?.events || [])].reverse().find(e => /号开枪带走/.test(e.text));
    if (!shot || !g || !sound || g.paused) return;
    const id = g.id + ":" + shot.id;
    if (lastShot.current !== id) { lastShot.current = id; playSound("shot"); }
  }, [g?.events.length, sound]);
  useEffect(() => {
    if (g?.takeover_choices?.length) { setModal("takeover"); setTakeoverToken(null); }
  }, [g?.takeover_choices?.join(",")]);
  useEffect(() => {
    if (stick.current && logRef.current) logRef.current.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [g?.events.length, g?.live?.text, tab]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 4500);
    return () => clearTimeout(t);
  }, [toast]);
  useEffect(() => {
    const eventId = w?.kind === "playback" ? w.event_id : undefined;
    if (!g?.read_aloud || g.paused || nightClosed || !eventId) {
      spoken.current = "";
      stopSpeech();
      return;
    }
    const e = g.events.find(e => e.id === eventId);
    const id = g.id + ":" + eventId + ":" + ++speechSerial.current;
    if (!e) return;
    spoken.current = id;
    const host = hostPlayback;
    const source = new Audio(`/api/speech_audio/${g.id}/${eventId}`);
    speechSource = source;
    source.preload = "auto";
    source.onerror = () => {
      if (spoken.current === id) setToast("朗读加载失败（独立语音服务），可点击跳过朗读继续。");
    };
    source.onended = () => {
      if (spoken.current !== id) return;
      spoken.current = "";
      stopSpeech();
      api("speech_done", { event_id: eventId }).catch(e => setToast(e.message));
    };
    source.load();
    (async () => {
      soundContext ||= new AudioContext();
      await soundContext.resume();
      await host;
      if (spoken.current !== id) return;
      await source.play();
    })().catch(error => {
      if (spoken.current === id) setToast(error.message + "，可点击跳过朗读继续。");
    });
    return () => { spoken.current = ""; stopSpeech(); };
  }, [g?.id, w?.event_id, g?.read_aloud, g?.paused, nightClosed]);
  useEffect(() => {
    window.moonVoice = value => {
      if (value.text !== undefined) setText(voicePrefix.current + value.text);
      if (value.recording !== undefined) setRecording(value.recording);
      if (value.error) setToast(value.error);
    };
    return () => { delete window.moonVoice; window.MoonAndroid?.voice("stop"); };
  }, []);
  useEffect(() => {
    window.MoonAndroid?.voice("stop");
    setRecording(false);
  }, [waitingId, g?.paused, g?.locked]);

  function toggleVoice() {
    if (!recording) voicePrefix.current = text ? text + " " : "";
    // 录音状态和识别结果统一由安卓系统桥接回传，不再维护第二套浏览器识别器。
    if (!window.MoonAndroid) { setToast("语音输入请在安卓应用中使用。"); return; }
    window.MoonAndroid.voice(recording ? "stop" : "start");
  }

  async function submit(nextChoice = choice, nextTarget = target) {
    if (busy || g?.paused) return;
    if (recording) window.MoonAndroid?.voice("stop");
    const state = await run(discussion ? "discussion" : "action", discussion ? { text, target: nextTarget, seat: speaker } : { text, target: nextTarget, choice: nextChoice });
    if (state) {
      if (w && ["vote", "elect_vote", "candidacy", "badge"].includes(w.kind)) playSound("vote");
      setText("");
      if (w && SEAT_ACTIONS[w.kind] && w.kind !== "peek" && !state.winner && state.human === g?.human) {
        const clue = state.private.length > (g?.private.length || 0) ? state.private[state.private.length - 1] : undefined;
        setFeedback({ kind: w.kind, target: w.kind === "potion" && nextChoice === "save" ? w.victim || null : nextTarget,
          text: clue?.text || (nextTarget ? `${SEAT_ACTIONS[w.kind].verb} ${nextTarget} 号，操作已完成。` : "本次选择已确认。"), clue });
      }
    }
  }
  function selectSeat(p: Player) {
    if (!g || g.paused || busy || g.winner) return;
    if (actionTargets.includes(p.seat)) { setFeedback(null); setTarget(p.seat); return; }
    if (!seatAction) {
      const clue = [...g.private].reverse().find(c => c.target === p.seat && ["inspect", "sculpt"].includes(c.kind || ""));
      if (clue) setFeedback({ kind: clue.kind!, target: p.seat, text: clue.text, clue });
    }
  }
  async function confirmSkill() {
    if (skill) {
      const current = skill;
      if (await run(current, { target })) { setSkill(null); setTarget(null); }
    } else await submit(w?.kind === "potion" ? choice : "act", w?.kind === "potion" && choice === "save" ? null : target);
  }
  function armSkill(kind: "duel" | "explode") {
    setSkill(kind); setTarget(null); setFeedback(null);
    tableRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }
  const roleCounts = countCards(chosenBoard?.roles || []);
  const tableRoleCounts = countCards(g?.cards || []);
  const canSend = !!w && w.kind !== "playback" && !g?.paused && !busy && (!discussion || !!g?.players[speaker - 1]?.alive && !g.players[speaker - 1].muted) && (speech ? w.kind === "free" || text.trim().length > 0 : REQUIRED_TARGETS.has(w.kind) ? target !== null : true);
  const canConfirmSkill = !busy && !g?.paused && !g?.winner &&
    (target !== null && actionTargets.includes(target) || actionKind === "explode" && g?.my_role !== "whitewolf");
  const revelation = feedback?.clue?.kind === "inspect" ? feedback.clue.result
    : feedback?.clue?.kind === "sculpt" ? roles[feedback.clue.result || ""]?.name : feedback?.clue?.kind === "aura" ? feedback.clue.result : null;

  const latestSpeech = g?.live || [...(g?.events || [])].reverse().find(e => e.kind === "speech");
  const recentHost = (g?.events || []).filter(e => e.kind === "host" && e.day === g?.day).slice(-3);
  return <div onPointerDown={() => { if (sound && !soundContext) { const Audio = window.AudioContext || (window as any).webkitAudioContext; if (Audio) soundContext = new Audio(); } if (sound) soundContext?.resume().catch(() => {}); }}
    className={"app " + (night ? "is-night" : "is-day") + (nightClosed ? " night-closed" : "") + (w && ["deal", "night_pass", "tomb"].includes(w.kind) ? " private-turn" : "") + (modal === "takeover" ? " takeover-open" : "")}>
    <header className="topbar">
      <a className="brand" href="/" aria-label="月下圆桌首页" onClick={e => { e.preventDefault(); if (g) returnHome(); else setModal(null); }}><span className="brand-mark"><WolfEmblem /></span><span>月下圆桌<small>WEREWOLF · MOON TABLE</small></span></a>
      <div className="header-center"><span className={"status-dot " + (!connected ? "offline" : "")} />{connected ? "本地圆桌" : "正在重新连接"}<span className="separator">/</span>{g ? `${g.human_count} 位真人 · ${g.count - g.human_count} 位 AI` : "单人 / 双人 · 竖屏同桌"}</div>
      <nav>
        {g && <button className="quiet" aria-label="返回主页" title="返回主页" disabled={busy} onClick={returnHome}><Icon name="home" /><span>返回主页</span></button>}
        <button className="quiet" aria-label={sound ? "关闭音效" : "开启音效"} onClick={() => { const enabled = !sound; setSound(enabled); localStorage.setItem("moon-sound", enabled ? "on" : "off"); if (enabled) playSound("deal"); }}><Icon name="volume" /><span>{sound ? "音效开" : "音效关"}</span></button>
        <button className="quiet mobile-log-toggle" onClick={() => setLogOpen(!logOpen)}><Icon name="book" />手记</button>
        <button className="quiet" onClick={() => setModal("guide")}><Icon name="book" /><span className="nav-label">角色图鉴</span></button>
        <button className="quiet" title="API 设置" onClick={() => setModal("settings")}><Icon name="settings" /><span className="nav-label">接口</span></button>
      </nav>
    </header>
    <main className="main-layout">
      <aside className="left-panel">
        <div className="eyebrow">YOUR SECRET CARD</div>
        <div className={"identity-card " + (g ? "" : "empty-card")}>
          <div className="card-top"><span>{g ? `${String(g.human).padStart(2, "0")} / 你的身份` : "等待入席"}</span><Icon name="shield" /></div>
          <div className="card-art"><div className="pixel-stars">✦</div><Portrait seat={g?.human || 1} role={g?.my_role || "seer"} large /><div className="art-ground" /></div>
          <small>{myRole?.camp === "wolf" ? "狼人阵营" : g ? "好人阵营" : "身份将在开局后揭晓"}</small>
          <h1>{myRole?.name || "神秘来客"}</h1>
          <p>{myRole?.skill || "入席一场月光下的博弈。每位 AI 都有独立身份、记忆与判断。"}</p>
          {g?.my_role === "witch" && <div className="potion-stock"><span className={!g.potions.antidote ? "used" : ""}>✧ 解药 {g.potions.antidote ? "1" : "0"}</span><span className={!g.potions.poison ? "used" : ""}>✧ 毒药 {g.potions.poison ? "1" : "0"}</span></div>}
          {g && !humanAlive && <div className="dead-note">你已出局 · 可继续观战</div>}
          {g?.skill_disabled && <div className="dead-note">长老出局后，你的特殊技能已失效</div>}
          {g && AUTO_SKILLS[g.my_role] && <div className="auto-skill-note">{AUTO_SKILLS[g.my_role]}</div>}
          <div className="card-bottom">PRIVATE · 仅你可见</div>
        </div>
      </aside>

      <section className="table-panel">
        <div className="table-heading">
          <div><div className="eyebrow">{g ? `第 ${g.day} ${night ? "夜" : "天"} · ${night ? "请闭眼" : "天已亮"}` : "主页"}</div><h2>{g ? phaseNames[g.phase] : "狼人杀"}</h2>{g && <button className="mobile-role" disabled={g.locked} onClick={() => { setTab("private"); setLogOpen(true); }}>{g.locked ? "身份已收起" : `${g.human} 号 · ${myRole?.name} · 我的线索`}</button>}</div>
          <div className="table-tools">
            {g && !g.winner && <button className="quiet" disabled={busy} onClick={() => run(g.paused ? "resume" : "pause")}><Icon name={g.paused ? "play" : "pause"} />{g.paused ? "继续" : "暂停"}</button>}
            <button className="quiet" onClick={() => { if (g) { setCount(g.count); setMode(randomGods ? g.mode : "classic"); setSelectedGods([]); } setModal("new"); }}><Icon name={g ? "reset" : "play"} />{g ? "新开一局" : "选择板子"}</button>
          </div>
        </div>
        {g && !night && latestSpeech && <button className="mobile-speech" onClick={() => { setTab("public"); setLogOpen(true); }}><strong>{latestSpeech.seat} 号{g.live ? " 正在说" : " 说"}</strong><span>{latestSpeech.text || "…"}</span></button>}
        {g && night && <div className={"host-banner " + (g.night_vision ? "peek-banner" : "")} role="status"><Icon name={g.night_vision ? "shield" : "moon"} /><div><small>{g.night_vision ? "小女孩 · 偷看视野" : "主持人"}</small><strong>{g.host?.text || "天黑请闭眼"}</strong>{g.night_vision && <p>你看见的狼队座位已记入“我的线索”。本夜仍有被发现的风险。</p>}</div></div>}
        <div ref={tableRef} className={"table-scene " + (g && g.count >= 13 ? "dense-table" : "")}>
          <VillageScene night={night} />
          {g?.host && !night && <div className="day-host"><span>主持人</span>{g.host.text}</div>}
          <div className="forest forest-left"><i /><i /><i /><i /></div><div className="forest forest-right"><i /><i /><i /></div>
          <div className="sky-moon" /><div className="sky-star s1" /><div className="sky-star s2" /><div className="sky-star s3" />
          <div className="floor-grid" />
          <div className="round-table"><div className="table-rim"><div className="table-inner">
            <div className="candle c1"><i /></div><div className="candle c2"><i /></div><div className="candle c3"><i /></div>
            <div className="table-sigil"><WolfEmblem /><span>月下圆桌</span>{g && <small>{g.players.filter(p => p.alive).length} / {g.count} 人在场</small>}</div>
          </div></div></div>
          {(g?.players || Array.from({ length: 10 }, (_, i) => ({ seat: i + 1, name: i === 0 ? "你" : ["松果", "阿栗", "晚风", "石榴", "麦芽", "山雀", "小满", "灰盐", "柚子"][i - 1], alive: true, can_vote: true, role: null, sheriff: false, muted: false, known_camp: null }))).map((p, i, arr) => {
            const angle = -Math.PI / 2 + 2 * Math.PI * i / arr.length;
            const x = 50 + 40 * Math.cos(angle);
            const y = 54 + 33 * Math.sin(angle);
            const active = g?.live?.seat === p.seat || (w?.kind === "playback" ? latestSpeech?.seat === p.seat : w?.seat === p.seat);
            const selectable = actionTargets.includes(p.seat) && !g?.paused && !busy && !g?.winner;
            return <button key={p.seat} style={{ left: `${x}%`, top: `${y}%`, "--seat-color": COLORS[i] } as React.CSSProperties}
              className={`seat ${active ? "speaking" : ""} ${!p.alive ? "eliminated" : ""} ${selectable ? "selectable" : ""} ${target === p.seat && selectable ? "selected" : ""} ${p.known_camp === "wolf" ? "observed-wolf" : ""} ${p.seat === (g?.human || 1) ? "human" : ""}`}
              onClick={() => selectSeat(p)} aria-pressed={selectable ? target === p.seat : undefined} aria-label={`${p.seat}号 ${p.name}${p.alive ? "" : " 已出局"}${p.role ? " " + roles[p.role]?.name : p.known_camp ? p.known_camp === "wolf" ? " 狼人阵营" : " 好人阵营" : ""}`}>
              {p.sheriff && <span className="badge">♜</span>}
              {active && <span className="speaking-dots"><i /><i /><i /></span>}
              <div className="seat-avatar"><Portrait seat={p.seat} role={p.role || (p.known_camp === "wolf" ? "wolf" : null)} /><span className="seat-number">{String(p.seat).padStart(2, "0")}</span>{p.known_camp && <span className={"camp-mark " + p.known_camp} aria-hidden="true">{p.known_camp === "wolf" ? "狼" : "好"}</span>}</div>
              <div className="seat-name">{p.name}{p.seat === (g?.human || 1) && <small>YOU</small>}</div>
              <div className="seat-state">{!p.alive ? "已出局" : p.muted ? "被禁言" : p.role ? roles[p.role]?.name : p.known_camp ? p.known_camp === "wolf" ? "狼人阵营" : "好人阵营" : g ? "身份未知" : "AI 玩家"}</div>
              {selectable && target === p.seat && seatAction && <span className="aim-mark" aria-hidden="true">{seatAction.mark}</span>}
            </button>;
          })}
          {g && !g.paused && !g.winner && (seatAction || feedback) && <div className={"skill-focus " + (feedback ? "skill-result" : actionKind === "potion" ? "potion-focus" : "")}
            style={{ "--skill-color": choice === "poison" && actionKind === "potion" ? "#e995ae" : myRole?.color || "#dfb878" } as React.CSSProperties}
            aria-label={feedback ? "你的技能结果" : "圆桌技能操作"}>
            {feedback ? <>
              <small>{night ? "仅你可见" : "你的操作记录"}</small>
              {revelation ? <div className="private-reveal" key={`${feedback.target}:${feedback.text}`}><Portrait seat={feedback.target || 1} large role={feedback.clue?.kind === "sculpt" ? feedback.clue.result : feedback.clue?.kind === "inspect" && feedback.clue.result === "狼人" ? "wolf" : null} /><strong>{revelation}</strong></div>
                : <span className="skill-symbol">{SEAT_ACTIONS[feedback.kind]?.mark || "✦"}</span>}
              <p className="skill-result-text" role="status">{feedback.text}</p>
              <button className="target" onClick={() => setFeedback(null)}>{night && !w && !g.night_vision ? "收起结果，继续闭眼" : "收起结果"}</button>
            </> : <>
              <small>{night ? "你的夜间技能 · 仅你可见" : "点选座位 · 确认后生效"}</small>
              <span className="skill-symbol">{seatAction.mark}</span><h3>{seatAction.title}</h3>
              {actionKind === "potion" && <div className="potion-tools">
                <button className={"potion-bottle remedy " + (choice === "save" ? "active" : "")} disabled={!w?.can_save || busy} onClick={() => { setChoice("save"); setTarget(w?.victim || null); }} aria-pressed={choice === "save"}><span>⚗</span>解药<small>{g.potions.antidote ? w?.victim ? `${w.victim} 号被袭击` : "今晚无人被袭击" : "已用完"}</small></button>
                <button className={"potion-bottle poison " + (choice === "poison" ? "active" : "")} disabled={!w?.can_poison || busy} onClick={() => { setChoice("poison"); setTarget(null); }} aria-pressed={choice === "poison"}><span>⚗</span>毒药<small>{g.potions.poison ? "点座位选择" : "已用完"}</small></button>
              </div>}
              <p className="skill-target" role="status">{selectedPlayer ? `${target} 号 · ${selectedPlayer.name}` : actionKind === "potion" && choice === "pass" ? "先选择一瓶药剂" : actionKind === "explode" && g.my_role !== "whitewolf" ? "确认自爆将立即出局" : actionKind === "peek" ? "要掀开眼罩吗？" : "点击圆桌上亮起的座位"}</p>
              {actionKind === "peek" ? <div className="skill-buttons"><button className="primary" disabled={busy} onClick={() => submit("peek", null)}>冒险偷看</button><button className="target" disabled={busy} onClick={() => submit("pass", null)}>保持闭眼</button></div> : <div className="skill-buttons">
                <button className="primary" disabled={!canConfirmSkill} onClick={confirmSkill}>{actionKind === "potion" ? choice === "save" ? "解药救人" : choice === "poison" ? "毒药毒人" : "选择药剂" : actionKind === "wolf" && target === g.human ? "确认袭击自己" : seatAction.verb}{target !== null ? ` ${target} 号` : ""}</button>
                {skill ? <button className="target" disabled={busy} onClick={() => { setSkill(null); setTarget(null); }}>取消</button> : seatAction.skip && <button className="target" disabled={busy} onClick={() => submit("pass", null)}>{seatAction.skip}</button>}
              </div>}
            </>}
          </div>}
          {g?.paused && <div className="scene-overlay"><Icon name="pause" /><h3>圆桌已暂停</h3><p>身份、行动与发言均已保存</p><button className="primary" onClick={() => run("resume")}>继续这一局</button></div>}
          {g?.winner && <div className="scene-overlay victory"><span className="victory-star">✦</span><h3>{g.winner === "good" ? "好人获胜" : "狼人获胜"}</h3><p>第 {g.day} 天 · 所有身份已经揭晓</p><button className="primary" onClick={() => setModal("new")}>再赴一场圆桌</button></div>}
        </div>
        {!g && <div className="welcome-bar"><div className="home-actions">
          <button className="primary" disabled={busy || !settingsLoaded} onClick={() => setModal(settings.configured ? "new" : "settings")}>开始新对局</button>
          {game && !game.winner && <button className="target" disabled={busy || !settingsLoaded} onClick={async () => { if (!settings.configured) { setModal("settings"); return; } if (game.takeover_choices.length) { setHome(false); setModal("takeover"); } else if (await run("resume")) setHome(false); }}>继续上次对局</button>}
          <button className="target danger-text" disabled={busy || !game} onClick={() => { if (window.confirm("删除所有历史对局、发言、投票和私有线索？删除后无法恢复。")) run("clear_history"); }}>清空历史记录</button>
        </div></div>}
        {g && g.human_count === 1 && !humanAlive && !g.winner && <div className="takeover-banner"><div><strong>出局之后，也能换个位置继续玩</strong><p>{g.can_takeover ? "继续观战，或盲选一个未翻牌的存活角色接管。确认后才展示新身份，技能状态原样保留。" : "完成遗言、开枪或选择下一位警长后可盲选接管；没有未翻牌角色时只能观战。"}</p></div><button className="primary" disabled={!g.can_takeover || busy} onClick={() => run("takeover", {prepare: true})}>盲选接管 AI</button></div>}
        {g && <div className={"action-panel " + (w && !g.paused ? "your-turn" : "")}>
          {discussion && <div className="discussion-bar"><strong role="timer">{g.paused ? "已暂停" : `${String(Math.floor(discussionSeconds / 60)).padStart(2, "0")}:${String(discussionSeconds % 60).padStart(2, "0")}`}</strong>
            <div className="speaker-switch">{g.humans.map((s, i) => <button key={s} aria-pressed={speaker === s} disabled={!g.players[s - 1].alive || g.players[s - 1].muted || busy || g.paused} onClick={() => { setSpeaker(s); setText(""); setTarget(null); }} className={speaker === s ? "active" : ""}>玩家{i + 1}<small>{s}号</small></button>)}</div>
            <button className="target" disabled={busy || g.paused} onClick={() => run("discussion", {finish: true})}>直接投票 →</button></div>}
          <div className="action-title"><span className="status-dot" /><strong>{g.winner ? "这一局已结束" : g.paused ? "等待继续" : w ? w.prompt : night ? "夜间行动正在进行，等待天亮。" : g.live ? `${g.live.seat} 号正在发言…` : humanAlive ? "其他玩家正在行动…" : "你已出局，正在观战。"}</strong><small>{w && w.kind !== "playback" && !g.paused ? "YOUR TURN" : "AUTO SAVED"}</small></div>
          {g.api_wait && !g.paused && <div className="api-wait" role="status"><strong>{waitSeconds > 0 ? `${waitSeconds} 秒后自动继续` : "正在继续…"}</strong><span>{g.api_wait.reason}，不用重复点击继续。</span></div>}
          {w?.kind === "playback" && <button className="target skip-speech" disabled={g.paused || busy} onClick={skipSpeech}>跳过朗读 · 下一位 →</button>}
          {w && ["deal", "review", "night_pass", "tomb"].includes(w.kind) && <div className="handoff-actions">
            {w.kind === "review" ? <button className="primary wide" disabled={busy || g.paused} onClick={() => run("handoff")}>我已看完 · 遮屏交接</button>
              : <button className="primary wide" disabled={busy || g.paused} onClick={() => submit("act", null)}>{w.kind === "deal" ? g.human_count > 1 ? g.human === g.humans[g.humans.length - 1] ? "身份确认 · 开始首夜" : "记住身份 · 遮屏交接" : "记住身份 · 确认" : w.kind === "tomb" ? "领取守墓线索" : "本轮无需行动 · 遮屏交接"}</button>}
          </div>}
          {w && ["wolf_chat", "wolf"].includes(w.kind) && g.my_role !== "gargoyle" && <div className="wolf-discussion" aria-label="狼队商议">
            <strong>{w.kind === "wolf_chat" ? "先商议，再投票" : "商议结束 · 选择袭击目标"}</strong>
            {g.wolf_chat.map((c, i) => <p key={i}><b>{c.seat ? `${c.seat} 号` : "狼队"}</b> {c.text}{c.target ? ` · 提议 ${c.target} 号` : ""}</p>)}
            {!g.wolf_chat.length && <p>你可以先提议袭击谁，说明理由或白天的分工。AI 队友随后回应。</p>}
          </div>}
          {(w?.kind === "free" || discussion) && <label className="question-target">点名追问 <select value={target || ""} onChange={e => setTarget(e.target.value ? Number(e.target.value) : null)} disabled={g.paused}><option value="">只补充我的判断</option>{w!.targets.filter(s => s !== (discussion ? speaker : g.human)).map(s => <option value={s} key={s}>{s} 号 · {g.players[s - 1]?.name}</option>)}</select></label>}
          {seatAction && <p className="skill-guidance">{seatAction.hint}</p>}
          {!seatAction && AUTO_SKILLS[g.my_role] && <p className="skill-guidance">{AUTO_SKILLS[g.my_role]}</p>}
          {g.my_role === "witch" && w?.kind === "potion" && w.victim === g.human && <p className="skill-guidance">今晚狼人袭击了你；本版女巫不能救自己。</p>}
          {w?.kind === "wolf" && target === g.human && <div className="special-options">你选择了自己。若狼队最终袭击你且无人救下，你会出局。</div>}
          {w && ["candidacy", "direction"].includes(w.kind) && <div className="special-options choice-buttons">
            {(w.kind === "candidacy" ? [["yes", "参加警长竞选"], ["no", "不参加竞选"]] : [["left", "左侧先发言"], ["right", "右侧先发言"]]).map(([c, label]) => <button key={c} className="target" disabled={g.paused || busy} onClick={() => submit(c)}>{label}</button>)}
          </div>}
          {(speech || w?.kind === "wolf" || w?.kind === "wolf_chat") && <div className="composer">
            <textarea aria-label="发言输入" placeholder={speech ? "说说你的判断，也可以点击麦克风…" : g.my_role === "gargoyle" ? "独立袭击理由（仅自己可见）…" : "给狼队的提议与分工（仅队友可见）…"} value={text} onChange={e => setText(e.target.value)} disabled={!w || g.paused || busy} maxLength={2000} onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && canSend) submit(); }} />
            <div className="composer-controls"><button className={"icon-button " + (recording ? "recording" : "")} title={recording ? "停止录音" : "语音转文字"} aria-label={recording ? "停止录音" : "语音输入"} disabled={!w || g.paused || (!speech && w.kind !== "wolf")} onClick={toggleVoice}><Icon name="mic" /></button>
              {speech && <button className="primary send" disabled={!canSend} onClick={() => submit()}><Icon name="send" />{w?.kind === "free" && !text.trim() ? "跳过" : "发送发言"}</button>}</div>
          </div>}
          <div className="composer-hint"><span>{(speech || w?.kind === "wolf") ? recording ? "● 正在录音，识别后可修改再发送" : "语音识别后可编辑 · 发言围绕具体线索" : ""}</span>{humanAlive && ["day", "discussion", "election"].includes(g.phase) && ["wolf", "wolfking", "whitewolf"].includes(g.my_role) && <button className="danger-text" disabled={g.paused || busy} onClick={() => armSkill("explode")}>发动自爆{g.my_role === "whitewolf" ? " · 带走一人" : ""}</button>}{g.can_duel && ["day", "discussion"].includes(g.phase) && <button className="danger-text" disabled={g.paused || busy} onClick={() => armSkill("duel")}>骑士决斗</button>}</div>
          {g.human_count > 1 && !g.locked && !g.winner && !["deal", "review", "night_pass"].includes(w?.kind || "") && <button className="privacy-lock" disabled={busy} onClick={() => run("handoff")}>遮住身份</button>}
        </div>}
      </section>

      <aside className={"right-panel " + (logOpen ? "log-open" : "")}>
        <div className="log-heading"><div><span className="eyebrow">VILLAGE CHRONICLE</span><h2>圆桌手记</h2></div><button title={g?.read_aloud ? "关闭朗读" : "朗读 AI 发言"} disabled={!g || busy} className={"quiet voice-toggle " + (g?.read_aloud ? "enabled" : "")} onClick={() => run("voice", { enabled: !g?.read_aloud })}><Icon name="volume" /><span>{g?.read_aloud ? "朗读开" : "朗读关"}</span></button><button className="icon-button mobile-log-toggle" aria-label="关闭手记" onClick={() => setLogOpen(false)}><Icon name="close" /></button></div>
        <div className="log-tabs"><button className={tab === "public" ? "active" : ""} onClick={() => setTab("public")}>公开记录</button><button className={tab === "private" ? "active" : ""} onClick={() => setTab("private")}>我的线索 {g && g.private.length > 0 && <i>{g.private.length}</i>}</button>{myRole?.camp === "wolf" && g?.my_role !== "gargoyle" && <button className={tab === "wolf" ? "active" : ""} onClick={() => setTab("wolf")}>狼队</button>}</div>
        <div ref={logRef} className="chronicle" onScroll={() => { const el = logRef.current; if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100; }}>
          {!g && <div className="empty-log"><Icon name="book" /><strong>暂无对局</strong><p>开始或继续对局后，发言和投票会显示在这里。</p></div>}
          {g && tab === "public" && <>
            {g.last_ballot && <div className="ballot-summary"><small>第 {g.last_ballot.day} 天 · {g.last_ballot.label}</small><div>{Object.entries(g.last_ballot.totals).sort((a, b) => b[1] - a[1]).map(([s, votes]) => <span key={s}><strong>{s} 号</strong>{votes} 票</span>)}</div><p>{Object.entries(g.last_ballot.choices).map(([s, t]) => `${s}→${t || "弃"}`).join(" · ")}</p></div>}
            {g.events.map((e, i) => <React.Fragment key={e.id}>
              {(i === 0 || g.events[i - 1].day !== e.day) && <div className="day-divider"><span>第 {e.day} 天</span></div>}
              {e.kind === "speech" ? <div className={"speech-entry " + (e.seat === g.human ? "my-speech" : "")}><div className="speech-meta"><span className="tiny-avatar" style={{ background: COLORS[(e.seat || 1) - 1] }} />{e.seat} 号 · {g.players[(e.seat || 1) - 1]?.name}<small>{e.phase === "election" ? "竞选" : "发言"}</small></div><p>{e.text}</p></div> : <div className={"system-entry " + (e.kind === "votes" ? "vote-entry" : "")}><span>✦</span><p>{e.text}</p></div>}
            </React.Fragment>)}
            {g.live && <div className="speech-entry live-entry"><div className="speech-meta"><span className="tiny-avatar" style={{ background: COLORS[g.live.seat - 1] }} />{g.live.seat} 号 · {g.players[g.live.seat - 1]?.name}<small>发言中</small></div><p>{g.live.text || <span className="typing">正在开口<span>...</span></span>}<span className="cursor" /></p></div>}
          </>}
          {g && tab === "private" && <><div className="privacy-banner"><Icon name="shield" />{g.locked ? "公共画面 · 身份和线索已收起" : "这些信息只有你能看到"}</div>{!g.locked && <div className="my-card-summary"><Portrait role={g.my_role} large /><h3>{myRole?.name}</h3><p>{myRole?.skill}</p></div>}{g.private.length ? g.private.map((p, i) => <div className="clue-entry" key={i}><small>第 {p.day} 夜 / 行动记录</small><p>{p.text}</p></div>) : <p className="empty-text">还没有私有线索。白天的发言与投票请在公开记录查看。</p>}</>}
          {g && tab === "wolf" && <><div className="privacy-banner">狼队商议仅狼队可见</div>{g.wolf_chat.map((c, i) => <div className="clue-entry" key={i}><small>{c.seat} 号 · 袭击目标 {c.target || "不袭击"}</small><p>{c.text}</p></div>)}{!g.wolf_chat.length && <p className="empty-text">等待本夜狼队商议。</p>}</>}
          <div ref={bottomRef} />
        </div>
        <div className="log-footer"><span className="status-dot" />本地自动保存<span>{settings.model}</span></div>
      </aside>
    </main>
    <footer className="app-footer"><span>月下圆桌</span><span>{g ? `${g.count - g.human_count} 位 AI · ${g.rules.sheriff ? "含警长竞选" : "无警长小桌"}` : "6～15 人"}</span></footer>

    {nightClosed && <div className="night-veil"><div className="night-message"><span>第 {g!.day} 夜 · 请保持闭眼</span><p>{g!.error ? g!.error : g!.paused ? "对局已暂停" : "天黑请闭眼"}</p><div className="host-cue" key={g!.host?.id || "night"} role="status" aria-live="polite"><small>主持人 · 当前进度</small><strong>{g!.host?.text || "所有玩家请闭眼，正在准备夜间行动。"}</strong></div><ol className="host-history">{recentHost.slice(0, -1).map(e => <li key={e.id}>{e.text}</li>)}</ol><small>{g!.paused ? "继续后，轮到你行动时画面会亮起" : g!.api_wait ? `接口等待中 · ${waitSeconds} 秒后自动继续` : "轮到你行动时，画面会亮起"}</small><div className="night-controls"><button disabled={busy} onClick={() => run(g!.paused ? "resume" : "pause")}>{g!.paused ? "继续" : "暂停"}</button><button disabled={busy} onClick={returnHome}>返回主页</button><button onClick={() => setModal("new")}>重开</button>{g!.can_takeover && <button onClick={() => run("takeover", {prepare: true})}>盲选接管</button>}{g!.error && <button onClick={() => setModal("settings")}>设置</button>}</div></div></div>}
    {g?.error && <div className="error-banner">{g.error}<button onClick={() => setModal("settings")}>检查设置</button></div>}
    {g?.locked && !g.winner && (g.handoff || night) && <div className="handoff-veil" role="dialog" aria-label="手机交接">
      <WolfEmblem /><small>身份已遮住 · 同屏多人</small><h2>{g.handoff ? `请将手机交给 ${g.handoff} 号` : "请等待下一位玩家"}</h2>
      <p>{g.handoff ? "其他玩家请移开视线。拿到手机后，再领取自己的视角。" : g.host?.text || (g.live ? `${g.live.seat} 号正在发言` : "AI 正在行动，公开信息会记录在圆桌手记中。")}</p>
      {g.live && <div className="public-live"><strong>{g.live.seat} 号</strong><p>{g.live.text || "正在发言…"}</p></div>}
      {g.handoff && <button className="primary wide" disabled={busy || g.paused} onClick={() => run("handoff", { reveal: true })}>我是 {g.handoff} 号 · 查看</button>}
      <div className="night-controls"><button disabled={busy} onClick={() => run(g.paused ? "resume" : "pause")}>{g.paused ? "继续" : "暂停"}</button><button disabled={busy} onClick={returnHome}>返回主页</button><button onClick={() => setModal("new")}>重开</button>{g.waiting?.kind === "playback" && <button disabled={busy || g.paused} onClick={skipSpeech}>跳过朗读</button>}</div>
      {g.error && <p role="alert">{g.error}</p>}
    </div>}
    {toast && <div className="toast" role="alert">{toast}</div>}
    {modal && <div className="modal-backdrop" onClick={closeModal}><section key={modal} className={"modal " + (modal === "guide" ? "guide-modal" : "")} onClick={e => e.stopPropagation()} role="dialog" aria-modal="true" aria-label={modal === "new" ? "开局配置" : modal === "guide" ? "身份图鉴" : modal === "takeover" ? "盲选接管" : "API 设置"}>
      <button className="modal-close icon-button" aria-label="关闭弹窗" onClick={closeModal}><Icon name="close" /></button>
      {modal === "new" && <><h2>新对局</h2><p className="modal-description">先选总人数，再选真人数量，空位由 AI 补齐。真人从 1 号起依次入座；15 人经典局为 5 狼、5 神、5 村民。</p>
        <div className="count-options">{Array.from({ length: 10 }, (_, i) => i + 6).map(n => <button className={count === n ? "active" : ""} onClick={() => { setCount(n); setSelectedGods([]); }} key={n}>{n}<small>人</small></button>)}</div>
        <label className="human-count">真人玩家 <select aria-label="真人玩家数量" value={humanCount} onChange={e => setHumanCount(Number(e.target.value))}>{[1, 2].map(n => <option value={n} key={n}>{n === 1 ? "单人" : "双人同屏"} · {n} 位真人 · {count - n} 位 AI</option>)}</select></label>
        <p className="random-note">{humanCount > 1 ? "同屏多人：按提示传手机，每次看完先遮屏。夜间按固定座位轮流领取视角，无技能也会轮到；其他玩家必须闭眼。白天可当面讨论，依次记录发言和投票。" : "单人模式：你与独立 AI 同桌；出局后可观战或盲选接管。"}</p>
        <div className="difficulty-options"><button className={!randomGods ? "active" : ""} onClick={() => { setRandomGods(false); setMode("classic"); setSelectedGods([]); }}><strong>简单版</strong><span>经典固定牌 · 完整流程</span></button><button className={randomGods ? "active" : ""} onClick={() => { setRandomGods(true); setSelectedGods([]); }}><strong>复杂版</strong><span>特殊板子 · 自选神职</span></button></div>
        {randomGods && <div className="mode-options">{Object.entries(modes).map(([m, info]) => <button key={m} className={mode === m ? "active" : ""} disabled={count < info.min} onClick={() => { setMode(m); setSelectedGods([]); }}><strong>{info.name}</strong><span>{count < info.min ? `${info.min} 人起可选` : info.note}</span></button>)}</div>}
        {randomGods && baseBoard && baseBoard.god_slots > 0 && <><div className="god-pick-heading"><div><strong>选择今晚的神职</strong><span>已选 {selectedGods.length} / {baseBoard.god_slots} · 空缺随机补齐</span></div><button className="text-button" onClick={() => { setSelectedGods([]); setRedraw(v => v + 1); }}>全部随机 ↻</button></div><p className="random-note">固定核心：{baseBoard.fixed_gods.map(r => roles[r]?.name).join("、")}。点击卡牌勾选，长按 0.5 秒翻看技能；上下滑动浏览。特殊组合属于自选练习玩法。</p><div className="god-select-list" aria-label="可选神职列表">{baseBoard.selectable_gods.map(r => <RoleCard key={r} role={r} info={roles[r]} seat={Object.keys(roles).indexOf(r) + 1} selected={selectedGods.includes(r)} onSelect={() => {
          if (selectedGods.includes(r)) setSelectedGods(v => v.filter(value => value !== r));
          else if (selectedGods.length < baseBoard.god_slots) setSelectedGods(v => [...v, r]);
          else setToast(`已选满 ${baseBoard.god_slots} 张，请先取消一张再替换`);
        }} />)}</div></>}
        {randomGods && baseBoard?.god_slots === 0 && <p className="random-note">本板神职全部固定：{baseBoard.fixed_gods.map(r => roles[r]?.name).join("、")}，无需额外选牌。</p>}
        {!randomGods && <p className="random-note">固定经典配牌：12 人为 4 狼、4 神、4 民；15 人为 5 狼、5 神、5 民。完整保留警长、质询、投票与夜间行动。</p>}
        {randomGods && !chosenBoard && <p className="random-note">正在抽取配牌…失败时可点击“重新抽取”。</p>}
        <div className="board-preview">{Object.entries(roleCounts).map(([r, n]) => <div key={r}><Portrait seat={Object.keys(roles).indexOf(r) + 1} role={r} /><span>{roles[r]?.name}<small>× {n}</small></span></div>)}</div>
        <div className="board-note"><p>{chosenBoard?.rules.win}</p><p>{count >= 9 ? "选举警长 · 警长投票算 1.5 票 · 平票后再发言" : "小桌不设警长"} · 发言后可点名追问</p>{chosenBoard?.roles.includes("girl") && <p>小女孩：偷看有 20% 被发现风险（线上改编玩法）。</p>}</div>
        {g && <p className="replace-note">新开一局会替换当前桌面的对局。当前记录仍保存在本地数据库。</p>}
        <button className="primary wide" disabled={busy || !chosenBoard} onClick={async () => { playSound("deal"); if (await run("new", { count, mode, human_count: humanCount, human_seat: 1, random_gods: randomGods, selected_gods: selectedGods, board_token: chosenBoard?.token })) { setHome(false); setModal(null); setTab("public"); stick.current = true; } }}>确认配牌，开始游戏 <span>→</span></button>
      </>}
      {modal === "takeover" && g && <><div className="eyebrow">A SECOND SEAT</div><h2>选一张背面身份牌</h2><p className="modal-description">此刻对局已暂停。所有背面牌均不显示座位、姓名或职业；确认接管后才领取新身份与该角色已有的技能记录，不能预览后退换。</p><div className="blind-cards">{g.takeover_choices.map((token, i) => <button key={token} className={takeoverToken === token ? "selected" : ""} aria-label={`选择背面牌 ${i + 1}`} onClick={() => setTakeoverToken(token)}><WolfEmblem /><span>神秘身份</span><small>{String(i + 1).padStart(2, "0")}</small></button>)}</div><p className="random-note">接管属于练习玩法：不会复活旧角色，不会重置药、枪或其他技能，也无法抹去你之前已知的信息。</p><div className="takeover-actions"><button className="quiet" disabled={busy} onClick={closeModal}>继续观战</button><button className="primary" disabled={busy || !takeoverToken} onClick={async () => { if (await run("takeover", {token: takeoverToken})) { setModal(null); setTab("private"); setLogOpen(true); setToast("已接管新角色，请查看你的身份与技能记录"); } }}>确认盲选接管</button></div></>}
      {modal === "settings" && <><div className="eyebrow">YOUR AI, YOUR TABLE</div><h2>{settings.configured ? "AI 接口设置" : "首次游玩 · 设置你的 API"}</h2><p className="modal-description">游戏不提供共享密钥。请填入你自己的 API Key，AI 发言与行动的费用由你的服务商账户承担；每位 AI 只接收自己的身份与记忆。</p>
        <div className="mode-options">{[["DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"], ["Agnes", "https://api.agnes-ai.cn/v1", "agnes-3.0-flash"]].map(([name, base_url, model]) => <button key={name} type="button" className={settings.base_url === base_url ? "active" : ""} aria-pressed={settings.base_url === base_url} onClick={() => { if (settings.base_url !== base_url) { setSettings({base_url, model, configured: false}); setKey(""); } }}><strong>{name}</strong><span>{model}</span></button>)}</div>
        <label>API 地址<input aria-label="API 地址" value={settings.base_url} onChange={e => setSettings({ ...settings, base_url: e.target.value })} placeholder="https://api.deepseek.com/v1" /></label>
        <label>模型<input aria-label="模型" value={settings.model} onChange={e => setSettings({ ...settings, model: e.target.value })} /></label>
        <label>API Key<input aria-label="API Key" type="password" autoComplete="off" spellCheck={false} value={key} onChange={e => setKey(e.target.value)} placeholder={settings.configured ? "已配置 · 留空保留现有密钥" : "填入你自己的 API Key"} /></label>
        <div className="board-note"><p>密钥仅保存在本机应用数据目录，不会在界面回显或上传给开发者。首次设置和从旧版升级都需要填写自己的密钥。</p><p>支持兼容 OpenAI 的 Chat Completions 接口。请求间隔至少 1.7 秒；429 会按服务端要求等待。朗读使用独立的 Edge TTS，语音输入需系统语音服务。</p></div>
        <button className="primary wide" disabled={busy || !settings.base_url.trim() || !settings.model.trim() || (!settings.configured && !key.trim())} onClick={async () => {
          setBusy(true);
          try { const s = await api("settings", { base_url: settings.base_url, model: settings.model, ...(key.trim() ? {api_key: key} : {}) }); setSettings(s); setKey(""); setModal(g ? null : "new"); setToast("接口配置已保存"); }
          catch (e) { setToast((e as Error).message); } finally { setBusy(false); }
        }}>保存设置</button>
      </>}
      {modal === "guide" && <><div className="eyebrow">THE FIELD GUIDE</div><h2>身份图鉴与玩法</h2><p className="modal-description">图鉴展示所有可用身份，不代表本局玩家的隐藏身份。</p>
        {g && <div className="board-note"><p>本局配牌 · {g.random_gods ? "随机神职，已固定" : "固定板子"}</p><div className="board-preview">{Object.entries(tableRoleCounts).map(([r, n]) => <span key={r}>{roles[r]?.name} × {n}</span>)}</div></div>}
        <p className="random-note">点击或长按卡牌翻看身份与技能，再次操作可翻回。键盘可用 Shift + Enter 翻牌。</p><div className="guide-grid">{Object.entries(roles).map(([r, info], i) => <RoleCard key={r} role={r} info={info} seat={i + 1} />)}</div><div className="full-rules"><h3>一场游戏如何进行</h3>{[g?.rules || chosenBoard?.rules || baseBoard?.rules || boards[0]?.rules].filter(Boolean).map((r, i) => <div key={i}><p>{r.win}</p><p>{r.night}</p><p>{r.discussion}</p><p>{r.sheriff_rule}</p><p>{r.board_note}</p><p>出局后可观战或盲选接管。暂停或退出会保存当前行动；限流倒计时后自动继续。游戏以真实阵营胜负结束，没有固定轮数上限。</p></div>)}</div></>}
    </section></div>}
  </div>;
}

createRoot(document.getElementById("root")!).render(<App />);
