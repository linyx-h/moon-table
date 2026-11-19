import random

ROLES = {
    "villager": {"name": "村民", "camp": "good", "skill": "没有夜间技能。通过发言、观察与放逐投票找到狼人。", "color": "#dca878", "icon": "wheat"},
    "wolf": {"name": "狼人", "camp": "wolf", "skill": "夜间认识狼队友，共同选择一名玩家袭击，也可不袭击。可故意袭击普通狼队友，尝试让女巫救下他来骗取信任；不能这样袭击狼美人。白天可公开狼人身份并立即出局，让白天结束。", "color": "#dc746c", "icon": "wolf"},
    "seer": {"name": "预言家", "camp": "good", "skill": "每晚查验另一名存活玩家，得知对方是狼人或好人。", "color": "#a69be0", "icon": "eye"},
    "witch": {"name": "女巫", "camp": "good", "skill": "整局各有一瓶解药、毒药，每晚最多使用一瓶；不可救自己。解药用完后不再得知谁被狼人袭击。守护与解药同时作用于同一人，仍会死亡。", "color": "#afc78f", "icon": "potion"},
    "hunter": {"name": "猎人", "camp": "good", "skill": "被狼人袭击或被投票赶走时可开枪带走一人。被毒死、被白狼王带走时不能开枪。已满足胜利条件则立即结束；最后一位有特殊技能的好人死亡后，不能再开枪改变胜负。", "color": "#dfb45f", "icon": "bow"},
    "guard": {"name": "守卫", "camp": "good", "skill": "每晚守护一人，也可不守护；可守护自己，不可连续两晚守护同一人。守护不能挡毒药。", "color": "#8cb9d3", "icon": "shield"},
    "idiot": {"name": "白痴", "camp": "good", "skill": "第一次被放逐时翻牌存活，此后失去投票权；仍可发言。其他原因死亡没有豁免。", "color": "#f0cb8d", "icon": "bell"},
    "wolfking": {"name": "狼王", "camp": "wolf", "skill": "参与狼队袭击。被放逐或被枪击时可开枪带走一人；自爆、毒杀时不能开枪。", "color": "#cb787b", "icon": "crown"},
    "whitewolf": {"name": "白狼王", "camp": "wolf", "skill": "参与狼队袭击。白天发言阶段可自爆并带走一名玩家，立即进入黑夜；被带走的猎人不能开枪。", "color": "#e8d5bc", "icon": "whitewolf"},
    "girl": {"name": "小女孩", "camp": "good", "skill": "狼人睁眼时可选择偷看，看到本夜行动的狼队座位，但不知道具体狼人角色。偷看期间画面亮起，不读取狼队私聊。每次有 20% 概率被发现，狼人会改为袭击小女孩；守卫不能保护小女孩，女巫可救。这是本版的线上改编玩法。", "color": "#f0a4b3", "icon": "ribbon"},
    "knight": {"name": "骑士", "camp": "good", "skill": "整局一次，白天发言或质询时翻牌决斗一人。决斗狼人则对方出局、立即入夜；决斗好人则自己出局、白天继续。骑士决斗狼美人不会触发殉情。", "color": "#b8c9df", "icon": "sword"},
    "wolfbeauty": {"name": "狼美人", "camp": "wolf", "skill": "参与狼队袭击，另每晚魅惑一人，不可连续魅惑同一人。被投票赶走、枪击或毒杀时，被魅惑者也一起出局且不能开枪；被骑士决斗不触发。不能主动公开狼人身份来结束白天，也不能被狼队故意袭击。", "color": "#e995ae", "icon": "rose"},
    "dreamer": {"name": "摄梦人", "camp": "good", "skill": "每晚必须让另一人梦游，对方不知情、免疫当夜伤害。连续两晚梦游同一人则其出局；自己夜间出局则本夜梦游者一并出局。梦亡不能开枪，解药不能救梦亡。", "color": "#aaa8ed", "icon": "dream"},
    "raven": {"name": "乌鸦", "camp": "good", "skill": "每晚诅咒一人，可选择自己或不使用。次日公布标记，该人被投票赶走时的得票额外增加 0.5；不影响警长选举。平票后重新投票时，只对仍在候选中的被诅咒者生效。", "color": "#98a5c6", "icon": "feather"},
    "demonhunter": {"name": "猎魔人", "camp": "good", "skill": "从第二夜起，每晚可狩猎另一人。猎中狼人则狼人出局，猎中好人则自己出局；可不狩猎。不怕女巫毒药，但仍会被狼人袭击。因狩猎或梦游死亡时不能开枪。", "color": "#dea56f", "icon": "crossbow"},
    "gravekeeper": {"name": "守墓人", "camp": "good", "skill": "从第二夜起，自动得知上一白天被投票放逐者是狼人还是好人。平票无人出局、白痴免于放逐时没有结果。只确认阵营，不确认具体职业。", "color": "#a9adb8", "icon": "lantern"},
    "silencer": {"name": "禁言长老", "camp": "good", "skill": "每晚可指定一人或不使用技能。目标次日不能参加圆桌发言与追问，仍可投票、竞选警长、留遗言和发动身份技能。竞选警长时仍能发言。", "color": "#c5ad89", "icon": "silence"},
    "bear": {"name": "驯熊师", "camp": "good", "skill": "每天黎明自动检查左右最近的存活邻座。任一邻座是狼人则公示熊咆哮，否则公示熊很安静。自己死亡或技能被封印后永远安静；公示不透露驯熊师座位。", "color": "#bd926c", "icon": "paw"},
    "elder": {"name": "长老", "camp": "good", "skill": "首次实际受到狼人袭击时不会死亡，守护或解药已救下时不消耗这次保护。被投票赶走、毒杀或枪杀则立即出局，让所有好人失去特殊技能；计算胜负时仍属于特殊角色。其他死亡方式不会让好人失去技能。", "color": "#c6c4ac", "icon": "staff"},
    "fox": {"name": "狐狸", "camp": "good", "skill": "每晚可查验一位存活玩家及其左右最近的存活邻座（可包含自己）。只得知这组人中有狼或全部好人；查出全部好人后永久失去查验能力，也可暂不查验。", "color": "#e3a270", "icon": "fox"},
    "gargoyle": {"name": "石像鬼", "camp": "wolf", "skill": "不认识普通狼队，也不参加狼队私聊，每夜独立查验另一人的具体身份。其他狼人全部出局后可独立袭击，先查验再袭击。不能主动公开狼人身份来结束白天，也不能故意袭击自己；普通狼人不知道你是谁，可能误伤你。", "color": "#8f9eb4", "icon": "stone"},
}

MODES = {
    "classic": {"name": "经典圆桌", "note": "基础角色 · 适合先熟悉推理", "min": 6},
    "advanced": {"name": "白狼月夜", "note": "白狼王可带走一人 · 竞选警长与隐藏特殊身份", "min": 9},
    "duel": {"name": "骑士与狼美人", "note": "白天决斗 · 魅惑牵制 · 敢于验逻辑", "min": 9},
    "dream": {"name": "狼王摄梦人", "note": "连梦进攻 · 夜间保护 · 狼王枪", "min": 9},
    "omen": {"name": "乌鸦预兆", "note": "额外半票 · 竞选警长与分析每个人的投票", "min": 9},
    "hunt": {"name": "猎魔之夜", "note": "第二夜开始狩猎 · 猜错会让自己出局", "min": 9},
    "tomb": {"name": "石像鬼守墓人", "note": "狼人独立查身份 · 得知被投票赶走者的阵营", "min": 9},
    "forest": {"name": "熊与狐狸", "note": "熊狐代替预言家 · 邻座范围查验", "min": 9},
    "elder": {"name": "长老守护", "note": "抵挡一次狼人袭击 · 谨慎投票与用药", "min": 9},
    "allgods": {"name": "全员特殊角色 · 娱乐", "note": "无普通村民 · 消灭全部好人才能获胜", "min": 9},
}

GOD_POOL = ("hunter", "guard", "idiot", "girl", "knight", "dreamer", "raven",
            "demonhunter", "gravekeeper", "silencer", "bear", "elder", "fox")
SPECIAL_GODS = {"duel": "knight", "dream": "dreamer", "omen": "raven", "hunt": "demonhunter",
                "tomb": "gravekeeper", "elder": "elder"}


def board_setup(count, mode):
    wolves = 2 if count <= 8 else 3 if count <= 11 else 4 if count <= 13 else 5
    gods = {
        6: ["seer", "hunter"],
        7: ["seer", "witch"],
        8: ["seer", "witch", "hunter"],
        9: ["seer", "witch", "hunter"],
        10: ["seer", "witch", "hunter"],
        11: ["seer", "witch", "hunter", "guard"],
        12: ["seer", "witch", "hunter", "idiot"],
        13: ["seer", "witch", "hunter", "guard", "idiot"],
        14: ["seer", "witch", "hunter", "guard", "idiot"],
        15: ["seer", "witch", "hunter", "guard", "idiot"],
    }[count]
    pack = ["wolf"] * wolves
    if mode == "advanced" and count >= 9:
        pack[0] = "whitewolf"
        if 13 <= count < 15:
            pack[1] = "wolfking"
        if count == 12:
            gods[-1] = "guard"
        if 13 <= count < 15:
            gods[-1] = "girl"
    if mode in SPECIAL_GODS:
        gods[2 if mode == "hunt" else -1] = SPECIAL_GODS[mode]
        if mode == "duel" and len(gods) >= 4:
            gods[2] = "guard"
            if len(gods) == 5:
                gods[-2] = "idiot"
        pack[0] = {"duel": "wolfbeauty", "dream": "wolfking", "tomb": "gargoyle"}.get(mode, "wolf")
    if mode == "forest":
        gods[0], gods[-1] = "bear", "fox"
    if mode == "allgods":
        gods = ["seer", "witch"] + list(GOD_POOL[:count - wolves - 2])
    fixed = ["seer"] if count == 6 else ["bear", "witch", "fox"] if mode == "forest" else ["seer", "witch"]
    if mode in SPECIAL_GODS:
        fixed.append(SPECIAL_GODS[mode])
    return pack, gods, fixed


def board_options(count, mode):
    _, gods, fixed = board_setup(count, mode)
    pool = [r for r in GOD_POOL if r not in fixed and (count >= 9 or r in ("hunter", "idiot"))]
    return {"fixed_gods": fixed, "god_slots": len(gods) - len(fixed), "selectable_gods": pool}


def board(count, mode, random_gods=False, selected_gods=None):
    pack, gods, fixed = board_setup(count, mode)
    options = board_options(count, mode)
    selected = [] if selected_gods is None else selected_gods
    if not isinstance(selected, list) or any(not isinstance(r, str) or r not in options["selectable_gods"] for r in selected):
        raise ValueError("只能选择列表中的可选特殊角色。")
    if len(selected) != len(set(selected)) or len(selected) > options["god_slots"]:
        raise ValueError("特殊角色不能重复或超过本桌名额。")
    if selected and not random_gods:
        raise ValueError("请在复杂版中选择特殊角色。")
    if random_gods:
        pool = [r for r in options["selectable_gods"] if r not in selected]
        # 手选牌保留，空缺从剩余牌中补齐，每种神职最多一张。
        gods = fixed + selected + random.sample(pool, options["god_slots"] - len(selected))
    return pack + gods + ["villager"] * (count - len(pack) - len(gods))


def rules(count, mode="classic"):
    return {
        "win": "狼人全部出局，好人获胜；好人全部出局，狼人获胜。" if count <= 8 or mode == "allgods" else "狼人全部出局，好人获胜；普通村民全部出局，或有特殊技能的好人全部出局，狼人获胜。",
        "sheriff": count >= 9,
        "discussion": "首夜结算前竞选警长（9 人以上）。白天依次发言，警长最后建议大家投给谁；随后由声称相同身份或被重点怀疑的玩家简短回应，真人可点名追问一次，再投票。平票时，得票相同的人再发言并重新投票；再次平票则无人出局。",
        "night": "摄梦人 / 守卫 → 石像鬼查验 → 狼队袭击（小女孩此时可偷看）→ 狼美人魅惑 → 预言家 / 狐狸 / 守墓人 → 女巫 → 乌鸦 / 猎魔人 / 禁言长老 → 统一结算与熊提示。主持人只公布哪个角色行动，不公布座位、目标或结果。先计算狼人袭击造成的胜负：若狼人已经获胜，即使女巫毒死最后一只狼人也不能改变结果。再处理开枪与遗言。第一夜和白天出局者有遗言；因魅惑牵连或梦游死亡时不能开枪。",
        "sheriff_rule": "警长是大家选出的带队玩家。竞选者发言后，未竞选的存活玩家投票；平票者再发言并重新投票，再次平票则不设警长。警长投票算 1.5 票，并决定发言顺序；出局时选择接任者，或结束警长职位。竞选期间狼人公开身份并出局，则取消这次竞选。",
        "board_note": "本桌为「%s」。经典 12 人为 4 名狼人、4 名有特殊技能的好人、4 名普通村民；经典 15 人各有 5 名，特殊角色是预言家、女巫、猎人、白痴、守卫。复杂版保留核心角色，可手选其他特殊角色，未选满随机补齐；开局公布后整局固定。全员特殊角色和 6～8 人小桌中，狼人需让全部好人出局才能获胜。乌鸦额外增加 0.5 票，小女孩偷看有 20%% 被发现风险。出局后可盲选接管角色练习，但仍会记得之前知道的信息。" % MODES[mode]["name"],
    }


def catalog_data():
    return {"roles": ROLES, "modes": MODES, "random_pool": list(GOD_POOL),
            "boards": [{"count": n, "mode": m, "roles": board(n, m), "rules": rules(n, m), **board_options(n, m)}
                       for n in range(6, 16) for m, info in MODES.items() if n >= info["min"]]}
