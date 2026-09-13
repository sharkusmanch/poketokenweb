"""UI strings in ko / en / ja / es — ports Localization.swift.

Strings are resolved in the daemon and shipped through state.json, so QML holds
no catalogue of its own. That keeps one source of truth and means changing the
language takes effect on the next poll without reloading the plasmoid.

Only strings the Linux UI actually renders are included; the Swift file also
covers macOS-only surfaces (Keychain, updater, support mail).
"""

from __future__ import annotations

LANGUAGES = ("en", "ko", "ja", "es")

# key: (en, ko, ja, es)
STRINGS: dict[str, tuple[str, str, str, str]] = {
    # tabs
    "home": ("Home", "홈", "ホーム", "Inicio"),
    "shop": ("Shop", "상점", "ショップ", "Tienda"),
    "bag": ("Bag", "가방", "バッグ", "Bolsa"),
    "collection": ("Collection", "컬렉션", "コレクション", "Colección"),
    "pokedex": ("Pokédex", "도감", "図鑑", "Pokédex"),
    "catch_log": ("Catch log", "포획 로그", "捕獲ログ", "Registro"),
    # today
    "todays_tokens": ("Today's tokens", "오늘의 토큰", "本日のトークン", "Tokens de hoy"),
    "this_week": ("This week", "이번 주", "今週", "Esta semana"),
    "this_month": ("This month", "이번 달", "今月", "Este mes"),
    # limits
    "limits_official": ("Limits (official)", "한도(공식)", "上限（公式）", "Límites (oficial)"),
    "five_hour_session": ("5-hour session", "5시간 세션", "5時間セッション", "Sesión de 5 horas"),
    "weekly": ("Weekly", "주간", "週間", "Semanal"),
    "resetting_now": ("resetting now", "지금 초기화 중", "リセット中", "reiniciando"),
    "limits_unavailable": (
        "Limits unavailable", "한도를 불러올 수 없음", "上限を取得できません",
        "Límites no disponibles",
    ),
    # companion
    "egg": ("Egg", "알", "タマゴ", "Huevo"),
    "final_form": ("Final form", "최종 형태", "最終形態", "Forma final"),
    "graduation": ("graduation", "졸업", "卒業", "graduación"),
    "next_evolution": ("next evolution", "다음 진화", "次の進化", "próxima evolución"),
    "shiny": ("Shiny", "이로치", "色違い", "Variocolor"),
    "raising": ("RAISING", "키우는 중", "育成中", "CRIANDO"),
    # status messages
    "status_idle": (
        "Keeping quiet today.", "오늘은 조용히 자리를 지켜요.", "今日は静かにしています。",
        "Hoy se mantiene tranquilo.",
    ),
    "status_working": (
        "Today's work is piling up.", "오늘의 작업 흔적이 쌓이고 있어요.",
        "本日の作業が積み重なっています。", "El trabajo de hoy se va acumulando.",
    ),
    "status_focus": (
        "In focus mode now.", "지금은 집중 모드예요.", "今は集中モードです。",
        "Ahora está en modo concentración.",
    ),
    "status_sleep": ("Sleeping now.", "지금은 자고 있어요.", "今は眠っています。", "Ahora está durmiendo."),
    "status_tired": (
        "Careful — the limit is close.", "조심해요 — 한도가 가까워요.", "注意 — 上限が近いです。",
        "Cuidado — el límite está cerca.",
    ),
    "status_egg": ("An egg is warming up.", "알이 따뜻해지고 있어요.", "タマゴが温まっています。", "Un huevo se está calentando."),
    "status_grew": ("It grew!", "성장했어요!", "成長しました！", "¡Ha crecido!"),
    # shop / bag
    "spendable_tokens": ("Spendable tokens", "사용 가능한 토큰", "使用可能なトークン", "Tokens disponibles"),
    "spend_hint": (
        "Spend the tokens you've used on items.", "사용한 토큰으로 아이템을 살 수 있어요.",
        "使ったトークンでアイテムを買えます。", "Gasta los tokens que has usado en objetos.",
    ),
    "buy": ("Buy", "구매", "購入", "Comprar"),
    "owned": ("Owned", "보유 중", "所持中", "En posesión"),
    "use": ("Use", "사용", "つかう", "Usar"),
    "active": ("Active", "적용 중", "適用中", "Activo"),
    "bag_empty": ("Your bag is empty.", "가방이 비어 있어요.", "バッグは空です。", "Tu bolsa está vacía."),
    "price": ("Price", "가격", "価格", "Precio"),
    "not_enough_tokens": ("Not enough tokens", "토큰이 부족해요", "トークンが足りません", "Tokens insuficientes"),
    # dex
    "no_pokemon_yet": (
        "No Pokémon caught yet!", "아직 잡은 포켓몬이 없어요!", "まだ捕まえたポケモンがいません！",
        "¡Aún no has capturado ninguno!",
    ),
    "legendary": ("Legendary", "전설", "伝説", "Legendario"),
    "rare": ("Rare", "희귀", "レア", "Raro"),
    "uncommon": ("Uncommon", "고급", "アンコモン", "Poco común"),
    "common": ("Common", "일반", "コモン", "Común"),
    # misc
    "refresh": ("Refresh", "새로고침", "更新", "Actualizar"),
    # "poketokend" was the systemd unit of the Linux port; in a browser it
    # names nothing the reader can act on.
    "stale_warning": (
        "Data is stale — the tracker may have stopped.",
        "데이터가 오래됐어요 — 트래커가 멈췄을 수 있어요.",
        "データが古いです — トラッカーが停止しているかもしれません。",
        "Datos obsoletos — puede que el rastreador se haya detenido.",
    ),
    "at_this_rate": ("at this rate, full at %1", "이 속도면 %1 에 도달", "このペースだと %1 に到達", "a este ritmo, lleno a las %1"),
    # cost provenance — "$0.00" and "we could not price this" are different claims
    "cost_unavailable": (
        "Cost unavailable", "비용을 알 수 없음", "コスト不明", "Coste no disponible",
    ),
    "cost_estimate_hint": (
        "Estimated from published model prices.", "공개된 모델 단가로 추정한 값이에요.",
        "公開されている料金からの概算です。", "Estimado con los precios públicos del modelo.",
    ),
    "cost_partial_hint": (
        "Some usage has no published price, so the real total is higher.",
        "단가가 없는 사용량이 있어 실제 합계는 더 커요.",
        "料金が公開されていない使用分があるため、実際の合計はこれより大きくなります。",
        "Parte del uso no tiene precio publicado, así que el total real es mayor.",
    ),
    # collection
    "released": ("RELEASED", "놓아줌", "逃がした", "LIBERADO"),
    "growth_boost": ("%1× growth", "%1× 성장", "成長 %1倍", "Crecimiento ×%1"),
    "pokedex_detail": ("Details", "상세", "詳細", "Detalles"),
    "close": ("Close", "닫기", "閉じる", "Cerrar"),
    "level": ("Level", "레벨", "レベル", "Nivel"),
    "ability": ("Ability", "특성", "とくせい", "Habilidad"),
    "gender": ("Gender", "성별", "せいべつ", "Género"),
    "male": ("Male", "수컷", "オス", "Macho"),
    "female": ("Female", "암컷", "メス", "Hembra"),
    "genderless": ("Unknown", "무성", "불명", "Desconocido"),
    "types": ("Type", "타입", "タイプ", "Tipo"),
    "base_stats": ("Stats", "능력치", "能力値", "Estadísticas"),
    "individual_values": ("Individual values", "개체값", "個体値", "Valores individuales"),
    "moves": ("Moves", "기술", "わざ", "Movimientos"),
    "no_moves": ("No moves recorded.", "기록된 기술이 없어요.", "記録された技がありません。", "Sin movimientos registrados."),
    "stat_hp": ("HP", "HP", "HP", "PS"),
    "stat_attack": ("Attack", "공격", "こうげき", "Ataque"),
    "stat_defense": ("Defense", "방어", "ぼうぎょ", "Defensa"),
    "stat_special_attack": ("Sp. Atk", "특수공격", "とくこう", "At. Esp."),
    "stat_special_defense": ("Sp. Def", "특수방어", "とくぼう", "Def. Esp."),
    "stat_speed": ("Speed", "스피드", "すばやさ", "Velocidad"),
    "detail_unavailable": (
        "Details could not be loaded.", "상세 정보를 불러오지 못했어요.",
        "詳細を読み込めませんでした。", "No se pudieron cargar los detalles.",
    ),
    # month trend
    "month_trend": ("This month, day by day", "이번 달 일별 사용량", "今月の日別使用量", "Este mes, día a día"),
    # shop gating
    "egg_needs_companion": (
        "Hatch your egg first.", "지금 있는 알이 먼저 부화해야 해요.",
        "先にタマゴをかえしてください。", "Primero incuba tu huevo.",
    ),
    # settings
    "settings": ("Settings", "설정", "設定", "Ajustes"),
    "difficulty": ("Difficulty", "난이도", "難易度", "Dificultad"),
    "growth_difficulty": ("Growth", "성장", "成長", "Crecimiento"),
    "shop_difficulty": ("Shop prices", "상점 가격", "ショップ価格", "Precios de la tienda"),
    "difficulty_hint": (
        "Lower is faster and cheaper. 100% is the original balance.",
        "낮을수록 빠르고 저렴해요. 100% 가 기본 밸런스예요.",
        "低いほど速く安くなります。100% が元のバランスです。",
        "Más bajo es más rápido y barato. 100% es el equilibrio original.",
    ),
    "difficulty_rescale_note": (
        "Changing this keeps the share of progress you have already earned.",
        "이 값을 바꿔도 이미 쌓은 진행도의 비율은 유지돼요.",
        "変更しても、すでに獲得した進捗の割合は保たれます。",
        "Al cambiarlo se conserva la parte del progreso ya conseguido.",
    ),
}

_INDEX = {code: i for i, code in enumerate(LANGUAGES)}


def t(key: str, language: str = "en") -> str:
    """Resolve one string, falling back to English then to the key itself.

    Returning the key rather than an empty string makes a missing translation
    visible in the UI instead of silently blanking a label.
    """
    row = STRINGS.get(key)
    if row is None:
        return key
    index = _INDEX.get(language, 0)
    return row[index] or row[0]


def catalogue(language: str = "en") -> dict[str, str]:
    """Every string resolved for one language, for shipping in state.json."""
    return {key: t(key, language) for key in STRINGS}
