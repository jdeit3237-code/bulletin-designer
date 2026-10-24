"""Catholic liturgical calendar helpers used for the designer's suggestions (general Roman calendar, US norms)."""
from datetime import date, timedelta

ORDINALS = ["", "First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth",
            "Eleventh", "Twelfth", "Thirteenth", "Fourteenth", "Fifteenth", "Sixteenth", "Seventeenth", "Eighteenth",
            "Nineteenth", "Twentieth", "Twenty-first", "Twenty-second", "Twenty-third", "Twenty-fourth",
            "Twenty-fifth", "Twenty-sixth", "Twenty-seventh", "Twenty-eighth", "Twenty-ninth", "Thirtieth",
            "Thirty-first", "Thirty-second", "Thirty-third", "Thirty-fourth"]

FIXED = {
    (1, 1): "Solemnity of Mary, the Holy Mother of God", (2, 2): "Presentation of the Lord",
    (2, 22): "Chair of St. Peter (parish patron)", (3, 19): "St. Joseph, Spouse of Mary",
    (3, 25): "Annunciation of the Lord", (6, 24): "Nativity of St. John the Baptist",
    (6, 29): "Sts. Peter and Paul (parish patron)", (8, 6): "Transfiguration of the Lord",
    (8, 15): "Assumption of the Blessed Virgin Mary", (9, 14): "Exaltation of the Holy Cross",
    (10, 4): "St. Francis of Assisi", (10, 7): "Our Lady of the Rosary", (11, 1): "All Saints",
    (11, 2): "All Souls (Commemoration of the Faithful Departed)", (11, 11): "Veterans Day / St. Martin of Tours",
    (12, 8): "Immaculate Conception", (12, 12): "Our Lady of Guadalupe", (12, 25): "Nativity of the Lord (Christmas)",
}

SEASON_IDEAS = {
    "Advent": ["Advent wreath with candles", "Starry night / star of Bethlehem", "Mary and the Annunciation", "Wheat and olive branch"],
    "Christmas": ["Nativity scene", "Holy Family", "Star and manger", "Three Kings and gifts (Epiphany)"],
    "Lent": ["Simple cross in violet", "Ashes and the cross", "Prayer, fasting, almsgiving icons", "Stations of the Cross"],
    "Triduum": ["Chalice and towel (Holy Thursday)", "Crucifix (Good Friday)", "Paschal candle (Holy Saturday)"],
    "Easter": ["Empty tomb and lilies", "Paschal candle", "Risen Christ / sunrise", "Dove (Pentecost)"],
    "Ordinary Time": ["Fish and nets (St. Peter the Fisherman)", "Chalice and host", "Bible with open pages", "Sailboat and sea (Florida Keys)"],
}
FEAST_IDEAS = {
    "All Saints": ["Gathering of saints with halos", "Stained-glass saint border"],
    "All Souls": ["Candle and prayer for the faithful departed", "Book of Remembrance / Novena"],
    "Christ the King": ["Crown and scepter", "Christ Pantocrator"],
    "Pentecost": ["Descending dove and flames", "Red flames border"],
    "Peter": ["Keys of the Kingdom", "Fisherman's net and keys"],
    "Mary": ["Rose and Marian blue", "Rosary"],
    "Thanksgiving": ["Cornucopia / harvest", "Grateful hands"],
    "Veterans": ["Flag and cross", "Prayer for those who served"],
}


def easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(y, month, day + 1)


def _sunday_on_or_after(d):
    return d + timedelta(days=(6 - d.weekday()) % 7)


def info(d):
    """Return season, liturgical color, observance name, theme title and graphic ideas for one date."""
    y = d.year
    e = easter(y)
    advent = date(y, 12, 25) - timedelta(days=(date(y, 12, 25).weekday() + 1) % 7 or 7) - timedelta(days=21)
    christ_king = advent - timedelta(days=7)
    epiphany = _sunday_on_or_after(date(y, 1, 2))
    baptism = epiphany + timedelta(days=7) if epiphany.day <= 7 else epiphany
    if epiphany.day >= 7:
        baptism = epiphany + timedelta(days=1)
    ash, pentecost = e - timedelta(days=46), e + timedelta(days=49)
    name, season, color = None, "Ordinary Time", "Green"
    if d >= advent:
        season, color = "Advent", "Violet"
        n = (d - advent).days // 7 + 1 if d.weekday() == 6 else None
        if n == 3:
            color = "Rose"
        if d.weekday() == 6 and d < date(y, 12, 25):
            name = f"{ORDINALS[n]} Sunday of Advent"
        if d >= date(y, 12, 25):
            season, color = "Christmas", "White"
    elif d <= baptism and d < ash:
        season, color = "Christmas", "White"
    elif ash <= d < e - timedelta(days=3):
        season, color = "Lent", "Violet"
        if d == ash:
            name = "Ash Wednesday"
        elif d.weekday() == 6:
            name = "Palm Sunday of the Passion of the Lord" if d == e - timedelta(days=7) else \
                f"{ORDINALS[(d - ash).days // 7 + 1]} Sunday of Lent"
            color = "Red" if d == e - timedelta(days=7) else ("Rose" if d == e - timedelta(days=21) else "Violet")
    elif e - timedelta(days=3) <= d < e:
        season, color = "Triduum", "White" if d.weekday() == 3 else "Red" if d.weekday() == 4 else "White"
        name = {3: "Holy Thursday (Mass of the Lord's Supper)", 4: "Good Friday", 5: "Holy Saturday"}.get(d.weekday())
    elif e <= d <= pentecost:
        season, color = "Easter", "White"
        name = "Easter Sunday" if d == e else None
        if d.weekday() == 6 and d != e:
            name = "Pentecost Sunday" if d == pentecost else (
                "Divine Mercy Sunday (Second Sunday of Easter)" if d == e + timedelta(days=7) else
                f"{ORDINALS[(d - e).days // 7 + 1]} Sunday of Easter")
            color = "Red" if d == pentecost else "White"
        if d == e + timedelta(days=42):
            name = "Ascension of the Lord"
    if season == "Christmas" and d.weekday() == 6 and not name:
        name = "Epiphany of the Lord" if d == epiphany and d.month == 1 else \
            "Baptism of the Lord" if d == baptism or (d - timedelta(days=1)) == baptism else "Holy Family / Sunday in the Christmas Season"
    if season == "Ordinary Time" and d.weekday() == 6 and not name:
        if d > pentecost:
            n = 34 - (christ_king - d).days // 7
            special = {e + timedelta(days=56): "Most Holy Trinity", e + timedelta(days=63): "Most Holy Body and Blood of Christ (Corpus Christi)"}
            name = special.get(d) or ("Our Lord Jesus Christ, King of the Universe" if d == christ_king else f"{ORDINALS[n]} Sunday in Ordinary Time")
            if d in special or d == christ_king:
                color = "White"
        else:
            name = f"{ORDINALS[2 + (d - _sunday_on_or_after(baptism)).days // 7]} Sunday in Ordinary Time"
    feast = FIXED.get((d.month, d.day))
    if d.month == 11 and d.weekday() == 3 and 22 <= d.day <= 28:
        feast = "Thanksgiving Day"
    if feast:
        solemn = (11, 1), (12, 25), (1, 1), (8, 15), (12, 8)
        name = f"{name}; {feast}" if name and d.weekday() == 6 and (d.month, d.day) not in solemn else feast
        if d.month == 11 and d.day == 2:
            color = "Violet / White"
        elif d.month in (8, 12, 1, 2, 3) and d.day in (15, 8, 12, 1, 2, 25, 19) or d.month == 11 and d.day == 1:
            color = "White"
    ideas = list(SEASON_IDEAS[season])
    text = (name or "").lower()
    for key, extra in FEAST_IDEAS.items():
        if key.lower() in text or (key == "Mary" and ("mary" in text or "immaculate" in text or "assumption" in text or "guadalupe" in text)):
            ideas = extra + ideas
    return {"date": d, "season": season, "color": color, "name": name, "ideas": ideas[:6],
            "readings_url": f"https://bible.usccb.org/bible/readings/{d:%m%d%y}.cfm"}


def week(sunday):
    return [info(sunday + timedelta(days=n)) for n in range(-1, 7)]
