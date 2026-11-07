"""Deterministic checks: cheap, explainable signals that back up (or challenge) the LLM verdicts.

Each rule returns {"signal": True/False/None, "turns": [...], "note": str}. Used as hints in the prompt and for
LLM-vs-rule disagreement -> needs_review. Keyword lists cover RU / KK / EN.
"""
from __future__ import annotations

import re

GREET = r"здравствуй|добрый (день|вечер|утро)|доброе утро|приветству|сәлем|сәлеметсіз|қайырлы|hello|hi\b|good (morning|afternoon|evening)|welcome"
INTRO = r"меня зовут|на связи|вас приветствует|с вами|менің атым|атым|my name is|this is \w+ from|i am \w+ from"
THANKS = r"спасибо за обращение|благодарим|рад(а)? (был[аи]?|помочь)|рахмет|алғыс|thank you for (contacting|reaching|your patience)|thanks for"
FAREWELL = r"хорошего дня|всего доброго|до свидания|обращайтесь|сау болыңыз|күніңіз сәтті|have a (nice|great|good) day|goodbye|bye\b"
MORE_HELP = r"(чем|могу ли) (ещё|еще) (помочь|могу)|остались (ли )?вопросы|есть ещё вопросы|тағы сұрақ|anything else|any other questions|can i help with anything"
APOLOGY = r"извин|прош[уy] прощен|приносим извинения|кешіріңіз|sorry|apologi"
RUDE = r"\bдура?к|идиот|тупо|заткн|отстань|не мои проблемы|сами виноваты|ваши проблемы|shut up|stupid|idiot|not my problem"
PII_ASK = r"полн(ый|ого) номер карты|cvv|cvc|код из смс|пароль|pin|пин-код|password|card number|sms code"
WAIT = r"ожидайте|минут(к|у)|подождите|уточню|күте тұрыңыз|please wait|one moment|let me check"


def _find(turns, pattern, speaker="OPERATOR"):
    rx = re.compile(pattern, re.I)
    return [t["i"] for t in turns if (speaker is None or t["speaker"] == speaker) and rx.search(t["text"])]


def run(dialogue: dict) -> dict:
    turns = dialogue["turns"]
    op = [t for t in turns if t["speaker"] == "OPERATOR"]
    first_ops = [t["i"] for t in op[:2]]
    last_ops = [t["i"] for t in op[-2:]]
    g = _find(turns, GREET)
    r = {
        "greeting": {"signal": bool(set(g) & set(first_ops)), "turns": g[:3], "note": "приветствие в первых репликах оператора"},
        "introduction": {"signal": bool(_find(turns, INTRO)), "turns": _find(turns, INTRO)[:3], "note": "оператор представился"},
        "farewell": {"signal": bool(set(_find(turns, FAREWELL) + _find(turns, THANKS)) & set(last_ops)), "turns": (_find(turns, FAREWELL) + _find(turns, THANKS))[:3], "note": "прощание/благодарность в конце"},
        "more_help": {"signal": bool(_find(turns, MORE_HELP)), "turns": _find(turns, MORE_HELP)[:3], "note": "уточнил, остались ли вопросы"},
        "apology": {"signal": bool(_find(turns, APOLOGY)), "turns": _find(turns, APOLOGY)[:3], "note": "извинение"},
        "rudeness": {"signal": bool(_find(turns, RUDE)), "turns": _find(turns, RUDE)[:3], "note": "грубость/обвинение клиента (крит.)"},
        "sensitive_data_request": {"signal": bool(_find(turns, PII_ASK)), "turns": _find(turns, PII_ASK)[:3], "note": "запрос CVV/пароля/кода (крит.)"},
        "hold_notice": {"signal": bool(_find(turns, WAIT)), "turns": _find(turns, WAIT)[:3], "note": "предупредил об ожидании"},
    }
    r["stats"] = {
        "n_turns": len(turns),
        "operator_turns": len(op),
        "client_turns": sum(t["speaker"] == "CLIENT" for t in turns),
        "operator_chars_avg": round(sum(len(t["text"]) for t in op) / max(len(op), 1)),
    }
    return r


def summary_for_prompt(r: dict) -> str:
    parts = [f"- {k}: {'yes' if v['signal'] else 'no'} (turns {v['turns']})" for k, v in r.items() if k != "stats"]
    return "Automatic keyword hints (may be wrong, verify yourself):\n" + "\n".join(parts)


RULE_FOR_ITEM = [  # (regex on item title/description, rule key)
    (r"привет|greet|сәлем", "greeting"),
    (r"предст|назвал|имя|introduc", "introduction"),
    (r"прощ|заверш|farewell|goodbye|closing", "farewell"),
    (r"ещё вопрос|еще вопрос|дополнительн|anything else|other question", "more_help"),
    (r"груб|вежлив|этик|rude|polite|tone|тон", "rudeness"),
    (r"персональн|cvv|код|безопасн|security|sensitive|данн", "sensitive_data_request"),
    (r"ожидан|hold|wait", "hold_notice"),
]


def rule_key_for(item: dict) -> str | None:
    text = (item["title"] + " " + item.get("description", "")).lower()
    for rx, key in RULE_FOR_ITEM:
        if re.search(rx, text):
            return key
    return None
