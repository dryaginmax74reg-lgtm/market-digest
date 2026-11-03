"""Deterministic advisor prompts. Hypotheses are never presented as observed causes."""
from common import e


def signal_meaning(kind, change):
    if kind == "KEY":
        hypothesis = "Изменение ключевой ставки может повлиять на стоимость фондирования и доходности; реакция рынка зависит от ожиданий."
        action = "Сверить сроки депозитов, долю плавающих купонов и дюрацию с потребностями клиента."
    elif kind == "CURVE":
        hypothesis = ("Рост доходности соответствует снижению цены сопоставимой облигации; возможна переоценка ставок или ликвидности."
                      if change > 0 else "Снижение доходности соответствует росту цены сопоставимой облигации; возможна переоценка ставок или ликвидности.")
        action = "Проверить дюрацию и чувствительность портфеля к развороту доходностей; сверить горизонт клиента и издержки сделки."
    elif kind == "RGBITR":
        hypothesis = ("Рост индекса полной доходности ОФЗ может отражать рост цен и купонный доход; это не гарантирует дальнейший рост."
                      if change > 0 else "Снижение индекса полной доходности ОФЗ может указывать на давление на цены; причины требуют проверки.")
        action = "Проверить вклад купонов и переоценки, дюрацию портфеля и допустимую просадку клиента."
    elif kind == "OFZ":
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


def explain(fact_html, kind, change=0, compact=False):
    rule = signal_meaning(kind, change)
    if compact:
        short = {
            "OFZ": ("Возможна переоценка ставок или ликвидности.", "Сверить дюрацию и ликвидность выпуска."),
            "RGBITR": ("Возможен вклад цен и купонов.", "Проверить риск дюрации портфеля."),
            "CNY": ("Может меняться валютный риск.", "Сверить валюту расходов и спред сделки."),
            "CBRT": ("Официальный курс отличается от цены сделки.", "Сверить доступные котировки и валютную долю."),
            "BRENT": ("Возможно влияние на экспортёров и рубль.", "Проверить долю нефтяного сектора."),
            "GOLD": ("Рублёвый результат зависит и от курса.", "Сверить долю золота и издержки."),
            "BTC": ("Возможна смена аппетита к риску.", "Проверить лимит риска клиента."),
        }
        hypothesis, action = short.get(kind, (rule["hypothesis"], rule["advisor_action"]))
        rule = {"hypothesis": hypothesis, "advisor_action": action}
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
    shift = a["slope_changes"].get(1)
    if shift is not None and abs(shift) >= 10:
        shape = "увеличился" if shift > 0 else "уменьшился"
        lines.append(f"<b>Факт:</b> Наклон 10Y–2Y {shape} на {fpct(shift, 1, suffix=' б.п.')} за 1 т.д."
                     "\n<b>Гипотеза:</b> Короткий и длинный участки переоцениваются неодинаково; причина требует проверки."
                     "\n<b>Советнику:</b> Сопоставить риск коротких и длинных позиций, не выводить прогноз ставки только из наклона.")
    return "\n".join(lines)
