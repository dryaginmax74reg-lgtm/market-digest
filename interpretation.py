"""Deterministic advisor prompts. Hypotheses are never presented as observed causes."""
from common import e


def signal_meaning(kind, change):
    if kind == "KEY":
        hypothesis = "Изменение ключевой ставки может повлиять на стоимость фондирования и доходности; реакция рынка зависит от ожиданий."
        action = "Сверить сроки депозитов, долю плавающих купонов и дюрацию с потребностями клиента."
    elif kind in ("OFZ", "RGBITR", "CURVE"):
        hypothesis = "Движение может отражать переоценку ставок или ликвидности; причина не установлена."
        action = "Проверить дюрацию, ликвидность и допустимую просадку портфеля; сопоставить с горизонтом клиента."
    elif kind in ("USD", "EUR", "CNY", "CBRT"):
        hypothesis = "Возможна переоценка валютного риска; официальный курс не равен доступной цене сделки."
        action = "Сверить валюту будущих расходов клиента, долю валюты и реальные котировки со спредом."
    elif kind == "BRENT":
        hypothesis = "Изменение нефти может повлиять на экспортную выручку и рубль, но связь нестабильна."
        action = "Проверить концентрацию в нефтяном секторе и валютные риски клиента."
    elif kind == "GOLD":
        hypothesis = "Возможна переоценка защитных активов; рублёвый результат зависит также от курса."
        action = "Проверить долю золота, валютную составляющую и издержки выбранного инструмента."
    elif kind == "BTC":
        hypothesis = "Движение может быть связано с изменением аппетита к риску; причина не установлена."
        action = "Проверить лимит риска и допустимость инструмента для клиента."
    else:
        hypothesis = "Причина движения требует проверки по первичным данным."
        action = "Проверить источник, ликвидность и соответствие риска профилю клиента."
    return {"hypothesis": hypothesis, "advisor_action": action}


def explain(fact_html, kind, change=0):
    rule = signal_meaning(kind, change)
    return ("<b>Факт:</b> " + fact_html + "\n<b>Гипотеза:</b> " + e(rule["hypothesis"])
            + "\n<b>Советнику:</b> " + e(rule["advisor_action"]))


def curve_block(a):
    if not a:
        return "⚠️ Аналитика кривой ОФЗ недоступна"
    from common import fnum, fpct, header
    lines = [header("📐", "КРИВАЯ ОФЗ", a.get("date"))]
    for tenor in (1, 2, 5, 10):
        value = a["points"].get(tenor)
        if value is None:
            continue
        parts = [f"{tenor}Y {fnum(value)}%"]
        for n in (1, 5):
            ch = a["changes"].get(n, {}).get(tenor)
            parts.append(f"{n} т.д.: " + (fpct(ch, 1, suffix=" б.п.") if ch is not None else "н/д"))
        lines.append(" · ".join(parts))
    if a["slope_bp"] is not None:
        line = f"Наклон 10Y–2Y: {fpct(a['slope_bp'], 1, suffix=' б.п.')}"
        for n, ch in a["slope_changes"].items():
            line += f" · Δ{n}: {fpct(ch, 1, suffix=' б.п.')}"
        lines.append(line)
    lines.extend("⚠️ " + e(w) for w in a["warnings"])
    ch = a["changes"].get(1, {}).get(10)
    if ch is not None and abs(ch) >= 10:
        lines.append(explain(f"Доходность 10Y изменилась на {fpct(ch, 1, suffix=' б.п.')} за торговый день.", "CURVE", ch))
    return "\n".join(lines)
