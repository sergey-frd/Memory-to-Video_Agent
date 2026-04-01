from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
from types import SimpleNamespace

from models.video_sequence import SequenceCandidate, SequenceOptimizationResult, SequenceRecommendationEntry
from utils.transition_recommendations import _select_recommended_transition_type


@dataclass(frozen=True)
class StructureBeatSpec:
    index: int
    title: str
    key: str
    purpose: str
    music: str
    sound: str
    color: str
    end_ratio: float


@dataclass(frozen=True)
class SoundtrackReference:
    title: str
    artist: str
    note: str
    tags: tuple[str, ...]


_STRUCTURE_BEATS = [
    StructureBeatSpec(
        index=1,
        title="Вход",
        key="intro",
        purpose="Зритель должен быстро войти в пространство, понять среду, атмосферу и базовый образ героя.",
        music="Мягкий старт без перегруза: атмосферный пад, лёгкая гармония или аккуратный пульс без ударной доминации.",
        sound="Оставить читаемый воздух сцены, мягкий room tone, без плотных эффектов на первом кадре.",
        color="Сохранить натуральную палитру и читаемый фон; контраст не зажимать, чтобы мир открылся спокойно.",
        end_ratio=0.15,
    ),
    StructureBeatSpec(
        index=2,
        title="Крючок",
        key="hook",
        purpose="Дать первый эмоциональный или визуальный акцент, который удержит внимание и задаст направление истории.",
        music="Первый музыкальный акцент: подъём по ритму, первый заметный beat-hit или короткий melodic hook.",
        sound="Подчеркнуть один акцентный жест: вдох, щелчок, swish, микро-rise или точечный hit по движению/взгляду.",
        color="Чуть усилить локальный контраст или световой акцент, чтобы крючок ощущался как первый поворот.",
        end_ratio=0.30,
    ),
    StructureBeatSpec(
        index=3,
        title="Развитие",
        key="development",
        purpose="Нарастить историю через чередование планов, серийность образа, лица, одежды и более ясный ритм монтажа.",
        music="Добавлять ритмический рисунок и постепенное нарастание, но не раскрывать главный пик слишком рано.",
        sound="Соединять кадры лёгкими whoosh, texture-layers и микросклейками, чтобы монтаж ускорялся без шума.",
        color="Держать continuity по тону и температуре, но позволять умеренный рост плотности и насыщенности.",
        end_ratio=0.60,
    ),
    StructureBeatSpec(
        index=4,
        title="Пик",
        key="peak",
        purpose="Собрать самый сильный эмоциональный или визуальный момент и вывести его как кульминацию клипа.",
        music="Максимальный по силе музыкальный такт, самый плотный beat, сильная вершина динамики или раскрытие темы.",
        sound="Самый точный акцент по удару, жесту, повороту головы, смене позы или эмоциональному взгляду.",
        color="Максимально выразительный цветовой слой: пик по контрасту, свету или насыщенности внутри общей палитры.",
        end_ratio=0.78,
    ),
    StructureBeatSpec(
        index=5,
        title="Успокоение",
        key="calm",
        purpose="Снять напряжение после пика, оставить смысл понятным и перевести ролик в мягкое послевкусие.",
        music="Убрать часть плотности, оставить хвост мелодии или более редкий ритм, дать зрителю выдохнуть.",
        sound="Сделать переходы менее агрессивными, вернуть воздух, реверб-хвосты и более мягкие текстуры.",
        color="Чуть смягчить контраст, дать больше воздуха в светах или тепле, чтобы спад ощущался естественно.",
        end_ratio=0.90,
    ),
    StructureBeatSpec(
        index=6,
        title="Послевкусие",
        key="aftertaste",
        purpose="Оставить финальный образ, эмоциональный след и ощущение завершённости без суеты.",
        music="Последний музыкальный штрих: sustain, fade tail, мягкий финальный аккорд или тишина с остаточным хвостом.",
        sound="Финальный воздух, лёгкий tail, затухающий ambience или чистая тишина после последнего образа.",
        color="Финальный образ должен быть цельным: не перегружать коррекцией, дать кадру спокойно закрепиться в памяти.",
        end_ratio=1.0,
    ),
]

_SOUNDTRACK_CATEGORY_TITLES = {
    "non_classical": "1. Не из мировой классической музыки",
    "classical": "2. Из мировой классической музыки",
    "jazz": "3. Из джаза",
}

_GENERIC_SERIES_TOKENS = {
    "главный",
    "главная",
    "главное",
    "главные",
    "возможно",
    "вероятно",
    "персонаж",
    "персонажа",
    "персонажу",
    "персонажем",
    "объект",
    "объекта",
    "объектом",
    "участник",
    "участника",
    "участников",
    "находящийся",
    "находящаяся",
    "находящиеся",
    "около",
    "лет",
    "часть",
    "образ",
    "фигура",
    "женский",
    "женское",
    "женского",
    "мужской",
    "мужское",
    "someone",
    "person",
    "main",
}

_SOUNDTRACK_REFERENCES = {
    "non_classical": [
        SoundtrackReference(
            title="A Walk",
            artist="Tycho",
            note="подходит для светлого движения, мягкого ритма и ощущения живого пути без перегруза.",
            tags=("light", "motion", "family", "warm", "travel", "scenic", "outdoor"),
        ),
        SoundtrackReference(
            title="Bless This Morning Year",
            artist="Helios",
            note="хорошо ложится на тёплый, семейный, наблюдательный монтаж с мягким эмоциональным ростом.",
            tags=("warm", "gentle", "family", "intimate"),
        ),
        SoundtrackReference(
            title="Arrival of the Birds",
            artist="The Cinematic Orchestra",
            note="даёт красивое кинематографичное нарастание, когда ролик строится на образе, памяти и визуальном раскрытии.",
            tags=("elegant", "dreamy", "rise", "warm", "scenic", "travel"),
        ),
        SoundtrackReference(
            title="Cirrus",
            artist="Bonobo",
            note="работает, если нужен более живой пульс, монтажное ускорение и современный саунд без резкой агрессии.",
            tags=("dynamic", "motion", "energetic", "urban", "travel", "city"),
        ),
        SoundtrackReference(
            title="I Can Almost See You",
            artist="Hammock",
            note="подходит для более хрупкого, воздушного и чуть меланхоличного ролика с длинным послевкусием.",
            tags=("reflective", "night", "dreamy", "intimate"),
        ),
        SoundtrackReference(
            title="Looped",
            artist="Kiasmos",
            note="полезен, когда нужен минималистичный моторик-пульс, холоднее по тону и собраннее по ритму.",
            tags=("night", "cool", "motion", "minimal", "travel", "city"),
        ),
        SoundtrackReference(
            title="Your Hand in Mine",
            artist="Explosions in the Sky",
            note="подходит для темы взросления, памяти и светлого эмоционального подъёма без прямолинейной поп-динамики.",
            tags=("nostalgic", "childhood", "rise", "warm"),
        ),
        SoundtrackReference(
            title="Saman",
            artist="Ólafur Arnalds",
            note="хорошо работает на деликатной, камерной и очень личной линии, особенно в ролике с памятью и близостью.",
            tags=("archive", "gentle", "intimate", "reflective"),
        ),
        SoundtrackReference(
            title="Steep Hills of Vicodin Tears",
            artist="A Winged Victory for the Sullen",
            note="подходит для архивного, более медитативного и многослойного по памяти видеоряда.",
            tags=("archive", "reflective", "dreamy", "multi_generation"),
        ),
        SoundtrackReference(
            title="Postcards From Italy",
            artist="Beirut",
            note="подходит для маршрута через красивые места, семейной поездки и лёгкого ощущения дорожной открытки.",
            tags=("travel", "cultural", "scenic", "warm", "family"),
        ),
        SoundtrackReference(
            title="Friday Morning",
            artist="Khruangbin",
            note="работает для расслабленного путешествия, тёплого воздуха, дороги и мягкого отпускного ритма.",
            tags=("travel", "leisure", "sun", "relaxed", "scenic"),
        ),
        SoundtrackReference(
            title="Threnody",
            artist="Goldmund",
            note="подходит для очень деликатного, камерного и почти безмолвного ролика, где важны память, воздух и тонкая личная интонация.",
            tags=("archive", "reflective", "intimate", "minimal"),
        ),
        SoundtrackReference(
            title="Flight from the City",
            artist="Jóhann Jóhannsson",
            note="хорошо работает на линии воспоминания, одиночного переживания и мягкого медленного времени без резких монтажных акцентов.",
            tags=("archive", "reflective", "gentle", "intimate"),
        ),
        SoundtrackReference(
            title="Music for a Found Harmonium",
            artist="Penguin Cafe Orchestra",
            note="даёт живую, светлую и чуть наивную энергию для детских, семейных и прогулочных историй с ощущением игры.",
            tags=("childhood", "playful", "family", "light", "motion"),
        ),
        SoundtrackReference(
            title="Window",
            artist="The Album Leaf",
            note="подходит для мягкого домашнего тепла, спокойных семейных сцен и светлого медленного движения без перегруза.",
            tags=("gentle", "warm", "family", "outdoor", "light"),
        ),
        SoundtrackReference(
            title="La Femme d'Argent",
            artist="Air",
            note="работает для стильной, лёгкой и свободной взрослой поездки, где важны пространство, воздух и утончённая расслабленность.",
            tags=("leisure", "travel", "elegant", "cool", "relaxed"),
        ),
        SoundtrackReference(
            title="Two Thousand and Seventeen",
            artist="Four Tet",
            note="полезен для современного, собранного и ритмичного travel-монтажа с ненавязчивым движением и ощущением пути.",
            tags=("travel", "motion", "minimal", "city", "leisure"),
        ),
        SoundtrackReference(
            title="Porz Goret",
            artist="Yann Tiersen",
            note="подходит для поэтичного путешествия по ландшафтам и местам силы, когда маршрут чувствуется как личная история.",
            tags=("travel", "cultural", "scenic", "reflective", "flow"),
        ),
        SoundtrackReference(
            title="La Joya",
            artist="Aukai",
            note="работает для медитативной дороги, природных панорам и тихого культурного маршрута с акцентом на пространство.",
            tags=("travel", "cultural", "scenic", "gentle", "outdoor"),
        ),
        SoundtrackReference(
            title="Hoppípolla",
            artist="Sigur Rós",
            note="подходит для семейного восторга, ощущения большого воздуха и светлой эмоциональной волны в детских и праздничных сценах.",
            tags=("family", "childhood", "rise", "bright", "celebration"),
        ),
    ],
    "classical": [
        SoundtrackReference(
            title="Clair de Lune",
            artist="Claude Debussy",
            note="лучше всего подходит для мягкой мечтательной атмосферы, красоты кадра и светлой ностальгии.",
            tags=("dreamy", "night", "elegant", "reflective"),
        ),
        SoundtrackReference(
            title="Gymnopédie No. 1",
            artist="Erik Satie",
            note="даёт сдержанный, интимный, очень деликатный тон без избыточной драматизации.",
            tags=("gentle", "intimate", "minimal", "reflective"),
        ),
        SoundtrackReference(
            title="Pavane pour une infante défunte",
            artist="Maurice Ravel",
            note="подходит для тёплого, чуть винтажного, элегантного видеоряда с мягким дыханием монтажа.",
            tags=("warm", "elegant", "nostalgic", "tender"),
        ),
        SoundtrackReference(
            title="Air on the G String",
            artist="Johann Sebastian Bach",
            note="даёт ощущение цельности, благородной плавности и чистого, ровного эмоционального потока.",
            tags=("warm", "graceful", "uplift", "continuity"),
        ),
        SoundtrackReference(
            title="Nocturne in E-flat major, Op. 9 No. 2",
            artist="Frédéric Chopin",
            note="лучше раскрывает ночной, лиричный и слегка меланхоличный монтаж с акцентом на лицо и взгляд.",
            tags=("night", "intimate", "melancholic", "romantic"),
        ),
        SoundtrackReference(
            title="Spring, The Four Seasons, I. Allegro",
            artist="Antonio Vivaldi",
            note="полезна там, где нужен свет, движение, игровой жест и более открытая жизнерадостная энергия.",
            tags=("light", "motion", "bright", "family", "celebration", "childhood"),
        ),
        SoundtrackReference(
            title="Sicilienne",
            artist="Gabriel Fauré",
            note="даёт тёплую, плавную и изящную интонацию, хорошо подходящую для семейного портрета без тяжести.",
            tags=("elegant", "warm", "graceful", "family", "leisure"),
        ),
        SoundtrackReference(
            title="Nimrod",
            artist="Edward Elgar",
            note="лучше работает там, где нужна значимость семейной памяти, достоинство и эмоциональный объём нескольких поколений.",
            tags=("archive", "multi_generation", "rise", "reflective"),
        ),
        SoundtrackReference(
            title="Morning Mood",
            artist="Edvard Grieg",
            note="подходит для светлого, открытого и более воздушного семейного видеоряда с мягким утренним ощущением.",
            tags=("light", "family", "pastoral", "motion", "travel", "scenic", "outdoor"),
        ),
        SoundtrackReference(
            title="The Moldau",
            artist="Bedřich Smetana",
            note="подходит для маршрута через ландшафты, мосты, воду и ощущение непрерывного движения по местам.",
            tags=("travel", "cultural", "scenic", "flow"),
        ),
        SoundtrackReference(
            title="Waltz of the Flowers",
            artist="Pyotr Ilyich Tchaikovsky",
            note="лучше работает в праздничной, светлой и торжественной семейной сцене с групповой красотой кадра.",
            tags=("celebration", "bright", "group", "graceful"),
        ),
        SoundtrackReference(
            title="Spiegel im Spiegel",
            artist="Arvo Pärt",
            note="подходит для очень тихого и сосредоточенного ролика о памяти, времени и внутреннем состоянии без лишней декоративности.",
            tags=("archive", "reflective", "minimal", "intimate"),
        ),
        SoundtrackReference(
            title="Aquarium",
            artist="Camille Saint-Saëns",
            note="даёт ощущение детского чуда, мягкого света и сказочной прозрачности в семейной и праздничной детской истории.",
            tags=("childhood", "light", "dreamy", "family", "bright"),
        ),
        SoundtrackReference(
            title="Jesu, Joy of Man's Desiring",
            artist="Johann Sebastian Bach",
            note="работает для светлого домашнего тепла, семейной связи и мягкого возвышенного течения ролика.",
            tags=("family", "warm", "uplift", "graceful", "childhood"),
        ),
        SoundtrackReference(
            title="Arabesque No. 1",
            artist="Claude Debussy",
            note="подходит для изящной взрослой поездки и свободного движения между красивыми местами без тяжёлой драматургии.",
            tags=("travel", "leisure", "elegant", "flow", "light"),
        ),
        SoundtrackReference(
            title="The Lark Ascending",
            artist="Ralph Vaughan Williams",
            note="лучше ложится на простор, воздух, природный маршрут и ощущение внутренней свободы в путешествии.",
            tags=("travel", "scenic", "pastoral", "gentle", "outdoor"),
        ),
        SoundtrackReference(
            title="Scene by the Brook",
            artist="Ludwig van Beethoven",
            note="работает для спокойного природного движения, дневного воздуха и более плавной, созерцательной дороги.",
            tags=("travel", "scenic", "pastoral", "gentle", "leisure"),
        ),
        SoundtrackReference(
            title="In the Steppes of Central Asia",
            artist="Alexander Borodin",
            note="подходит для широкого маршрута через культурные и природные пространства, когда важны чувство пути и историческая глубина.",
            tags=("travel", "cultural", "scenic", "flow", "expansive"),
        ),
        SoundtrackReference(
            title="Flower Duet",
            artist="Léo Delibes",
            note="подходит для праздничной элегантности, семейной красоты кадра и светлого течения без тяжёлого пафоса.",
            tags=("family", "celebration", "graceful", "elegant", "warm"),
        ),
    ],
    "jazz": [
        SoundtrackReference(
            title="Peace Piece",
            artist="Bill Evans",
            note="подходит для очень мягкого, человечного и созерцательного монтажа с близкой эмоциональной дистанцией.",
            tags=("gentle", "intimate", "reflective", "warm"),
        ),
        SoundtrackReference(
            title="Blue in Green",
            artist="Miles Davis",
            note="лучше работает в более ночной, тонкой и внутренне напряжённой версии ролика.",
            tags=("night", "melancholic", "elegant", "reflective"),
        ),
        SoundtrackReference(
            title="In a Sentimental Mood",
            artist="Duke Ellington & John Coltrane",
            note="даёт тёплую, благородную лирику и хорошо поддерживает человеческое присутствие в кадре.",
            tags=("warm", "romantic", "elegant", "intimate", "leisure"),
        ),
        SoundtrackReference(
            title="Take Five",
            artist="The Dave Brubeck Quartet",
            note="подходит, если монтажу нужен более упругий ход, игривость и читаемый ритмический рисунок.",
            tags=("playful", "motion", "dynamic", "light", "travel", "city"),
        ),
        SoundtrackReference(
            title="Cantaloupe Island",
            artist="Herbie Hancock",
            note="работает на более энергичном, современно-пульсирующем видеоряде с акцентом на движение.",
            tags=("groove", "motion", "urban", "energetic"),
        ),
        SoundtrackReference(
            title="Cast Your Fate to the Wind",
            artist="Vince Guaraldi Trio",
            note="подходит для тёплой, семейной, чуть игровой атмосферы с лёгкой улыбкой и воздухом.",
            tags=("family", "light", "warm", "playful"),
        ),
        SoundtrackReference(
            title="Night Lights",
            artist="Gerry Mulligan",
            note="подходит для более тихой, ночной и ностальгической семейной хроники с мягким городским дыханием.",
            tags=("archive", "night", "reflective", "elegant"),
        ),
        SoundtrackReference(
            title="Django",
            artist="Modern Jazz Quartet",
            note="хорошо ложится на элегантную, архивную и немного винтажную линию с уважением к прошлому.",
            tags=("archive", "nostalgic", "elegant", "intimate"),
        ),
        SoundtrackReference(
            title="Hymn to Freedom",
            artist="Oscar Peterson Trio",
            note="подходит для объединяющего, светлого и человеческого ролика, где важны общность и внутренний подъём.",
            tags=("rise", "warm", "group", "uplift", "celebration"),
        ),
        SoundtrackReference(
            title="The Girl from Ipanema",
            artist="Stan Getz & João Gilberto",
            note="хорошо ложится на лёгкую поездку, солнечный воздух, прогулочные кадры и спокойный отпускной ритм.",
            tags=("travel", "leisure", "sun", "scenic"),
        ),
        SoundtrackReference(
            title="Poinciana",
            artist="Ahmad Jamal",
            note="подходит для взрослого, спокойного, немного элегантного отдыха с дорогой, вечером и пространством вокруг.",
            tags=("leisure", "night", "elegant", "travel"),
        ),
        SoundtrackReference(
            title="Naima",
            artist="John Coltrane",
            note="хорошо работает на очень личной, глубокой и мягко проживаемой линии памяти или внутреннего состояния героя.",
            tags=("archive", "intimate", "reflective", "warm"),
        ),
        SoundtrackReference(
            title="Linus and Lucy",
            artist="Vince Guaraldi Trio",
            note="даёт живую детскую энергию, домашнюю игру и лёгкость для ролика о ребёнке, семье и маленьких радостях.",
            tags=("childhood", "playful", "family", "light", "bright"),
        ),
        SoundtrackReference(
            title="Bossa Antigua",
            artist="Paul Desmond",
            note="подходит для расслабленного travel-ритма, дневного воздуха и взрослой прогулочной истории без лишней тяжести.",
            tags=("travel", "leisure", "light", "relaxed", "scenic"),
        ),
        SoundtrackReference(
            title="Wave",
            artist="Antonio Carlos Jobim",
            note="работает для взрослого морского или отпускного ролика, где важны пространство, свет и лёгкая текучесть.",
            tags=("travel", "leisure", "sun", "scenic", "elegant"),
        ),
        SoundtrackReference(
            title="Blue Rondo à la Turk",
            artist="The Dave Brubeck Quartet",
            note="полезен для более маршрутного и культурного путешествия, где монтаж держится на движении, смене мест и интеллектуальном пульсе.",
            tags=("travel", "cultural", "dynamic", "motion", "city"),
        ),
        SoundtrackReference(
            title="Song for My Father",
            artist="Horace Silver",
            note="подходит для живого культурного маршрута и тёплого движения между местами, когда нужен ритм без агрессии.",
            tags=("travel", "cultural", "warm", "motion", "family"),
        ),
        SoundtrackReference(
            title="Caravan",
            artist="Duke Ellington",
            note="работает для более характерного и насыщенного маршрута, где путешествие ощущается как смена сред и настроений.",
            tags=("travel", "cultural", "dynamic", "expansive", "dramatic"),
        ),
        SoundtrackReference(
            title="Splanky",
            artist="Count Basie",
            note="подходит для светлого семейного сбора, общего движения и праздничного, чуть игривого ансамблевого ощущения.",
            tags=("family", "celebration", "group", "playful", "bright"),
        ),
    ],
}

_SOUNDTRACK_MODE_RULES: dict[str, dict[str, set[str]]] = {
    "archive_family_memory": {
        "prefer": {"archive", "nostalgic", "reflective", "multi_generation", "intimate"},
        "avoid": {"travel", "scenic", "sun", "playful", "bright", "urban", "groove"},
    },
    "cultural_travel": {
        "prefer": {"travel", "cultural", "scenic", "flow", "motion"},
        "avoid": {"archive", "multi_generation", "childhood", "night", "melancholic"},
    },
    "adult_leisure_escape": {
        "prefer": {"leisure", "sun", "scenic", "travel", "elegant", "relaxed"},
        "avoid": {"archive", "multi_generation", "childhood", "playful", "group"},
    },
    "festive_childhood": {
        "prefer": {"childhood", "playful", "family", "light", "bright", "celebration"},
        "avoid": {"archive", "melancholic", "night", "urban", "minimal"},
    },
    "childhood_album": {
        "prefer": {"childhood", "warm", "family", "light", "playful", "nostalgic"},
        "avoid": {"urban", "night", "archive", "groove"},
    },
    "festive_family": {
        "prefer": {"family", "group", "celebration", "bright", "warm", "graceful"},
        "avoid": {"archive", "night", "melancholic", "urban"},
    },
    "adult_family_portrait": {
        "prefer": {"family", "group", "elegant", "graceful", "intimate", "warm"},
        "avoid": {"childhood", "playful", "travel", "urban", "groove"},
    },
    "family_outing": {
        "prefer": {"family", "motion", "light", "travel", "playful", "scenic"},
        "avoid": {"archive", "night", "urban", "melancholic"},
    },
    "adult_portrait": {
        "prefer": {"elegant", "intimate", "reflective", "graceful"},
        "avoid": {"playful", "childhood", "bright", "travel"},
    },
    "family_portrait": {
        "prefer": {"family", "warm", "intimate", "graceful"},
        "avoid": {"urban", "groove", "travel"},
    },
    "generic_human_story": {
        "prefer": {"warm", "intimate", "reflective"},
        "avoid": set(),
    },
}

_SOUNDTRACK_FAMILY_BY_STORY_MODE = {
    "archive_family_memory": "memory_archive",
    "cultural_travel": "cultural_travel",
    "adult_leisure_escape": "leisure_travel",
    "festive_childhood": "childhood_family",
    "childhood_album": "childhood_family",
    "festive_family": "family_celebration",
    "adult_family_portrait": "adult_family_portrait",
    "family_outing": "family_journey",
    "adult_portrait": "portrait_intimate",
    "family_portrait": "family_celebration",
    "generic_human_story": "portrait_intimate",
}

_SOUNDTRACK_HARD_POOLS: dict[str, dict[str, tuple[tuple[str, str], ...]]] = {
    "non_classical": {
        "memory_archive": (
            ("Ólafur Arnalds", "Saman"),
            ("A Winged Victory for the Sullen", "Steep Hills of Vicodin Tears"),
            ("Goldmund", "Threnody"),
            ("Jóhann Jóhannsson", "Flight from the City"),
            ("Hammock", "I Can Almost See You"),
        ),
        "cultural_travel": (
            ("Beirut", "Postcards From Italy"),
            ("The Cinematic Orchestra", "Arrival of the Birds"),
            ("Bonobo", "Cirrus"),
            ("Yann Tiersen", "Porz Goret"),
            ("Aukai", "La Joya"),
        ),
        "leisure_travel": (
            ("Khruangbin", "Friday Morning"),
            ("Tycho", "A Walk"),
            ("Air", "La Femme d'Argent"),
            ("Four Tet", "Two Thousand and Seventeen"),
            ("The Album Leaf", "Window"),
        ),
        "childhood_family": (
            ("Helios", "Bless This Morning Year"),
            ("Explosions in the Sky", "Your Hand in Mine"),
            ("Penguin Cafe Orchestra", "Music for a Found Harmonium"),
            ("Sigur Rós", "Hoppípolla"),
            ("The Album Leaf", "Window"),
        ),
        "family_celebration": (
            ("The Cinematic Orchestra", "Arrival of the Birds"),
            ("Helios", "Bless This Morning Year"),
            ("Tycho", "A Walk"),
            ("Sigur Rós", "Hoppípolla"),
            ("Beirut", "Postcards From Italy"),
        ),
        "adult_family_portrait": (
            ("The Cinematic Orchestra", "Arrival of the Birds"),
            ("Air", "La Femme d'Argent"),
            ("Helios", "Bless This Morning Year"),
            ("The Album Leaf", "Window"),
            ("Ólafur Arnalds", "Saman"),
        ),
        "family_journey": (
            ("Tycho", "A Walk"),
            ("Penguin Cafe Orchestra", "Music for a Found Harmonium"),
            ("Beirut", "Postcards From Italy"),
            ("Khruangbin", "Friday Morning"),
            ("The Album Leaf", "Window"),
        ),
        "portrait_intimate": (
            ("Ólafur Arnalds", "Saman"),
            ("Goldmund", "Threnody"),
            ("Jóhann Jóhannsson", "Flight from the City"),
            ("Hammock", "I Can Almost See You"),
            ("Helios", "Bless This Morning Year"),
        ),
    },
    "classical": {
        "memory_archive": (
            ("Arvo Pärt", "Spiegel im Spiegel"),
            ("Erik Satie", "Gymnopédie No. 1"),
            ("Edward Elgar", "Nimrod"),
            ("Maurice Ravel", "Pavane pour une infante défunte"),
            ("Frédéric Chopin", "Nocturne in E-flat major, Op. 9 No. 2"),
        ),
        "cultural_travel": (
            ("Bedřich Smetana", "The Moldau"),
            ("Alexander Borodin", "In the Steppes of Central Asia"),
            ("Edvard Grieg", "Morning Mood"),
            ("Claude Debussy", "Arabesque No. 1"),
            ("Ralph Vaughan Williams", "The Lark Ascending"),
        ),
        "leisure_travel": (
            ("Gabriel Fauré", "Sicilienne"),
            ("Claude Debussy", "Arabesque No. 1"),
            ("Johann Sebastian Bach", "Air on the G String"),
            ("Léo Delibes", "Flower Duet"),
            ("Ludwig van Beethoven", "Scene by the Brook"),
        ),
        "childhood_family": (
            ("Antonio Vivaldi", "Spring, The Four Seasons, I. Allegro"),
            ("Camille Saint-Saëns", "Aquarium"),
            ("Edvard Grieg", "Morning Mood"),
            ("Johann Sebastian Bach", "Jesu, Joy of Man's Desiring"),
            ("Pyotr Ilyich Tchaikovsky", "Waltz of the Flowers"),
        ),
        "family_celebration": (
            ("Pyotr Ilyich Tchaikovsky", "Waltz of the Flowers"),
            ("Johann Sebastian Bach", "Air on the G String"),
            ("Léo Delibes", "Flower Duet"),
            ("Antonio Vivaldi", "Spring, The Four Seasons, I. Allegro"),
            ("Gabriel Fauré", "Sicilienne"),
        ),
        "adult_family_portrait": (
            ("Léo Delibes", "Flower Duet"),
            ("Gabriel Fauré", "Sicilienne"),
            ("Johann Sebastian Bach", "Air on the G String"),
            ("Maurice Ravel", "Pavane pour une infante défunte"),
            ("Claude Debussy", "Arabesque No. 1"),
        ),
        "family_journey": (
            ("Edvard Grieg", "Morning Mood"),
            ("Antonio Vivaldi", "Spring, The Four Seasons, I. Allegro"),
            ("Gabriel Fauré", "Sicilienne"),
            ("Johann Sebastian Bach", "Air on the G String"),
            ("Ralph Vaughan Williams", "The Lark Ascending"),
        ),
        "portrait_intimate": (
            ("Erik Satie", "Gymnopédie No. 1"),
            ("Claude Debussy", "Clair de Lune"),
            ("Maurice Ravel", "Pavane pour une infante défunte"),
            ("Frédéric Chopin", "Nocturne in E-flat major, Op. 9 No. 2"),
            ("Arvo Pärt", "Spiegel im Spiegel"),
        ),
    },
    "jazz": {
        "memory_archive": (
            ("Bill Evans", "Peace Piece"),
            ("John Coltrane", "Naima"),
            ("Gerry Mulligan", "Night Lights"),
            ("Modern Jazz Quartet", "Django"),
            ("Miles Davis", "Blue in Green"),
        ),
        "cultural_travel": (
            ("The Dave Brubeck Quartet", "Blue Rondo à la Turk"),
            ("Horace Silver", "Song for My Father"),
            ("Duke Ellington", "Caravan"),
            ("The Dave Brubeck Quartet", "Take Five"),
            ("Herbie Hancock", "Cantaloupe Island"),
        ),
        "leisure_travel": (
            ("Stan Getz & João Gilberto", "The Girl from Ipanema"),
            ("Ahmad Jamal", "Poinciana"),
            ("Antonio Carlos Jobim", "Wave"),
            ("Paul Desmond", "Bossa Antigua"),
            ("Duke Ellington & John Coltrane", "In a Sentimental Mood"),
        ),
        "childhood_family": (
            ("Vince Guaraldi Trio", "Linus and Lucy"),
            ("Vince Guaraldi Trio", "Cast Your Fate to the Wind"),
            ("Oscar Peterson Trio", "Hymn to Freedom"),
            ("The Dave Brubeck Quartet", "Take Five"),
            ("Count Basie", "Splanky"),
        ),
        "family_celebration": (
            ("Oscar Peterson Trio", "Hymn to Freedom"),
            ("Duke Ellington & John Coltrane", "In a Sentimental Mood"),
            ("Count Basie", "Splanky"),
            ("Vince Guaraldi Trio", "Cast Your Fate to the Wind"),
            ("The Dave Brubeck Quartet", "Take Five"),
        ),
        "adult_family_portrait": (
            ("Duke Ellington & John Coltrane", "In a Sentimental Mood"),
            ("Bill Evans", "Peace Piece"),
            ("John Coltrane", "Naima"),
            ("Ahmad Jamal", "Poinciana"),
            ("Oscar Peterson Trio", "Hymn to Freedom"),
        ),
        "family_journey": (
            ("The Dave Brubeck Quartet", "Take Five"),
            ("Vince Guaraldi Trio", "Cast Your Fate to the Wind"),
            ("Paul Desmond", "Bossa Antigua"),
            ("Antonio Carlos Jobim", "Wave"),
            ("Oscar Peterson Trio", "Hymn to Freedom"),
        ),
        "portrait_intimate": (
            ("Bill Evans", "Peace Piece"),
            ("Miles Davis", "Blue in Green"),
            ("Duke Ellington & John Coltrane", "In a Sentimental Mood"),
            ("John Coltrane", "Naima"),
            ("Gerry Mulligan", "Night Lights"),
        ),
    },
}


def derive_structure_report_path(report_txt_path: Path) -> Path:
    return report_txt_path.with_name(f"{report_txt_path.stem}_structure.txt")


def write_sequence_structure_report(
    result: SequenceOptimizationResult,
    *,
    output_path: Path,
) -> Path:
    output_path.write_text(build_sequence_structure_report(result), encoding="utf-8")
    return output_path


def build_sequence_structure_report(result: SequenceOptimizationResult) -> str:
    section_entries = _group_entries_by_structure(result.entries)
    _profile_metrics, _profile_tags, story_mode = _build_profile_context(result.entries)
    lines = [
        "ОПТИМАЛЬНАЯ СТРУКТУРА ВИДЕОКЛИПА",
        "",
        f"Источник: {result.source_xml}",
        f"Последовательность: {result.selected_sequence_name}",
        f"Количество видео-клипов: {len(result.entries)}",
        "",
        "Задача отчёта",
        "",
        "Документ описывает будущую оптимальную структуру ролика на русском языке, чтобы затем вручную довести монтаж до финального качества.",
        "Порядок кадров уже опирается на текущую оптимизацию последовательности, continuity по лицу и одежде там, где признаки доступны, и на narrative progression по сценам.",
        "",
    ]

    lines.extend(_format_video_description_section(result.entries))
    lines.extend(_format_soundtrack_recommendations_section(result.entries))
    lines.extend(
        [
            "Каркас ролика",
            "",
        ]
    )

    for section in section_entries:
        lines.extend(_format_structure_section(section, result.entries, story_mode))

    lines.extend(_format_global_notes(result.entries))
    return "\n".join(lines).strip() + "\n"


def _group_entries_by_structure(
    entries: list[SequenceRecommendationEntry],
) -> list[tuple[StructureBeatSpec, list[SequenceRecommendationEntry], float, float]]:
    if not entries:
        return [(spec, [], 0.0, 0.0) for spec in _STRUCTURE_BEATS]

    durations = [max(1, int(entry.candidate.clip.duration or 1)) for entry in entries]
    total_duration = sum(durations)
    grouped: list[list[SequenceRecommendationEntry]] = [[] for _ in _STRUCTURE_BEATS]
    cursor = 0
    clip_ranges: list[tuple[float, float]] = []
    for duration in durations:
        start_ratio = cursor / total_duration
        cursor += duration
        end_ratio = cursor / total_duration
        clip_ranges.append((start_ratio, end_ratio))

    for entry, (start_ratio, end_ratio) in zip(entries, clip_ranges):
        midpoint = (start_ratio + end_ratio) / 2
        for section_index, spec in enumerate(_STRUCTURE_BEATS):
            if midpoint <= spec.end_ratio:
                grouped[section_index].append(entry)
                break

    sections: list[tuple[StructureBeatSpec, list[SequenceRecommendationEntry], float, float]] = []
    previous_end = 0.0
    for section_index, spec in enumerate(_STRUCTURE_BEATS):
        group = grouped[section_index]
        if group:
            first_index = entries.index(group[0])
            last_index = entries.index(group[-1])
            start_ratio = clip_ranges[first_index][0]
            end_ratio = clip_ranges[last_index][1]
        else:
            start_ratio = previous_end
            end_ratio = spec.end_ratio
        sections.append((spec, group, start_ratio, end_ratio))
        previous_end = end_ratio
    return sections


def _format_structure_section(
    section: tuple[StructureBeatSpec, list[SequenceRecommendationEntry], float, float],
    all_entries: list[SequenceRecommendationEntry],
    story_mode: str,
) -> list[str]:
    spec, entries, start_ratio, end_ratio = section
    lines = [
        f"{spec.index}. {spec.title}",
        "",
        f"Тайминг блока: {_format_ratio(start_ratio)} - {_format_ratio(end_ratio)} ролика",
        f"Функция блока: {spec.purpose}",
    ]

    if entries:
        lines.extend(
            [
                f"Группа кадров: {entries[0].recommended_index}-{entries[-1].recommended_index}",
                f"Ключевые клипы: {_format_clip_labels(entries)}",
                f"Визуальная задача: {_describe_visual_strategy(entries, spec, story_mode)}",
                f"Внутри блока: {_describe_internal_transition(entries, story_mode)}",
                f"Переход к следующему блоку: {_describe_exit_transition(entries, all_entries, story_mode)}",
                f"Цветовой акцент: {_describe_color_strategy(entries, spec, story_mode)}",
                f"Музыкальный акцент: {_describe_music_strategy(entries, spec, story_mode)}",
                f"Звуковое решение: {_describe_sound_strategy(entries, spec, story_mode)}",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "Группа кадров: держать блок очень коротким и ритмически опираться на соседние секции.",
                f"Визуальная задача: {spec.purpose}",
                f"Цветовой акцент: {spec.color}",
                f"Музыкальный акцент: {spec.music}",
                f"Звуковое решение: {spec.sound}",
                "",
            ]
        )
    return lines


def _format_global_notes(entries: list[SequenceRecommendationEntry]) -> list[str]:
    repeated_appearance = _series_token_counter(
        token
        for entry in entries
        for token in entry.candidate.series_appearance_tokens
    )
    repeated_subjects = _series_token_counter(
        token
        for entry in entries
        for token in entry.candidate.series_subject_tokens
    )
    main_character_notes = [
        note
        for entry in entries
        for note in entry.candidate.main_character_notes
        if note
    ]
    lines = [
        "Монтажные ориентиры",
        "",
        "- Вручную сохранять continuity по взгляду, направлению движения и масштабу плана внутри каждой соседней группы.",
        "- Серии по одному образу и одежде держать подряд только там, где это усиливает развитие, а не делает монтаж однообразным.",
        "- Кульминационные кадры лучше оставлять на самых сильных лицевых эмоциях или на наиболее читаемом жесте.",
    ]
    if repeated_appearance:
        lines.append(
            f"- Повторяющиеся appearance-маркеры: {', '.join(token for token, _count in repeated_appearance.most_common(4))}."
        )
    if repeated_subjects:
        lines.append(
            f"- Повторяющиеся subject-маркеры: {', '.join(token for token, _count in repeated_subjects.most_common(4))}."
        )
    if main_character_notes:
        localized_notes = [_localize_main_character_note(note) for note in dict.fromkeys(main_character_notes)]
        lines.append(f"- Возрастные и character-акценты: {', '.join(localized_notes)}.")
    lines.append("")
    return lines


def _format_video_description_section(entries: list[SequenceRecommendationEntry]) -> list[str]:
    if not entries:
        return [
            "Описание видеоролика",
            "",
            "Основная тема: для уверенного определения темы ролика не хватило видеоклипов в текущей последовательности.",
            "",
            "Краткое описание: для уверенного описания ролика не хватило видеоклипов в текущей последовательности.",
            "",
        ]

    profile_metrics, profile_tags, story_mode = _build_profile_context(entries)
    return [
        "Описание видеоролика",
        "",
        f"Основная тема: {_describe_main_theme(profile_tags, story_mode, profile_metrics)}",
        "",
        f"Краткое описание: {_describe_video_core(entries, profile_tags, story_mode, profile_metrics)}",
        f"Эмоциональный тон: {_describe_video_tone(profile_tags, story_mode, profile_metrics)}",
        f"Визуальная драматургия: {_describe_visual_dramaturgy(entries, profile_tags, story_mode)}",
        f"Монтажная логика: {_describe_montage_logic(entries, profile_tags, story_mode)}",
        "",
    ]


def _format_soundtrack_recommendations_section(entries: list[SequenceRecommendationEntry]) -> list[str]:
    _profile_metrics, profile_tags, story_mode = _build_profile_context(entries)
    lines = [
        "Рекомендуемая музыка",
        "",
        "Подборка ниже даётся как музыкальные референсы для ручного финального монтажа: по 5 вариантов в каждой категории под характер этого видеоряда.",
        "Музыкальный каталог жёстко разделён по типам ролика, поэтому архив, детство, путешествие, взрослый отдых и семейная сцена получают разные по природе саундтреки.",
        "",
    ]
    for category_key in ("non_classical", "classical", "jazz"):
        lines.append(_SOUNDTRACK_CATEGORY_TITLES[category_key])
        lines.append("")
        for option in _select_soundtrack_references(category_key, profile_tags, story_mode):
            lines.append(f"- {option.artist} — {option.title}: {option.note}")
        lines.append("")
    return lines


def _build_profile_context(
    entries: list[SequenceRecommendationEntry],
) -> tuple[dict[str, float | int | bool], set[str], str]:
    profile_metrics = _collect_profile_metrics(entries)
    profile_tags = _derive_music_profile_tags_from_metrics(profile_metrics, len(entries))
    story_mode = _derive_story_mode(profile_metrics, profile_tags)
    return profile_metrics, profile_tags, story_mode


def _format_ratio(value: float) -> str:
    percent = max(0, min(100, round(value * 100)))
    return f"{percent}%"


def _format_clip_labels(entries: list[SequenceRecommendationEntry]) -> str:
    return "; ".join(
        f"{entry.recommended_index}. {entry.candidate.clip.name}"
        for entry in entries[:4]
    )


def _describe_visual_strategy(
    entries: list[SequenceRecommendationEntry],
    spec: StructureBeatSpec,
    story_mode: str,
) -> str:
    average_shot_scale = sum(entry.candidate.shot_scale for entry in entries) / len(entries)
    repeated_appearance = _top_tokens(
        token
        for entry in entries
        for token in entry.candidate.series_appearance_tokens
    )
    if story_mode == "adult_family_portrait":
        if average_shot_scale <= 1:
            shot_note = (
                "держать взрослый групповой или средний портрет так, чтобы семейные фигуры, осанка "
                "и связи между родственниками читались сразу"
            )
        elif average_shot_scale <= 2:
            shot_note = (
                "сместить внимание на лицо одного из близких, взгляд, руки и осанку, не теряя рядом "
                "семью и межпоколенческую связь"
            )
        else:
            shot_note = (
                "использовать крупные портретные детали, улыбку, взгляд и руки как главный "
                "эмоциональный акцент без клиповой суеты"
            )
        if repeated_appearance:
            return (
                f"{shot_note}; внутри блока удерживать один зрелый семейный образ через "
                f"{', '.join(repeated_appearance)}."
            )
        return (
            f"{shot_note}; внутри блока собирать не событийность, а взрослую семейную близость "
            f"и достоинство общего портрета."
        )
    if average_shot_scale <= 1:
        shot_note = "держать читаемый общий или средний план, чтобы пространство работало на историю"
    elif average_shot_scale <= 2:
        shot_note = "сместить внимание на лицо, взгляд и жест, не теряя связи с окружением"
    else:
        shot_note = "использовать крупные детали как эмоциональный акцент и не дробить их лишними склейками"
    if repeated_appearance:
        return f"{shot_note}; внутри блока поддерживать continuity образа через {', '.join(repeated_appearance)}."
    return f"{shot_note}; {spec.purpose}"


def _describe_internal_transition(entries: list[SequenceRecommendationEntry], story_mode: str) -> str:
    if len(entries) < 2:
        if story_mode == "adult_family_portrait":
            return (
                "держать прямой cut или очень лёгкий dissolve, чтобы не разрушать достоинство "
                "взрослого семейного портрета и паузы между взглядами, без клиповой декоративности."
            )
        return "держать прямой cut или очень лёгкий переход, чтобы блок не растягивался искусственно."
    transition_counter = Counter()
    for previous_entry, current_entry in zip(entries, entries[1:]):
        transition_type, _reason = _select_recommended_transition_type(
            _to_transition_candidate(previous_entry.candidate),
            _to_transition_candidate(current_entry.candidate),
        )
        transition_counter[transition_type.display_name] += 1
    dominant_transition, _count = transition_counter.most_common(1)[0]
    transmission = _transition_transmission_hint(dominant_transition)
    if story_mode == "adult_family_portrait":
        if dominant_transition == "Morph Cut":
            return (
                f"опираться в основном на {dominant_transition}; использовать его как мягкую "
                f"портретную склейку между близкими лицами и родственными жестами; "
                f"трансмишн — {transmission}."
            )
        return (
            f"опираться в основном на {dominant_transition}; держать склейку сдержанной, "
            f"портретной и взрослой, без клиповой декоративности; трансмишн — {transmission}."
        )
    return f"опираться в основном на {dominant_transition}; трансмишн — {transmission}."


def _describe_exit_transition(
    entries: list[SequenceRecommendationEntry],
    all_entries: list[SequenceRecommendationEntry],
    story_mode: str,
) -> str:
    last_entry = entries[-1]
    last_index = all_entries.index(last_entry)
    if last_index >= len(all_entries) - 1:
        if story_mode == "adult_family_portrait":
            return (
                "последний кадр лучше оставить с чистым хвостом, без лишнего эффекта, чтобы "
                "закрепить взрослый семейный портрет и спокойное межпоколенческое послевкусие."
            )
        return "последний кадр лучше оставить с чистым хвостом, без лишнего эффекта, чтобы закрепить послевкусие."
    next_entry = all_entries[last_index + 1]
    transition_type, reason = _select_recommended_transition_type(
        _to_transition_candidate(last_entry.candidate),
        _to_transition_candidate(next_entry.candidate),
    )
    transmission = _transition_transmission_hint(transition_type.display_name)
    if story_mode == "adult_family_portrait":
        return (
            f"{transition_type.display_name}; {_localize_transition_reason(reason)}; держать переход "
            f"как спокойный переток между взрослыми семейными состояниями, а не как эффектный "
            f"монтажный трюк; трансмишн — {transmission}."
        )
    return f"{transition_type.display_name}; {_localize_transition_reason(reason)}; трансмишн — {transmission}."


def _describe_color_strategy(
    entries: list[SequenceRecommendationEntry],
    spec: StructureBeatSpec,
    story_mode: str,
) -> str:
    keywords = _merged_keywords(entries)
    if any(token in keywords for token in ("gold", "warm", "sun", "sunny", "тепл", "золот")):
        tone = "чуть подчеркнуть тёплые оттенки и мягкий свет"
    elif any(token in keywords for token in ("blue", "night", "cool", "cold", "син", "ноч", "холод")):
        tone = "сохранить более холодную гамму и контролируемый контраст"
    else:
        tone = "держать единую палитру и не ломать continuity по температуре"
    if story_mode == "adult_family_portrait":
        if any(token in keywords for token in ("gold", "warm", "sun", "sunny", "тепл", "золот")):
            tone = "бережно держать тёплую кожу, ткани и домашний свет, сохраняя благородную портретную палитру"
        elif any(token in keywords for token in ("blue", "night", "cool", "cold", "син", "ноч", "холод")):
            tone = "сохранять сдержанную холодноватую портретную гамму, не убивая живость кожи и фактуру одежды"
        else:
            tone = "держать благородную семейную палитру кожи, тканей и домашнего света, без кричащей яркости"
        return (
            f"{tone}; {spec.color} Дополнительно избегать детской пестроты и агрессивной сатурации, "
            f"чтобы кадр оставался взрослым семейным портретом."
        )
    return f"{tone}; {spec.color}"


def _describe_music_strategy(
    entries: list[SequenceRecommendationEntry],
    spec: StructureBeatSpec,
    story_mode: str,
) -> str:
    average_energy = sum(entry.candidate.energy_level for entry in entries) / len(entries)
    if average_energy >= 2.2:
        energy_note = "можно смелее поднимать ритм и плотность"
    elif average_energy <= 0.8:
        energy_note = "лучше оставить больше воздуха и не перегружать ритм"
    else:
        energy_note = "ритм стоит наращивать умеренно, без агрессивного форсажа"
    if story_mode == "adult_family_portrait":
        adult_family_music_notes = {
            "intro": (
                "Вести музыку как зрелый семейный портрет: мягкое фортепиано, деликатные струнные "
                "или благородный джазовый штрих, без детской игривости."
            ),
            "hook": (
                "Первый музыкальный акцент лучше делать элегантным и человеческим, подчёркивая взгляд, "
                "жест и взрослую близость, а не резкий beat."
            ),
            "development": (
                "Развитие вести через плавный эмоциональный рост и портретную теплоту, не уходя "
                "в бодрую детскую или travel-пружину."
            ),
            "peak": (
                "Пик делать не шумным, а благородно-эмоциональным: раскрытие семейной близости, "
                "лиц близких и межпоколенческой сцены."
            ),
            "calm": (
                "После пика возвращать музыку в тёплое взрослое состояние, оставляя благородный "
                "хвост мелодии и ощущение семейного воздуха."
            ),
            "aftertaste": (
                "Финальный музыкальный штрих лучше оставить камерным, тёплым и портретным, чтобы "
                "закрепить взрослую близость между родными."
            ),
        }
        return f"{adult_family_music_notes.get(spec.key, spec.music)} {energy_note}."
    return f"{spec.music} {energy_note}."


def _describe_sound_strategy(
    entries: list[SequenceRecommendationEntry],
    spec: StructureBeatSpec,
    story_mode: str,
) -> str:
    face_continuity_present = any(entry.candidate.series_subject_tokens for entry in entries)
    if story_mode == "adult_family_portrait":
        adult_family_sound_notes = {
            "intro": (
                "Сохранять мягкий room tone, дыхание комнаты, ткань и лёгкий бытовой воздух без "
                "клиповых шумовых эффектов."
            ),
            "hook": (
                "Акцентировать не эффект, а человеческое присутствие: лёгкий вдох, смешок, движение "
                "ткани или тихий жест внутри комнаты."
            ),
            "development": (
                "Держать звук живым, но сдержанным: шаг, ткань, смех, посуда и мягкий воздух семьи "
                "вместо агрессивных whoosh."
            ),
            "peak": (
                "В пике лучше усилить эмоциональное присутствие людей, а не SFX: голосовой воздух, "
                "смех, общий вздох, тихий телесный жест."
            ),
            "calm": (
                "После пика вернуть мягкий домашний воздух, хвост комнаты и спокойное присутствие "
                "людей без декоративного шума."
            ),
            "aftertaste": (
                "Оставить деликатный хвост комнаты и человеческого присутствия, чтобы финал ощущался "
                "как взрослая семейная память, а не эффект."
            ),
        }
        if face_continuity_present:
            continuity_note = (
                "Избегать резких SFX поверх лиц, волос, украшений и ткани; лучше слышать комнату, "
                "дыхание и живое семейное присутствие."
            )
        else:
            continuity_note = (
                "Даже при более редких лицевых continuity-линях держать звук камерным и человеческим, "
                "без клиповой ударности."
            )
        return f"{adult_family_sound_notes.get(spec.key, spec.sound)} {continuity_note}"
    if face_continuity_present:
        continuity_note = "Избегать резких SFX поверх continuity-линий лица и одежды."
    else:
        continuity_note = "Допустимы более заметные текстурные переходы, если они не спорят с музыкой."
    return f"{spec.sound} {continuity_note}"


def _to_transition_candidate(candidate: SequenceCandidate) -> SimpleNamespace:
    scene_analysis = candidate.assets.scene_analysis or {}
    return SimpleNamespace(
        series_subject_tokens=list(candidate.series_subject_tokens),
        series_appearance_tokens=list(candidate.series_appearance_tokens),
        keywords=list(candidate.keywords),
        shot_scale=int(candidate.shot_scale),
        people_count=int(candidate.people_count),
        energy_level=int(candidate.energy_level),
        summary=str(scene_analysis.get("summary") or ""),
        background=str(scene_analysis.get("background") or ""),
        shot_type_text=str(scene_analysis.get("shot_type") or ""),
        main_action=str(scene_analysis.get("main_action") or ""),
        mood=[str(item) for item in (scene_analysis.get("mood") or []) if item],
        relationships=[str(item) for item in (scene_analysis.get("relationships") or []) if item],
        prompt_text=str(candidate.assets.prompt_text or ""),
    )


def _transition_transmission_hint(transition_name: str) -> str:
    if transition_name == "Morph Cut":
        return "невидимая лицевая склейка с упором на continuity позы и взгляда"
    if transition_name == "Film Dissolve":
        return "мягкий световой переток с лёгким glow и более длинным хвостом"
    if transition_name == "Dip to Black":
        return "короткий затемняющий провал или приглушение света перед новым блоком"
    return "нейтральный мягкий переток по свету без лишней декоративности"


def _localize_transition_reason(reason: str) -> str:
    mapping = {
        "rule: same-person or same-look continuity with similar framing suggests a face-preserving smoothing transition":
            "правило: continuity одного и того же человека или образа при близком кадрировании лучше вести через мягкую face-preserving склейку",
        "rule: strong scene or tone break suggests a reset transition instead of a neutral blend":
            "правило: сильный перелом сцены или тона лучше подчеркнуть reset-переходом, а не нейтральным перетоком",
        "rule: dreamy, nostalgic, beauty, or soft-emotional language suggests a softer cinematic dissolve":
            "правило: dreamy, nostalgic и мягко-эмоциональный тон лучше поддерживать более мягким cinematic dissolve",
        "rule: related shots with readable continuity fit a neutral dissolve best":
            "правило: связанные кадры с читаемой continuity лучше всего держать на нейтральном dissolve",
        "rule: default to the safest neutral transition when no stronger style signal is present":
            "правило: если сильного стилевого сигнала нет, безопаснее оставить нейтральный переход",
    }
    return mapping.get(reason, reason)


def _localize_main_character_note(note: str) -> str:
    mapping = {
        "keeps the youngest child near the front of the story": "удерживает самого младшего ребёнка ближе к началу истории",
        "features a young child who can anchor the story": "использует маленького ребёнка как устойчивую эмоциональную опору истории",
        "gives priority to the youngest visible character": "даёт приоритет самому младшему заметному персонажу",
    }
    return mapping.get(note, note)


def _merged_keywords(entries: list[SequenceRecommendationEntry]) -> set[str]:
    return {
        keyword
        for entry in entries
        for keyword in entry.candidate.keywords
    }


def _normalize_series_token(token: str | None) -> str | None:
    if not token:
        return None
    normalized = str(token).strip().lower()
    if not normalized or normalized in _GENERIC_SERIES_TOKENS:
        return None
    if normalized.isdigit() or len(normalized) <= 2:
        return None
    return normalized


def _series_token_counter(tokens: object) -> Counter[str]:
    counter: Counter[str] = Counter()
    for token in tokens:
        normalized = _normalize_series_token(token)
        if normalized:
            counter[normalized] += 1
    return counter


def _top_tokens(tokens: object, *, limit: int = 3) -> list[str]:
    counter = _series_token_counter(tokens)
    return [token for token, _count in counter.most_common(limit)]


def _describe_companion_presence(profile_tags: set[str]) -> str:
    if {"dogs", "cats"} <= profile_tags:
        return "домашние собаки и кошки"
    if "dogs" in profile_tags:
        return "домашние собаки"
    if "cats" in profile_tags:
        return "домашние кошки"
    if "pets" in profile_tags:
        return "домашние животные"
    return ""


def _describe_companion_presence_genitive(profile_tags: set[str]) -> str:
    if {"dogs", "cats"} <= profile_tags:
        return "домашних собак и кошек"
    if "dogs" in profile_tags:
        return "домашних собак"
    if "cats" in profile_tags:
        return "домашних кошек"
    if "pets" in profile_tags:
        return "домашних животных"
    return ""


def _resolve_story_specific_motif(profile_tags: set[str], profile_metrics: dict[str, float | int | bool]) -> str:
    entry_count = max(1, int(profile_metrics.get("entry_count") or 0))
    primary_threshold = max(3, int(round(entry_count * 0.35)))
    if "wedding" in profile_tags:
        if int(profile_metrics.get("wedding_hits") or 0) >= primary_threshold:
            return "wedding_primary"
        if int(profile_metrics.get("child_hits") or 0) >= 2:
            return "wedding_age_arc"
        return "wedding_accent"
    if "fishing" in profile_tags:
        if int(profile_metrics.get("fishing_hits") or 0) >= primary_threshold:
            return "fishing_primary"
        return "fishing_accent"
    return ""


def _describe_story_specific_theme(
    profile_tags: set[str],
    story_mode: str,
    profile_metrics: dict[str, float | int | bool],
) -> str:
    motif = _resolve_story_specific_motif(profile_tags, profile_metrics)
    if motif == "wedding_primary":
        return "свадебный день, жених и невеста, романтическая близость пары и ощущение важного общего события"
    if motif == "wedding_age_arc":
        return "взросление героя от детства к взрослой жизни, семейные связи и финальный свадебный акцент"
    if motif == "wedding_accent":
        return "взрослая семейная история, близкие связи и важное общее событие, внутри которых заметно читается свадебная линия"
    if motif == "fishing_primary":
        return "рыбалка, рыбак, пойманная рыба и спокойное сосредоточенное присутствие героя на природе"
    if motif == "fishing_accent":
        if "travel" in profile_tags or story_mode in {"cultural_travel", "adult_leisure_escape", "family_outing"}:
            return "поездка, встречи, отдых и жизненные эпизоды героя, среди которых появляется рыбацкая линия"
        if "group_family" in profile_tags:
            return "семейные встречи, взрослые портреты и разные эпизоды героя, среди которых появляется рыбацкая линия"
        return "взрослая история героя, его среда и занятия, среди которых появляется рыбацкая линия"
    return ""


def _describe_story_specific_subject_line(
    profile_tags: set[str],
    story_mode: str,
    profile_metrics: dict[str, float | int | bool],
) -> str:
    motif = _resolve_story_specific_motif(profile_tags, profile_metrics)
    if motif == "wedding_primary":
        return "Ролик воспринимается как свадебная история, где в центре стоят жених, невеста, их близость и ощущение важного дня."
    if motif == "wedding_age_arc":
        return "Ролик воспринимается как история взросления героя от детства к взрослой жизни, где семейная линия приходит к свадебному событию."
    if motif == "wedding_accent":
        return "Ролик воспринимается как взрослая семейная история, внутри которой заметно читается свадебная линия героя."
    if motif == "fishing_primary":
        return "Ролик воспринимается как история рыбалки, где в центре стоят рыбак, пойманная рыба и спокойное присутствие героя на природе."
    if motif == "fishing_accent":
        if "travel" in profile_tags or story_mode in {"cultural_travel", "adult_leisure_escape", "family_outing"}:
            return "Ролик воспринимается как история поездки, встреч и разных жизненных эпизодов героя, где среди отдельных блоков появляется линия рыбалки, рыбака и пойманной рыбы."
        if "group_family" in profile_tags:
            return "Ролик воспринимается как история взрослых встреч и разных жизненных эпизодов героя, где среди отдельных блоков появляется линия рыбалки, рыбака и пойманной рыбы."
        return "Ролик воспринимается как взрослая история героя, его среды и занятий, где среди отдельных эпизодов появляется линия рыбалки, рыбака и пойманной рыбы."
    return ""


def _describe_video_core(
    entries: list[SequenceRecommendationEntry],
    profile_tags: set[str],
    story_mode: str,
    profile_metrics: dict[str, float | int | bool],
) -> str:
    repeated_subjects = _top_tokens(
        token
        for entry in entries
        for token in entry.candidate.series_subject_tokens
    )
    repeated_appearance = _top_tokens(
        token
        for entry in entries
        for token in entry.candidate.series_appearance_tokens
    )
    child_present = "childhood" in profile_tags or any(
        "youngest child" in note
        for entry in entries
        for note in entry.candidate.main_character_notes
    )
    special_subject_line = _describe_story_specific_subject_line(profile_tags, story_mode, profile_metrics)
    if special_subject_line:
        subject_line = special_subject_line
    elif story_mode == "archive_family_memory":
        subject_line = "Ролик воспринимается как семейная фото-хроника памяти, где детство, взрослые портреты и старшие поколения собираются в одну линию времени."
    elif story_mode == "cultural_travel":
        subject_line = "Ролик воспринимается как маршрутное путешествие по местам, где важны дорога, культурные точки, архитектура и впечатления от локаций."
    elif story_mode == "adult_leisure_escape":
        subject_line = "Ролик воспринимается как спокойная взрослая поездка-отдых, собранная из воздуха, пространства, моря, прогулок и неторопливых встреч."
    elif story_mode == "festive_childhood":
        subject_line = "Ролик воспринимается как тёплая детская семейная история с праздниками, играми и небольшими событиями взросления."
    elif story_mode == "childhood_album":
        subject_line = "Ролик воспринимается как история детства и взросления, где эмоциональная линия держится на ребёнке, домашних сценах и близости семьи."
    elif story_mode == "festive_family":
        subject_line = "Ролик воспринимается как праздничный семейный портрет, где важны общая встреча, группы людей, домашнее тепло и ощущение события."
    elif story_mode == "adult_family_portrait":
        subject_line = "Ролик воспринимается как взрослый семейный портрет, где в центре оказываются близкие люди разных поколений, общая встреча и зрелая эмоциональная связь внутри семьи."
    elif story_mode == "family_outing":
        subject_line = "Ролик воспринимается как семейная хроника прогулок и совместных выходов, где движение по местам важнее одной статичной сцены."
    elif story_mode == "adult_portrait":
        subject_line = "Ролик воспринимается как взрослый портретный рассказ, где характер героя и его внутренняя интонация важнее событийной суеты."
    elif story_mode == "family_portrait":
        subject_line = "Ролик воспринимается как семейная хроника связей между близкими, собранная через общие портреты, взгляды и сцены взаимодействия."
    elif "archive" in profile_tags and "multi_generation" in profile_tags:
        subject_line = "Ролик воспринимается как семейная фото-хроника памяти, которая соединяет разные поколения и временные слои."
    elif "cultural_travel" in profile_tags:
        subject_line = "Ролик воспринимается как путешествие по культурным и природным местам, где важны маршрут, открытия и атмосфера поездки."
    elif "leisure_travel" in profile_tags:
        subject_line = "Ролик воспринимается как спокойная взрослая поездка-отдых, собранная из морских видов, прогулок, ресторанов и неторопливых встреч."
    elif "family_trip" in profile_tags:
        subject_line = "Ролик воспринимается как семейная поездка и серия прогулок, где важны смена мест, дорожные впечатления и живые остановки по пути."
    elif "group_family" in profile_tags and "holiday" in profile_tags:
        subject_line = "Ролик воспринимается как праздничный семейный портрет нескольких поколений, где важны близость, общие сцены и атмосфера встречи."
    elif child_present and "playful" in profile_tags:
        subject_line = "Ролик воспринимается как история детства, взросления и маленьких семейных событий, собранных в живую эмоциональную линию."
    elif "family_outing" in profile_tags:
        subject_line = "Ролик воспринимается как семейная хроника прогулок, встреч и небольших совместных событий вне дома."
    elif "group_family" in profile_tags:
        subject_line = "Ролик воспринимается как семейная хроника связей между близкими, снятая через общие портреты и сцены взаимодействия."
    elif "adult_portrait" in profile_tags:
        subject_line = "Ролик воспринимается как взрослый портретный рассказ, где важны характер, лицо и личная линия героя."
    elif child_present:
        subject_line = "Ролик воспринимается как история ребёнка и его эмоционального мира внутри домашнего пространства."
    else:
        subject_line = "Ролик воспринимается как история одного героя, его взгляда и внутреннего состояния."

    detail_bits: list[str] = []
    if repeated_subjects:
        detail_bits.append(f"Центральные повторяющиеся subject-маркеры: {', '.join(repeated_subjects)}")
    if repeated_appearance:
        detail_bits.append(f"Повторяющиеся appearance-маркеры: {', '.join(repeated_appearance)}")
    motif = _describe_scene_motif(profile_tags, story_mode, profile_metrics)
    if motif:
        detail_bits.append(f"Опорный мотив видеоряда: {motif}")
    companion_presence = _describe_companion_presence(profile_tags)
    if companion_presence:
        detail_bits.append(f"Повторяющийся живой мотив: {companion_presence}")
    if detail_bits:
        return f"{subject_line} {'; '.join(detail_bits)}."
    return subject_line


def _describe_video_tone(
    profile_tags: set[str],
    story_mode: str,
    profile_metrics: dict[str, float | int | bool],
) -> str:
    motif = _resolve_story_specific_motif(profile_tags, profile_metrics)
    if motif == "wedding_primary":
        return "романтический, праздничный, тёплый, с ощущением важного дня, близости пары и красивого общего события."
    if motif == "fishing_primary":
        return "спокойный, природный, сосредоточенный, с ощущением тихой удачи, воздуха и уважения к моменту улова."
    if story_mode == "archive_family_memory":
        return "архивный, ностальгический, тёплый, с чувством семейной памяти, глубины времени и бережного отношения к прошлому."
    if story_mode == "cultural_travel":
        return "светлый, дорожный, наблюдательный, с интересом к местам, архитектуре и спокойному раскрытию маршрута."
    if story_mode == "adult_leisure_escape":
        return "спокойный, зрелый, отпускной, с ощущением простора, мягкого ветра, воды и неторопливого отдыха."
    if story_mode == "festive_childhood":
        return "тёплый, детский, праздничный, домашний, с живой эмоциональной энергией и мягкой радостью."
    if story_mode == "childhood_album":
        return "светлый, человечный, детский, местами игровой, но прежде всего семейный и эмоционально близкий."
    if story_mode == "festive_family":
        return "праздничный, объединяющий, семейный, с ощущением общей встречи и красивой групповой сцены."
    if story_mode == "adult_family_portrait":
        return "зрелый, тёплый, семейный, более элегантный и портретный, чем игровой, с ощущением взрослой встречи и связи поколений."
    if story_mode == "family_outing":
        return "живой, лёгкий, прогулочный, семейный, с ощущением движения, воздуха и совместного времени вне дома."
    if story_mode == "adult_portrait":
        return "собранный, спокойный, взрослый, чуть более элегантный и внутренне сосредоточенный."
    if story_mode == "family_portrait":
        return "тёплый, семейный, мягкий, ориентированный на близость и человеческое присутствие в кадре."
    if "archive" in profile_tags and "multi_generation" in profile_tags:
        return "архивный, ностальгический, тёплый, с чувством семейной памяти и мягкого уважения к времени."
    if "cultural_travel" in profile_tags:
        return "светлый, дорожный, созерцательный, с интересом к новым местам, культуре и семейным впечатлениям от поездки."
    if "leisure_travel" in profile_tags:
        return "спокойный, зрелый, отпускной, с ощущением простора, моря, воздуха и мягкого внутреннего отдыха."
    if "family_trip" in profile_tags:
        return "живой, дорожный, семейный, с ощущением прогулки, маршрута и общей радости от движения вместе."
    if "group_family" in profile_tags and "holiday" in profile_tags:
        return "праздничный, домашний, объединяющий, со светлой семейной энергией и ощущением общей встречи."
    if "childhood" in profile_tags and "playful" in profile_tags:
        return "живой, светлый, детский, местами игровой и очень человечный."
    if "adult_portrait" in profile_tags and "elegant" in profile_tags:
        return "спокойный, взрослый, портретный, с более собранной и элегантной эмоцией."
    if {"dreamy", "elegant"} & profile_tags:
        return "мечтательный, деликатный, визуально красивый и ориентированный на плавное послевкусие."
    if {"night", "reflective"} & profile_tags:
        return "созерцательный, интимный, местами меланхоличный, с внутренней тишиной между акцентами."
    if {"energetic", "motion"} & profile_tags:
        return "живой, более ритмичный, с ощущением движения и монтажного импульса."
    return "мягкий, наблюдательный, человечный и ориентированный на цельный эмоциональный поток."


def _describe_main_theme(
    profile_tags: set[str],
    story_mode: str,
    profile_metrics: dict[str, float | int | bool],
) -> str:
    special_base = _describe_story_specific_theme(profile_tags, story_mode, profile_metrics)
    if special_base:
        base = special_base
    elif story_mode == "archive_family_memory":
        base = "семейная память, связь поколений и бережное проживание времени через архивные образы"
    elif story_mode == "cultural_travel":
        base = "путешествие по культурным и природным местам, где маршрут раскрывает людей и пространство"
    elif story_mode == "adult_leisure_escape":
        base = "взрослый отдых, море, воздух и спокойное совместное время без суеты"
    elif story_mode == "festive_childhood":
        base = "детство, семейные праздники, игры и маленькие шаги взросления"
    elif story_mode == "childhood_album":
        base = "детство, домашние сцены и эмоциональная линия роста ребёнка"
    elif story_mode == "festive_family":
        base = "семейная встреча, общий праздник и ощущение объединяющего события"
    elif story_mode == "adult_family_portrait":
        base = "взрослая семейная встреча, портреты близких разных поколений и ощущение зрелой близости между родными"
    elif story_mode == "family_outing":
        base = "совместные прогулки, выходы и движение семьи через разные места и моменты"
    elif story_mode == "adult_portrait":
        base = "характер взрослого героя, его внутреннее состояние и портретное присутствие в кадре"
    elif story_mode == "family_portrait":
        base = "близость, семейные связи и взаимодействие между родными людьми"
    elif "travel" in profile_tags:
        base = "дорога, смена мест и эмоциональное движение через пространство"
    elif "group_family" in profile_tags:
        base = "семья, общая сцена и связи между близкими"
    elif "childhood" in profile_tags:
        base = "детство и эмоциональное развитие ребёнка внутри семейной среды"
    else:
        base = "эмоциональная линия героя и её раскрытие через последовательность образов"

    companion_presence = _describe_companion_presence_genitive(profile_tags)
    if not companion_presence:
        return f"{base}."
    if story_mode in {
        "archive_family_memory",
        "festive_childhood",
        "childhood_album",
        "festive_family",
        "adult_family_portrait",
        "family_portrait",
    }:
        return f"{base} и повторяющееся присутствие {companion_presence}."
    if story_mode in {"cultural_travel", "adult_leisure_escape", "family_outing"}:
        return f"{base}, где заметную роль играют {companion_presence}."
    return f"{base} с повторяющимся присутствием {companion_presence}."


def _describe_visual_dramaturgy(
    entries: list[SequenceRecommendationEntry],
    profile_tags: set[str],
    story_mode: str,
) -> str:
    average_shot_scale = sum(entry.candidate.shot_scale for entry in entries) / len(entries)
    average_energy = sum(entry.candidate.energy_level for entry in entries) / len(entries)
    if story_mode == "archive_family_memory":
        framing = "Драматургия лучше работает как бережная анимация архивных кадров и семейных портретов: мягкие приближения, раскрытие деталей и аккуратное движение через время."
    elif story_mode == "cultural_travel":
        framing = "Драматургия лучше строится на маршруте через локации: сначала пространство и место, затем человек внутри среды и короткие акценты на детали путешествия."
    elif story_mode == "adult_leisure_escape":
        framing = "Драматургия лучше строится на чередовании морских и прогулочных общих планов с более близкими спокойными портретами отдыхающих."
    elif story_mode == "festive_childhood":
        framing = "Драматургия лучше строится на детских жестах, играх, домашних сценах и праздничных микро-событиях, где важны реакция лица и тёплая среда."
    elif story_mode == "childhood_album":
        framing = "Драматургия строится на чередовании детских портретов, домашних сцен и маленьких эмоциональных жестов взросления."
    elif story_mode == "festive_family":
        framing = "Драматургия строится на групповых композициях, праздничных столах, общих портретах и считывании связей между людьми внутри одной сцены."
    elif story_mode == "adult_family_portrait":
        framing = "Драматургия строится на взрослых семейных портретах, лицах близких, более тесных межпоколенческих связках и элегантных групповых композициях без детской игривости."
    elif story_mode == "family_outing":
        framing = "Драматургия строится на смене точек прогулки, выходов и небольших остановок, где место и люди должны читаться одновременно."
    elif story_mode == "adult_portrait":
        framing = "Драматургия держится на лице, жесте, одежде и пластике взрослого героя, без перегруза лишней событийностью."
    elif "archive" in profile_tags:
        framing = "Драматургия лучше работает как бережная анимация архивных кадров и портретов: мягкие приближения, панорамы и аккуратное раскрытие деталей."
    elif "cultural_travel" in profile_tags:
        framing = "Драматургия лучше строится на маршруте через локации: общие виды места, затем средние планы человека в среде и короткие акценты на детали путешествия."
    elif "leisure_travel" in profile_tags:
        framing = "Драматургия лучше строится на чередовании морских и прогулочных общих планов с более близкими спокойными портретами отдыхающих."
    elif "family_trip" in profile_tags:
        framing = "Драматургия строится на смене точек маршрута, дорожных остановках и групповых кадрах, где место и люди должны читаться одновременно."
    elif "group_family" in profile_tags and average_shot_scale <= 1.4:
        framing = "Драматургия строится на групповых композициях и считывании связей между людьми, поэтому важны общие и средние планы с ясным распределением фигур."
    elif "childhood" in profile_tags:
        framing = "Драматургия строится на чередовании детских портретов, домашних сцен, игр и коротких эмоциональных жестов."
    elif average_shot_scale >= 2.0:
        framing = "Драматургия держится на более близких планах, лице, жесте и считываемой мимике."
    elif average_shot_scale >= 1.2:
        framing = "Драматургия строится на чередовании средних и более близких планов, чтобы сохранять и человека, и среду."
    else:
        framing = "Драматургия строится на более читаемом пространстве, общих и средних планах с ясной сценой."
    if average_energy >= 2.0:
        rhythm = "Внутри ролика можно позволить более заметное нарастание ритма к пику."
    elif "reflective" in profile_tags:
        rhythm = "Ритм лучше держать мягким, с паузами и воздухом между сильными моментами."
    else:
        rhythm = "Ритм лучше поднимать постепенно, без резких монтажных скачков."
    return f"{framing} {rhythm}"


def _describe_montage_logic(
    entries: list[SequenceRecommendationEntry],
    profile_tags: set[str],
    story_mode: str,
) -> str:
    repeated_subjects = _top_tokens(
        token
        for entry in entries
        for token in entry.candidate.series_subject_tokens
    )
    if story_mode == "archive_family_memory":
        continuity = "Основная линия монтажа держится на переходе между поколениями, возрастами и временными слоями семейной памяти."
    elif story_mode == "cultural_travel":
        continuity = "Основная линия монтажа держится на маршруте через места, поэтому важно чередовать establishing-виды локаций, человека в среде и детали путешествия."
    elif story_mode == "adult_leisure_escape":
        continuity = "Основная линия монтажа держится на состоянии отдыха: виды пространства, столы, террасы, вода и спокойные портреты должны перетекать мягко и без суеты."
    elif story_mode == "festive_childhood":
        continuity = "Основная линия монтажа держится на детских реакциях, играх и праздничных домашних микро-событиях, которые лучше собирать в тёплую эмоциональную дугу."
    elif story_mode == "childhood_album":
        continuity = "Основная линия монтажа держится на этапах детства, смене состояний ребёнка и домашних микрособытиях взросления."
    elif story_mode == "festive_family":
        continuity = "Основная линия монтажа держится на чередовании общих праздничных портретов и более близких семейных связок внутри группы."
    elif story_mode == "adult_family_portrait":
        continuity = "Основная линия монтажа держится на взрослых семейных портретах, образах близких и переходах между поколениями внутри одной встречи, поэтому важны лицо, осанка, жест и уважение к паузам."
    elif story_mode == "family_outing":
        continuity = "Основная линия монтажа держится на прогулках, выходах и перемещении по точкам маршрута, поэтому важно не терять ощущение совместного пути."
    elif story_mode == "adult_portrait":
        continuity = "Основная линия монтажа держится на характере взрослого героя, его взгляде, жесте и пластике, а не на большом количестве монтажных событий."
    elif "archive" in profile_tags and "multi_generation" in profile_tags:
        continuity = "Основная линия монтажа держится на переходе между поколениями, возрастами и временными слоями семейной памяти."
    elif "cultural_travel" in profile_tags:
        continuity = "Основная линия монтажа держится на маршруте через разные места, поэтому важно чередовать establishing-виды локаций, кадры человека в среде и детали путешествия."
    elif "leisure_travel" in profile_tags:
        continuity = "Основная линия монтажа держится на состоянии отдыха: виды пространства, столы, террасы, морские планы и спокойные портреты должны перетекать мягко и без суеты."
    elif "family_trip" in profile_tags or "family_outing" in profile_tags:
        continuity = "Основная линия монтажа держится на прогулках и семейных перемещениях, поэтому важно собирать кадры по точкам маршрута и не терять ощущение дороги."
    elif "group_family" in profile_tags and "holiday" in profile_tags:
        continuity = "Основная линия монтажа держится на чередовании общих праздничных портретов и более близких семейных связок внутри группы."
    elif "childhood" in profile_tags:
        continuity = "Основная линия монтажа держится на этапах детства, смене состояний ребёнка и домашних микрособытиях."
    elif repeated_subjects:
        continuity = f"Основная линия монтажа держится на continuity по образу и повторяемым subject-маркерам: {', '.join(repeated_subjects)}."
    else:
        continuity = "Основная линия монтажа держится на смысловом нарастании, смене состояний и читаемом переходе между блоками."
    if "motion" in profile_tags and "energetic" in profile_tags:
        ending = "При этом важно не потерять ясность крючка и оставить финал с чистым эмоциональным хвостом."
    else:
        ending = "При этом кульминацию лучше оставлять на самый эмоционально читаемый жест, а финал делать мягче и чище."
    return f"{continuity} {ending}"


def _derive_music_profile_tags(entries: list[SequenceRecommendationEntry]) -> set[str]:
    metrics = _collect_profile_metrics(entries)
    return _derive_music_profile_tags_from_metrics(metrics, len(entries))


def _soundtrack_key(option: SoundtrackReference) -> tuple[str, str]:
    return option.artist, option.title


def _resolve_soundtrack_family(story_mode: str, profile_tags: set[str]) -> str:
    family = _SOUNDTRACK_FAMILY_BY_STORY_MODE.get(story_mode)
    if family:
        return family
    if {"archive", "multi_generation"} & profile_tags:
        return "memory_archive"
    if "cultural_travel" in profile_tags:
        return "cultural_travel"
    if "leisure_travel" in profile_tags:
        return "leisure_travel"
    if "childhood" in profile_tags:
        return "childhood_family"
    if "group_family" in profile_tags:
        return "family_celebration"
    return "portrait_intimate"


def _get_soundtrack_pool(category_key: str, story_mode: str, profile_tags: set[str]) -> list[SoundtrackReference]:
    family = _resolve_soundtrack_family(story_mode, profile_tags)
    category_items = _SOUNDTRACK_REFERENCES[category_key]
    category_lookup = {_soundtrack_key(option): option for option in category_items}
    pool_keys = _SOUNDTRACK_HARD_POOLS.get(category_key, {}).get(family, ())
    selected = [category_lookup[key] for key in pool_keys if key in category_lookup]
    return selected or category_items


def _presence_threshold(entry_count: int, ratio: float, minimum: int = 2) -> int:
    if entry_count <= 0:
        return minimum
    return max(minimum, int(round(entry_count * ratio)))


def _derive_music_profile_tags_from_metrics(
    metrics: dict[str, float | int | bool],
    entry_count: int,
) -> set[str]:
    profile_tags: set[str] = set()
    low_presence = 1 if entry_count <= 3 else 2
    medium_presence = _presence_threshold(entry_count, 0.22, 2)
    strong_presence = _presence_threshold(entry_count, 0.35, 3)

    if metrics["warm_hits"] >= medium_presence:
        profile_tags.add("warm")
    if metrics["archive_hits"] >= low_presence and (
        metrics["archive_hits"] >= medium_presence or metrics["elder_hits"] >= 1
    ):
        profile_tags.update({"archive", "nostalgic"})
    if metrics["explicit_travel_hits"] >= low_presence and metrics["travel_hits"] >= medium_presence:
        profile_tags.add("travel")
    if "travel" in profile_tags and metrics["culture_hits"] >= low_presence:
        profile_tags.add("cultural_travel")
    if "travel" in profile_tags and metrics["leisure_hits"] >= low_presence:
        profile_tags.add("leisure_travel")
    if "travel" in profile_tags and metrics["group_hits"] >= low_presence:
        profile_tags.add("family_trip")
    if metrics["reflective_hits"] >= medium_presence or ("archive" in profile_tags and metrics["intimate_hits"] >= 1):
        profile_tags.add("reflective")
    if metrics["night_hits"] >= low_presence:
        profile_tags.add("night")
    if metrics["motion_hits"] >= low_presence or metrics["average_energy"] >= 1.5:
        profile_tags.add("motion")
    if metrics["elegant_hits"] >= low_presence:
        profile_tags.add("elegant")
    if metrics["group_hits"] >= medium_presence or metrics["average_people"] >= 2.5:
        profile_tags.add("group_family")
    if metrics["outing_hits"] >= medium_presence and metrics["group_hits"] >= low_presence:
        profile_tags.add("family_outing")
    if metrics["holiday_hits"] >= low_presence:
        profile_tags.add("holiday")
    if metrics["celebration_hits"] >= medium_presence or (
        metrics["celebration_hits"] >= low_presence and "holiday" in profile_tags
    ):
        profile_tags.add("celebration")
    if metrics["wedding_hits"] >= low_presence:
        profile_tags.update({"wedding", "celebration", "elegant"})
    if metrics["fishing_hits"] >= low_presence:
        profile_tags.add("fishing")
    if (
        metrics["child_hits"] >= medium_presence
        and metrics["child_hits"] >= max(low_presence, metrics["adult_hits"] // 2)
    ):
        profile_tags.add("childhood")
    if metrics["elder_hits"] >= 1 and (
        metrics["child_hits"] >= low_presence or metrics["archive_hits"] >= low_presence
    ):
        profile_tags.add("multi_generation")
    if metrics["playful_hits"] >= medium_presence or (
        "childhood" in profile_tags and (metrics["motion_hits"] >= 1 or metrics["average_energy"] >= 1.2)
    ):
        profile_tags.add("playful")
    if metrics["intimate_hits"] >= low_presence or metrics["has_closeup"]:
        profile_tags.add("intimate")
    if metrics["pet_hits"] >= low_presence:
        profile_tags.add("pets")
    if metrics["dog_hits"] >= low_presence:
        profile_tags.add("dogs")
    if metrics["cat_hits"] >= low_presence:
        profile_tags.add("cats")
    if "childhood" not in profile_tags and metrics["adult_hits"] >= medium_presence:
        profile_tags.add("adult_portrait")
    if metrics["average_energy"] >= 2.0 or metrics["motion_hits"] >= strong_presence:
        profile_tags.add("energetic")
    elif metrics["average_energy"] <= 1.0:
        profile_tags.add("gentle")
    else:
        profile_tags.add("balanced")
    if not profile_tags:
        profile_tags.update({"balanced", "warm"})
    return profile_tags


def _select_soundtrack_references(
    category_key: str,
    profile_tags: set[str],
    story_mode: str,
) -> list[SoundtrackReference]:
    weighted: list[tuple[int, int, int, SoundtrackReference]] = []
    mode_rule = _SOUNDTRACK_MODE_RULES.get(story_mode, _SOUNDTRACK_MODE_RULES["generic_human_story"])
    preferred_tags = mode_rule["prefer"]
    avoided_tags = mode_rule["avoid"]
    candidate_pool = _get_soundtrack_pool(category_key, story_mode, profile_tags)
    category_lookup = {
        _soundtrack_key(option): index
        for index, option in enumerate(_SOUNDTRACK_REFERENCES[category_key])
    }
    for pool_index, option in enumerate(candidate_pool):
        score = 0
        option_tags = set(option.tags)
        score += len(option_tags & profile_tags) * 3
        score += len(option_tags & preferred_tags) * 5
        score -= len(option_tags & avoided_tags) * 4
        if "cultural_travel" in profile_tags and {"travel", "cultural", "scenic"} & option_tags:
            score += 4
        if "leisure_travel" in profile_tags and {"travel", "leisure", "sun", "scenic", "elegant"} & option_tags:
            score += 4
        if "family_trip" in profile_tags and {"travel", "family", "scenic", "light"} & option_tags:
            score += 3
        if "family_outing" in profile_tags and {"motion", "light", "family", "playful"} & option_tags:
            score += 3
        if "celebration" in profile_tags and {"celebration", "bright", "group", "playful"} & option_tags:
            score += 3
        if "warm" in profile_tags and {"family", "light", "tender"} & option_tags:
            score += 2
        if "childhood" in profile_tags and {"family", "playful", "light", "childhood"} & option_tags:
            score += 4
        if "group_family" in profile_tags and {"family", "group", "graceful", "multi_generation"} & option_tags:
            score += 3
        if "holiday" in profile_tags and {"holiday", "bright", "light", "playful"} & option_tags:
            score += 3
        if "archive" in profile_tags and {"archive", "nostalgic", "reflective", "multi_generation"} & option_tags:
            score += 4
        if "dreamy" in profile_tags and {"dreamy", "elegant", "reflective"} & option_tags:
            score += 2
        if "motion" in profile_tags and {"motion", "dynamic", "groove"} & option_tags:
            score += 3
        if "night" in profile_tags and {"night", "reflective", "melancholic"} & option_tags:
            score += 2
        if "adult_portrait" in profile_tags and {"elegant", "intimate", "graceful"} & option_tags:
            score += 2
        if story_mode == "archive_family_memory" and {"archive", "reflective"} & option_tags:
            score += 3
        if story_mode == "adult_leisure_escape" and {"leisure", "sun", "relaxed"} & option_tags:
            score += 3
        if story_mode == "cultural_travel" and {"cultural", "flow"} & option_tags:
            score += 3
        if story_mode in {"festive_childhood", "childhood_album"} and {"childhood", "playful", "light"} & option_tags:
            score += 3
        if story_mode == "festive_family" and {"family", "group", "celebration"} & option_tags:
            score += 3
        if story_mode == "adult_family_portrait" and {"family", "elegant", "graceful", "intimate", "warm"} & option_tags:
            score += 3
        weighted.append(
            (
                score,
                len(option_tags & preferred_tags),
                -pool_index,
                option,
            )
        )
    weighted.sort(reverse=True)
    selected = [option for _score, _preferred, _neg_index, option in weighted[:5]]
    if len(selected) >= 5:
        return selected

    selected_keys = {_soundtrack_key(option) for option in selected}
    fallback_pool = []
    for option in _SOUNDTRACK_REFERENCES[category_key]:
        if _soundtrack_key(option) not in selected_keys:
            fallback_pool.append(option)

    if not fallback_pool:
        return selected

    fallback_weighted: list[tuple[int, int, int, SoundtrackReference]] = []
    for option in fallback_pool:
        option_tags = set(option.tags)
        score = len(option_tags & profile_tags) * 2
        score += len(option_tags & preferred_tags) * 3
        score -= len(option_tags & avoided_tags) * 3
        fallback_weighted.append(
            (
                score,
                len(option_tags & preferred_tags),
                -category_lookup.get(_soundtrack_key(option), 0),
                option,
            )
        )
    fallback_weighted.sort(reverse=True)
    for _score, _preferred, _neg_index, option in fallback_weighted:
        selected.append(option)
        if len(selected) == 5:
            break
    return selected


def _derive_story_mode(
    metrics: dict[str, float | int | bool],
    profile_tags: set[str],
) -> str:
    if {"archive", "multi_generation"} <= profile_tags:
        return "archive_family_memory"
    if "cultural_travel" in profile_tags:
        return "cultural_travel"
    if (
        "leisure_travel" in profile_tags
        and "adult_portrait" in profile_tags
        and metrics["travel_hits"] >= max(int(metrics["group_hits"]) + 4, 3)
        and metrics["holiday_hits"] == 0
        and metrics["child_hits"] <= 1
    ):
        return "adult_leisure_escape"
    if {"childhood", "celebration"} <= profile_tags or {"childhood", "holiday"} <= profile_tags:
        return "festive_childhood"
    if "childhood" in profile_tags:
        return "childhood_album"
    if (
        ("family_outing" in profile_tags or "family_trip" in profile_tags)
        and metrics["travel_hits"] >= max(int(metrics["group_hits"]) + 4, 5)
    ):
        return "family_outing"
    if "adult_portrait" in profile_tags and "group_family" in profile_tags and (
        "celebration" in profile_tags or "holiday" in profile_tags or "multi_generation" in profile_tags
    ):
        return "adult_family_portrait"
    if {"group_family", "holiday"} <= profile_tags or {"group_family", "celebration"} <= profile_tags:
        return "festive_family"
    if "family_outing" in profile_tags or "family_trip" in profile_tags:
        return "family_outing"
    if "leisure_travel" in profile_tags and "adult_portrait" in profile_tags:
        return "adult_leisure_escape"
    if "adult_portrait" in profile_tags:
        return "adult_portrait"
    if "group_family" in profile_tags:
        return "family_portrait"
    return "generic_human_story"


def _collect_entry_signal_phrases(entry: SequenceRecommendationEntry) -> list[str]:
    phrases: list[str] = []
    candidate = entry.candidate
    phrases.extend(
        token
        for token in candidate.series_subject_tokens
        if _normalize_series_token(token)
    )
    phrases.extend(
        token
        for token in candidate.series_appearance_tokens
        if _normalize_series_token(token)
    )
    phrases.extend(
        token
        for token in candidate.series_pose_tokens
        if _normalize_series_token(token)
    )
    phrases.extend(candidate.continuity_notes)
    scene_analysis = candidate.assets.scene_analysis or {}
    for key in ("summary", "background", "shot_type", "main_action"):
        value = scene_analysis.get(key)
        if value:
            phrases.append(str(value))
    for key in ("mood", "relationships"):
        values = scene_analysis.get(key) or []
        phrases.extend(str(value) for value in values if value)
    phrases.append(candidate.clip.name)
    return phrases


def _collect_signal_phrases(entries: list[SequenceRecommendationEntry]) -> list[str]:
    phrases: list[str] = []
    for entry in entries:
        phrases.extend(_collect_entry_signal_phrases(entry))
    return phrases


def _tokenize_text(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", text)]


def _phrase_matches_fragment(phrase: str, fragment: str) -> bool:
    phrase_tokens = _tokenize_text(phrase)
    fragment_tokens = _tokenize_text(fragment)
    if not phrase_tokens or not fragment_tokens:
        return False
    if len(fragment_tokens) == 1:
        fragment_token = fragment_tokens[0]
        return any(token.startswith(fragment_token) for token in phrase_tokens)
    window_size = len(fragment_tokens)
    for start_index in range(len(phrase_tokens) - window_size + 1):
        window = phrase_tokens[start_index:start_index + window_size]
        if all(token.startswith(fragment_token) for token, fragment_token in zip(window, fragment_tokens)):
            return True
    return False


def _phrase_has_exact_token(phrase: str, exact_tokens: set[str]) -> bool:
    if not exact_tokens:
        return False
    phrase_tokens = set(_tokenize_text(phrase))
    return bool(phrase_tokens & exact_tokens)


def _count_fragment_hits(phrases: list[str], fragments: tuple[str, ...]) -> int:
    count = 0
    for phrase in phrases:
        if any(_phrase_matches_fragment(phrase, fragment) for fragment in fragments):
            count += 1
    return count


def _entry_matches_signal(
    phrases: list[str],
    *,
    fragments: tuple[str, ...] = (),
    exact_tokens: tuple[str, ...] = (),
) -> bool:
    normalized_exact_tokens = {str(token).lower() for token in exact_tokens if token}
    for phrase in phrases:
        if fragments and any(_phrase_matches_fragment(phrase, fragment) for fragment in fragments):
            return True
        if normalized_exact_tokens and _phrase_has_exact_token(phrase, normalized_exact_tokens):
            return True
    return False


def _count_entry_signal_hits(
    phrases_by_entry: list[list[str]],
    *,
    fragments: tuple[str, ...] = (),
    exact_tokens: tuple[str, ...] = (),
) -> int:
    count = 0
    for phrases in phrases_by_entry:
        if _entry_matches_signal(phrases, fragments=fragments, exact_tokens=exact_tokens):
            count += 1
    return count


def _count_entry_fragment_hits(
    phrases_by_entry: list[list[str]],
    fragments: tuple[str, ...],
) -> int:
    return _count_entry_signal_hits(phrases_by_entry, fragments=fragments)


def _collect_profile_metrics(entries: list[SequenceRecommendationEntry]) -> dict[str, float | int | bool]:
    average_energy = sum(entry.candidate.energy_level for entry in entries) / max(1, len(entries))
    average_people = sum(entry.candidate.people_count for entry in entries) / max(1, len(entries))
    phrases_by_entry = [_collect_entry_signal_phrases(entry) for entry in entries]
    has_youngest_child_anchor = any(
        "youngest child" in note
        for entry in entries
        for note in entry.candidate.main_character_notes
    )
    has_closeup = any(entry.candidate.shot_scale >= 2 for entry in entries)
    metrics: dict[str, float | int | bool] = {
        "entry_count": len(entries),
        "average_energy": average_energy,
        "average_people": average_people,
        "warm_hits": _count_entry_fragment_hits(phrases_by_entry, ("уют", "тепл", "дружел", "smile", "warm", "love", "care")),
        "child_hits": _count_entry_fragment_hits(phrases_by_entry, ("ребен", "ребён", "младен", "девоч", "мальчик", "baby", "child")),
        "elder_hits": _count_entry_fragment_hits(phrases_by_entry, ("бабуш", "дедуш", "пожил", "elder", "grand")),
        "group_hits": _count_entry_fragment_hits(phrases_by_entry, ("семейн", "семья", "группа", "портрет", "поколен", "вместе", "родствен", "семи человек", "three generations")),
        "holiday_hits": _count_entry_fragment_hits(phrases_by_entry, ("празд", "christmas", "ёлк", "елк", "гирлянд", "санта", "огни", "xmas")),
        "archive_hits": _count_entry_fragment_hits(phrases_by_entry, ("винтаж", "ретро", "архив", "old photo")) + _count_year_prefixed_clip_names(entries),
        "playful_hits": _count_entry_fragment_hits(phrases_by_entry, ("игрив", "беззабот", "улыб", "радост", "bike", "play", "ride", "jump")),
        "reflective_hits": _count_entry_fragment_hits(phrases_by_entry, ("задум", "интросп", "quiet", "alone", "serious", "спокой", "calm", "still")),
        "night_hits": _count_entry_fragment_hits(phrases_by_entry, ("ноч", "night", "evening", "dusk", "moon")),
        "intimate_hits": _count_entry_fragment_hits(phrases_by_entry, ("обнима", "hug", "close", "лиц", "взгляд", "мимик", "плече", "held", "holding")),
        "pet_hits": _count_entry_signal_hits(
            phrases_by_entry,
            fragments=("собак", "щен", "кошк", "животн"),
            exact_tokens=(
                "пес", "пёс", "пса", "псу", "псом",
                "кот", "кота", "коту", "котом", "коты", "котик", "котики", "котенок", "котёнок",
                "котенка", "котёнка", "котят",
                "dog", "dogs", "puppy", "puppies", "cat", "cats", "kitten", "kittens", "pet", "pets",
            ),
        ),
        "dog_hits": _count_entry_signal_hits(
            phrases_by_entry,
            fragments=("собак", "щен"),
            exact_tokens=("пес", "пёс", "пса", "псу", "псом", "dog", "dogs", "puppy", "puppies"),
        ),
        "cat_hits": _count_entry_signal_hits(
            phrases_by_entry,
            fragments=("кошк",),
            exact_tokens=(
                "кот", "кота", "коту", "котом", "коты", "котик", "котики", "котенок", "котёнок",
                "котенка", "котёнка", "котят",
                "cat", "cats", "kitten", "kittens",
            ),
        ),
        "wedding_hits": _count_entry_signal_hits(
            phrases_by_entry,
            fragments=("свад", "невест", "жених", "молодож"),
            exact_tokens=("wedding", "bride", "groom"),
        ),
        "fishing_hits": _count_entry_signal_hits(
            phrases_by_entry,
            fragments=("рыбак", "рыболов", "рыбал", "рыб", "улов", "пойман", "щук"),
            exact_tokens=("fish", "fishes", "fishing", "fisherman", "fishermen", "angler", "catch", "caught", "pike"),
        ),
        "elegant_hits": _count_entry_fragment_hits(phrases_by_entry, ("плать", "наряд", "pose", "beauty", "elegant", "glamour", "dress")),
        "motion_hits": _count_entry_fragment_hits(phrases_by_entry, ("тан", "бег", "движ", "walk", "ride", "zoom", "pan", "play")),
        "adult_hits": _count_entry_fragment_hits(phrases_by_entry, ("взросл", "женщин", "мужчин", "woman", "man")),
        "explicit_travel_hits": _count_entry_fragment_hits(phrases_by_entry, ("поезд", "путеш", "турист", "japan", "япон", "замок", "храм", "деревн", "олен", "море", "причал", "лодк", "террас", "ресторан", "кафе", "берег", "sea", "pier", "boat", "terrace", "restaurant", "castle", "temple", "village", "deer", "shrine")),
        "culture_hits": _count_entry_fragment_hits(phrases_by_entry, ("япон", "замок", "храм", "деревн", "олен", "castle", "temple", "village", "deer", "japan", "shrine")),
        "leisure_hits": _count_entry_fragment_hits(phrases_by_entry, ("море", "ресторан", "террас", "кафе", "бокал", "вино", "закат", "отдых", "умиротвор", "наслажд", "sea", "restaurant", "terrace", "wine", "sunset", "relax", "pier", "boat")),
        "outing_hits": _count_entry_fragment_hits(phrases_by_entry, ("улиц", "двор", "мост", "велосипед", "прогул", "street", "courtyard", "bridge", "bike", "walk", "outdoor")),
        "celebration_hits": _count_entry_fragment_hits(phrases_by_entry, ("празд", "prom", "dance", "вечер", "обнима", "xmas", "christmas", "ёлк", "елк", "гирлянд", "party", "hug")),
        "travel_hits": 0,
        "has_youngest_child_anchor": has_youngest_child_anchor,
        "has_closeup": has_closeup,
    }
    metrics["travel_hits"] = int(metrics["explicit_travel_hits"]) + _count_entry_fragment_hits(
        phrases_by_entry,
        ("лес", "вода", "водо", "forest", "lake", "view", "панорам", "природ", "scenic"),
    )
    return metrics


def _count_year_prefixed_clip_names(entries: list[SequenceRecommendationEntry]) -> int:
    count = 0
    for entry in entries:
        clip_name = entry.candidate.clip.name
        if re.search(r"(^|[_\W])(19\d{2})([_\W])", clip_name):
            count += 1
    return count


def _describe_scene_motif(
    profile_tags: set[str],
    story_mode: str,
    profile_metrics: dict[str, float | int | bool],
) -> str:
    motif = _resolve_story_specific_motif(profile_tags, profile_metrics)
    if motif == "wedding_primary":
        return "свадебный день, жених, невеста, букет, поцелуй и романтические кадры пары"
    if motif == "wedding_age_arc":
        return "детство, взросление героя, семейные сцены и финальный свадебный блок"
    if motif == "wedding_accent":
        return "семейные сцены, взрослые портреты и свадебный эпизод как важная линия ролика"
    if motif == "fishing_primary":
        return "рыбак, пойманная рыба, природные планы и спокойный ритм рыбалки"
    if motif == "fishing_accent":
        if "travel" in profile_tags or story_mode in {"cultural_travel", "adult_leisure_escape", "family_outing"}:
            return "поездка, встречи, природные и дорожные планы, среди которых появляются рыбацкие эпизоды"
        if "group_family" in profile_tags:
            return "взрослые сцены героя, семейные встречи и отдельные рыбацкие эпизоды"
        return "разные занятия и сцены героя, среди которых появляется рыбацкая линия"
    if story_mode == "archive_family_memory":
        return "архивные семейные фотографии, переходы между возрастами и межпоколенческие встречи"
    if story_mode == "cultural_travel":
        return "храмы, замки, мосты, деревни, парки и другие точки маршрута путешествия"
    if story_mode == "adult_leisure_escape":
        return "террасы, рестораны, морские виды, прогулочные кадры и спокойный ритм отдыха"
    if story_mode == "festive_childhood":
        return "детские портреты, домашние праздники, игры, улыбки и семейные микрособытия"
    if story_mode == "childhood_album":
        return "детские портреты, домашние ритуалы, игры и моменты взросления"
    if story_mode == "festive_family":
        return "праздничные групповые портреты, домашние встречи и общая семейная сцена"
    if story_mode == "adult_family_portrait":
        return "взрослые семейные портреты, родственники разных поколений, праздничные столы и зрелые сцены близости"
    if story_mode == "family_outing":
        return "прогулки, выезды, остановки по пути и совместные впечатления вне дома"
    if story_mode == "adult_portrait":
        return "взрослый портрет и характер героя внутри спокойной жизненной среды"
    if "archive" in profile_tags and "multi_generation" in profile_tags:
        return "архивные семейные фотографии, переходы между возрастами и межпоколенческие встречи"
    if "cultural_travel" in profile_tags:
        return "храмы, замки, мосты, деревни, парки и другие точки маршрута путешествия"
    if "leisure_travel" in profile_tags:
        return "террасы, рестораны, морские виды, прогулочные кадры и спокойный ритм отдыха"
    if "family_trip" in profile_tags or "family_outing" in profile_tags:
        return "прогулки, дорожные остановки, семейные выезды и совместные впечатления вне дома"
    if "group_family" in profile_tags and "holiday" in profile_tags:
        return "праздничные групповые портреты, домашние встречи и общая семейная сцена"
    if "childhood" in profile_tags and "playful" in profile_tags:
        return "детские портреты, игры, домашние ритуалы и моменты взросления"
    if "group_family" in profile_tags:
        return "групповые семейные портреты и эмоциональные связи между родственниками"
    if "adult_portrait" in profile_tags:
        return "взрослый портрет и характер героя внутри жизненного окружения"
    return ""
