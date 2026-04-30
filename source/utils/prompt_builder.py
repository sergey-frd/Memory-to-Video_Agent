from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from config import VideoFramingMode
from models.scene_analysis import SceneAnalysis
from utils.image_analysis import ImageMetadata

BASE_STYLE_GUIDELINES = (
    "Maximum photorealism, cinematic lighting, and 4K output.\n"
    "Facial identity, gaze, and anatomy remain stable.\n"
    "Camera motion stays smooth and cinematic.\n"
    "Depth of field and contrast reinforce the emotional center of the frame.\n"
    "The final frame must read as a new freeze-frame, not as a light retouch."
)


@dataclass
class PromptBundle:
    video_prompt: str
    video_prompt_ru: str
    final_frame_prompt: str
    image_edit_prompt: str | None = None


@dataclass
class BackgroundPromptBundle:
    background_prompt: str
    background_prompt_ru: str
    association_prompt: str
    association_prompt_ru: str


class PromptBuilder:
    """Build prompt bundles for video and final frame generation."""

    def __init__(
        self,
        metadata: ImageMetadata,
        stage_id: str,
        scene_analysis: SceneAnalysis | None = None,
        base_guidelines: str = BASE_STYLE_GUIDELINES,
        framing_mode: VideoFramingMode = VideoFramingMode.IDENTITY_SAFE,
        hide_phone_in_selfie: bool = True,
        prefer_loving_kindness_tone: bool = False,
    ):
        self.metadata = metadata
        self.metadata_description = metadata.format_description
        self.stage_id = stage_id
        self.scene_analysis = scene_analysis
        self.base_guidelines = base_guidelines
        self.framing_mode = framing_mode
        self.hide_phone_in_selfie = hide_phone_in_selfie
        self.prefer_loving_kindness_tone = prefer_loving_kindness_tone

    def build_video_prompt(
        self,
        prompt_index: int,
        total_videos: int,
        initial_frame_description: str,
        motion_sequence: Iterable[str],
    ) -> PromptBundle:
        motions = self._normalize_motion_sequence(motion_sequence)
        video_prompt_ru = self._build_video_prompt_ru(initial_frame_description, motions)
        video_prompt_en = self._build_video_prompt_en(initial_frame_description, motions)
        final_frame_prompt = self._build_style_edit_final_frame_prompt(
            prompt_index=prompt_index,
            total_videos=total_videos,
            motions=motions,
        )

        image_edit_prompt = None
        if 1 < prompt_index < total_videos:
            clean_prompt = self._strip_camera_motion(video_prompt_ru)
            image_edit_prompt = "\n".join(
                [
                    f"????: {self.stage_id}",
                    f"?????? #{prompt_index} (???? A+N):",
                    "????: ???????? ???????????, ??????????????? ???????? ?????????? ???????????? ??? ?????????? ???????? ??????.",
                    "",
                    clean_prompt,
                ]
            )

        return PromptBundle(
            video_prompt=video_prompt_en,
            video_prompt_ru=video_prompt_ru,
            final_frame_prompt=final_frame_prompt,
            image_edit_prompt=image_edit_prompt,
        )

    def build_background_prompt_bundle(self, motion_sequence: Iterable[str] | None = None) -> BackgroundPromptBundle:
        motions = self._normalize_motion_sequence(motion_sequence or [])
        summary = self.scene_analysis.summary if self.scene_analysis and self.scene_analysis.summary else self.metadata.scene_summary
        background = self.scene_analysis.background if self.scene_analysis and self.scene_analysis.background else self.metadata.composition_label
        action = self.scene_analysis.main_action if self.scene_analysis and self.scene_analysis.main_action else "the core action of the source frame"
        mood = ", ".join(self.scene_analysis.mood) if self.scene_analysis and self.scene_analysis.mood else self.metadata.atmosphere_label
        people_bits: list[str] = []
        if self.scene_analysis and self.scene_analysis.people:
            for person in self.scene_analysis.people:
                bit = person.label
                if person.clothing:
                    bit = f"{bit} in {person.clothing}"
                if person.position_in_frame:
                    bit = f"{bit}, positioned {person.position_in_frame}"
                people_bits.append(bit)
        people_text = "; ".join(people_bits) if people_bits else "all visible subjects from the source frame"
        motion_text_en = ", ".join(motions) if motions else "cinematic wide establishing movement"
        motion_text_ru = ", ".join(motions) if motions else "кинематографичное широкое открывающее движение"
        association_prompt = self._build_association_prompt_en(summary, background, action, mood, people_text, motion_text_en)
        association_prompt_ru = self._build_association_prompt_ru(summary, background, action, mood, people_text, motion_text_ru)
        return BackgroundPromptBundle(
            background_prompt="\n".join(
                [
                    f"Stage: {self.stage_id}",
                    "Create a cinematic horizontal 16:9 background-oriented image derived from the source frame.",
                    "Use the source image as the anchor reference, but do not treat this as a simple cleanup or people-removal task.",
                    f"Scene summary: {summary}.",
                    f"Visible subjects and anchors in the source frame: {people_text}.",
                    f"Main action or dramatic beat to echo: {action}.",
                    f"Environment to reinterpret and expand: {background}.",
                    f"Mood to preserve: {mood}.",
                    f"Camera-movement inspiration for the visual redesign: {motion_text_en}.",
                    *self._loving_kindness_background_lines_en(),
                    "First derive a realistic associative environmental image from the source-image analysis: nature, architecture, vegetation, landscape, or another grounded motif that naturally echoes the scene.",
                    "Then build the background as a balanced fusion of that realistic associative image and a blurred, transformed echo of the source image, so the result still feels connected to frame A while becoming more cinematic and expansive.",
                    "The associative image must stay clearly readable and realistic. The source-image echo must be secondary, enlarged in scale, more blurred, softer in contrast, and mixed in balance rather than overwhelming the associative layer.",
                    "Choose a creative cinematic approach: expand the world of the image, enrich the environment, add depth layers, spatial storytelling, and visual motifs that support the same scene.",
                    "You may recompose the frame into a wider setting, shift emphasis, add believable environmental details, and transform the scene into a stronger widescreen moment while preserving identity cues and scene continuity.",
                    "Prefer a background plate without the main visible people if that avoids a near-copy of the source frame. Reconstruct the hidden environment plausibly instead of leaving empty holes.",
                    "If the result stays too close to the source image, force at least one noticeable cinematic change: stronger background blur or separation, a lighter or darker lighting design, a clear color-tone shift, wider spatial expansion, or a more pronounced scale change.",
                    "Do not invent unrelated fantasy or disaster elements. No dragons, wolves, monsters, storms, explosions, supernatural effects, or new story subjects that are absent from the source scene.",
                    "Any changes must stay grounded in the same real-world scene and should come from cinematography, lighting, color, atmosphere, spatial expansion, and plausible environmental reconstruction.",
                    "Do not simply erase people and leave an empty plate. Build a visually rich, story-driven wide image inspired by the source frame and the planned camera movement.",
                    "No text. Maximum photorealism.",
                ]
            ),
            background_prompt_ru="\n".join(
                [
                    f"Этап: {self.stage_id}",
                    "Создай кинематографичное горизонтальное изображение 16:9, опираясь на исходный кадр.",
                    "Это не задача на простое удаление людей или механическую чистку фона.",
                    f"Краткое содержание сцены: {summary}.",
                    f"Видимые персонажи и опорные элементы исходного кадра: {people_text}.",
                    f"Ключевое действие или драматический момент: {action}.",
                    f"Среда, которую нужно творчески переосмыслить и расширить: {background}.",
                    f"Настроение, которое нужно сохранить: {mood}.",
                    f"Вдохновение от движения камеры: {motion_text_ru}.",
                    "Сначала найди правдоподобную визуальную ассоциацию с исходным кадром: природу, архитектуру, растительность, пейзаж или другой реальный мотив среды, который естественно перекликается со сценой.",
                    "Затем построй фон как сочетание этой ассоциативной картины и размытого, преобразованного эха исходного изображения, чтобы результат оставался связанным с кадром A, но становился более кинематографичным и широким.",
                    "Выбери более творческий кинематографичный подход: расширь мир кадра, усили глубину пространства, добавь выразительные слои окружения, детали среды и визуальные мотивы, которые поддерживают ту же сцену.",
                    "Допускается перераспределить акценты внутри широкого кадра, сделать композицию более выразительной и превратить исходный момент в сильный широкий кинематографичный образ, сохранив узнаваемость сцены и ключевых персонажей.",
                    "Если это помогает уйти от почти полной копии исходного кадра, лучше сделать фон без главных видимых людей и правдоподобно восстановить скрытые части среды, чем оставлять почти неизменённую фотографию.",
                    "Если результат всё равно получается слишком близким к исходному изображению, нужно обязательно сделать хотя бы одно заметное кинематографичное изменение: более мягкое размытие или отделение фона, более светлую или более тёмную световую схему, явный сдвиг цветового тона, расширение пространства или более выраженное изменение масштаба.",
                    "Не нужно просто убирать людей и оставлять пустой фон. Нужен визуально богатый, сюжетно осмысленный широкий кадр, вдохновлённый исходным изображением и выбранными движениями камеры.",
                    "Без текста. Максимальный фотореализм.",
                ]
            ),
            association_prompt=association_prompt,
            association_prompt_ru=association_prompt_ru,
        )

    def build_background_prompt(self, motion_sequence: Iterable[str] | None = None) -> str:
        return self.build_background_prompt_bundle(motion_sequence).background_prompt

    def _association_theme_en(self, summary: str, background: str, action: str, mood: str) -> str:
        haystack = " ".join((summary, background, action, mood)).lower()
        architecture_tokens = ("arch", "brick", "stone", "kitchen", "interior", "room", "window", "building", "corridor", "table")
        vegetation_tokens = ("tree", "leaf", "garden", "park", "forest", "green", "flower", "grass")
        landscape_tokens = ("mountain", "sea", "beach", "sky", "horizon", "valley", "river", "landscape")
        if any(token in haystack for token in architecture_tokens):
            return "architecture"
        if any(token in haystack for token in vegetation_tokens):
            return "vegetation"
        if any(token in haystack for token in landscape_tokens):
            return "landscape"
        return "nature"

    def _association_theme_ru(self, theme_en: str) -> str:
        mapping = {
            "architecture": "архитектура и интерьер",
            "vegetation": "растительность и природная среда",
            "landscape": "пейзаж и дальний план",
            "nature": "природная среда",
        }
        return mapping.get(theme_en, "природная среда")

    def _build_association_prompt_en(
        self,
        summary: str,
        background: str,
        action: str,
        mood: str,
        people_text: str,
        motion_text_en: str,
    ) -> str:
        theme = self._association_theme_en(summary, background, action, mood)
        return " ".join(
            [
                "Create a realistic associative environmental image that can serve as a background plate for the source frame.",
                f"Association direction: {theme}.",
                f"Scene anchor: {summary}.",
                f"Environment anchor: {background}.",
                f"Narrative beat: {action}.",
                f"Mood: {mood}.",
                *self._loving_kindness_association_lines_en(),
                f"Visible people to avoid duplicating directly: {people_text}.",
                f"Camera-motion inspiration: {motion_text_en}.",
                "Show a believable real-world place with clear depth, realistic textures, natural lighting, and cinematic composition.",
                "Keep the scene grounded in reality: no fantasy, no monsters, no storms, no surreal objects, no unrelated subjects.",
                "Build a detailed, photorealistic association image of the environment itself so it can later be blended with a blurred enlarged echo of the source image.",
                "The association image must be complete and readable on its own as a realistic landscape, city view, architectural interior, vegetation-rich space, or other plausible environmental world linked to the source analysis.",
            ]
        )

    def _build_association_prompt_ru(
        self,
        summary: str,
        background: str,
        action: str,
        mood: str,
        people_text: str,
        motion_text_ru: str,
    ) -> str:
        theme_ru = self._association_theme_ru(self._association_theme_en(summary, background, action, mood))
        return " ".join(
            [
                "Создай реалистичное ассоциативное изображение среды, которое может быть фоном для исходной фотографии.",
                f"Направление ассоциации: {theme_ru}.",
                f"Опорное содержание сцены: {summary}.",
                f"Опорное описание среды: {background}.",
                f"Ключевое действие: {action}.",
                f"Настроение: {mood}.",
                f"Люди, которых не нужно дублировать напрямую: {people_text}.",
                f"Вдохновение от движения камеры: {motion_text_ru}.",
                "Нужна правдоподобная реальная среда с читаемой глубиной пространства, естественным светом, реалистичными фактурами и кинематографичной композицией.",
                "Без фантастики, без чудовищ, без бури, без сюрреалистических объектов и без новых посторонних персонажей.",
                "Нужно создать подробное фотореалистичное ассоциативное изображение природы, пейзажа, города, архитектуры или интерьера, логически связанное с анализом исходного кадра.",
                "Это ассоциативное изображение должно быть самостоятельным реалистичным фоном, который затем можно смешать с увеличенным и размытым эхом исходной фотографии.",
            ]
        )

    def _build_video_prompt_ru(self, initial_frame_description: str, motions: list[str]) -> str:
        video_lines = [
            f"????: {self.stage_id}",
            f"????: {initial_frame_description}",
            f"??????: {self._format_description_ru()}. ????????? ???????? ????????? ? ??????????.",
            "",
            "?????:",
            *self._scene_section_lines_ru(),
            "",
            "?????????:",
            *self._subject_section_lines_ru(),
            "",
            "????????? ? ???????????:",
            *self._relationship_section_lines_ru(),
            "",
            "??????:",
            *self._framing_camera_lines_ru(),
            *self._camera_section_lines_ru(motions),
            "",
            "???? ? ???????:",
            f"- ?????????: {self._brightness_ru()}.",
            f"- ????????: {self._contrast_ru()}.",
            f"- ???????: {self._palette_ru()}.",
            f"- ??????? ?????: {self._depth_ru()}.",
            "",
            "?????:",
            "- ??????????? ????????????????? ???????.",
            "- ??????? ???????????, ?????????????? ??????? ????, ????? ? ?????????.",
            "",
            "?????????? ?????????????:",
            "- ????????, ????? ????, ??????????? ??????? ? ??????? ???? ?? ????????.",
            "- ??????????? ?????? ??????????? ???????????? ????????????? ???? ? ??????.",
            *self._selfie_phone_lines_ru(),
            "",
            "?????????:",
            f"- ????????? ????????: {self._atmosphere_ru()}.",
            *self._loving_kindness_video_lines_ru(),
            "",
            "????????:",
            "- 4K, ??????????? ???????????????, ???????? ? ???????????? ?????????.",
        ]
        return "\n".join(video_lines)

    def _build_video_prompt_en(self, initial_frame_description: str, motions: list[str]) -> str:
        video_lines = [
            f"Stage: {self.stage_id}",
            f"FRAME: {self._translate_initial_frame_description(initial_frame_description)}",
            f"Format: {self._format_description_en()}. Preserve the source aspect ratio and orientation.",
            "",
            "SCENE:",
            *self._scene_section_lines_en(),
            "",
            "SUBJECTS:",
            *self._subject_section_lines_en(),
            "",
            "RELATIONSHIPS AND DRAMA:",
            *self._relationship_section_lines_en(),
            "",
            "CAMERA:",
            *self._framing_camera_lines_en(),
            *self._camera_section_lines_en(motions),
            "",
            "LIGHT AND TEXTURE:",
            f"- Lighting: {self._brightness_en()}.",
            f"- Contrast: {self._contrast_en()}.",
            f"- Palette: {self._palette_en()}.",
            f"- Depth profile: {self._depth_en()}.",
            "",
            "STYLE:",
            "- Natural cinematic realism.",
            "- High detail with believable skin, fabric, and environmental texture.",
            "",
            "CONTINUITY:",
            "- Identity, facial structure, gaze direction, and base pose must stay consistent.",
            "- Only minimal natural micro-movements of body and clothing are allowed.",
            *self._selfie_phone_lines_en(),
            "",
            "ATMOSPHERE:",
            f"- Preserve the feeling of {self._atmosphere_en()}.",
            *self._loving_kindness_video_lines_en(),
            "",
            "QUALITY:",
            "- 4K, maximum photorealism, stable anatomy, stable identity.",
        ]
        return "\n".join(video_lines)

    def _normalize_motion_sequence(self, motion_sequence: Iterable[str]) -> list[str]:
        cleaned = [motion.strip() for motion in motion_sequence if motion and motion.strip()]
        return cleaned or ["standard cinematic movement"]

    def _scene_section_lines_ru(self) -> list[str]:
        lines = [
            f"- ??????? ?????????? ??????: {self._composition_ru()}, {self._palette_ru()}, {self._brightness_ru()}.",
            f"- ????????? ?????: {self._atmosphere_ru()}.",
        ]
        if not self.scene_analysis:
            return lines

        lines.extend(
            [
                f"- ????????? ???????? ?????: {self.scene_analysis.summary}",
                f"- ?????????? ??????? ?????: {self.scene_analysis.people_count}.",
            ]
        )
        if self.scene_analysis.background:
            lines.append(f"- ???: {self.scene_analysis.background}")
        if self.scene_analysis.shot_type:
            lines.append(f"- ???? ??????: {self.scene_analysis.shot_type}")
        if self.scene_analysis.main_action:
            lines.append(f"- ???????? ????????: {self.scene_analysis.main_action}")
        if self.scene_analysis.mood:
            lines.append(f"- ??????????: {', '.join(self.scene_analysis.mood)}")
        return lines

    def _scene_section_lines_en(self) -> list[str]:
        lines = [
            f"- Base visual structure: {self._composition_en()}, {self._palette_en()}, {self._brightness_en()}.",
            f"- Frame atmosphere: {self._atmosphere_en()}.",
        ]
        if not self.scene_analysis:
            return lines

        lines.extend(
            [
                f"- Detailed scene description: {self._scene_analysis_text_en(self.scene_analysis.summary)}",
                f"- Visible people count: {self.scene_analysis.people_count}.",
            ]
        )
        if self.scene_analysis.background:
            lines.append(f"- Background: {self._scene_analysis_text_en(self.scene_analysis.background)}")
        if self.scene_analysis.shot_type:
            lines.append(f"- Shot type: {self._scene_analysis_text_en(self.scene_analysis.shot_type)}")
        if self.scene_analysis.main_action:
            lines.append(f"- Main action: {self._scene_analysis_text_en(self.scene_analysis.main_action)}")
        if self.scene_analysis.mood:
            lines.append(f"- Mood: {', '.join(self._scene_analysis_text_en(item) for item in self.scene_analysis.mood)}")
        return lines

    def _subject_section_lines_ru(self) -> list[str]:
        if not self.scene_analysis or not self.scene_analysis.people:
            return [
                "- ???????? ????? ??? ??????????? ?????? ???????? ?????????? ??????? ?????.",
                "- ????? ????, ???????? ? ???? ????????? ?????????????.",
            ]

        lines: list[str] = []
        for person in self.scene_analysis.people:
            bits = [person.label]
            if person.position_in_frame:
                bits.append(f"????????????: {person.position_in_frame}")
            if person.role_in_scene:
                bits.append(f"????: {person.role_in_scene}")
            if person.facial_expression:
                bits.append(f"????????? ????: {person.facial_expression}")
            if person.clothing:
                bits.append(f"??????: {person.clothing}")
            if person.pose:
                bits.append(f"????: {person.pose}")
            lines.append(f"- {'; '.join(bits)}.")
        lines.append("- ???????? ? ?????????? ?????? ??????? ????????? ??????????? ??? ???????.")
        return lines

    def _subject_section_lines_en(self) -> list[str]:
        if not self.scene_analysis or not self.scene_analysis.people:
            return [
                "- The main subject or central group remains the visual center of the frame.",
                "- Facial structure, anatomy, and pose stay continuous.",
            ]

        lines: list[str] = []
        for person in self.scene_analysis.people:
            bits = [self._scene_analysis_text_en(person.label)]
            if person.position_in_frame:
                bits.append(f"position: {self._scene_analysis_text_en(person.position_in_frame)}")
            if person.role_in_scene:
                bits.append(f"role: {self._scene_analysis_text_en(person.role_in_scene)}")
            if person.facial_expression:
                bits.append(f"facial expression: {self._scene_analysis_text_en(person.facial_expression)}")
            if person.clothing:
                bits.append(f"clothing: {self._scene_analysis_text_en(person.clothing)}")
            if person.pose:
                bits.append(f"pose: {self._scene_analysis_text_en(person.pose)}")
            lines.append(f"- {'; '.join(bits)}.")
        lines.append("- Identity and recognizable details of every subject remain intact.")
        return lines

    def _relationship_section_lines_ru(self) -> list[str]:
        lines = ["- ????????????? ????? ????? ?????? ?????????? ???????? ? ??????????????."]
        if self.scene_analysis and self.scene_analysis.relationships:
            lines.extend([f"- {relation}." for relation in self.scene_analysis.relationships])
        if self.scene_analysis and self.scene_analysis.main_action:
            lines.append(f"- ???????? ?????? ???? ????????: {self.scene_analysis.main_action}.")
        return lines

    def _relationship_section_lines_en(self) -> list[str]:
        lines = ["- The emotional center of the frame must stay readable and believable."]
        if self.scene_analysis and self.scene_analysis.relationships:
            lines.extend([f"- {self._scene_analysis_text_en(relation)}." for relation in self.scene_analysis.relationships])
        if self.scene_analysis and self.scene_analysis.main_action:
            lines.append(f"- The action must remain clearly readable: {self._scene_analysis_text_en(self.scene_analysis.main_action)}.")
        return lines

    def _has_visible_people(self) -> bool:
        return bool(self.scene_analysis and self.scene_analysis.people_count > 0)

    def _identity_safe_camera_lines_ru(self) -> list[str]:
        if self._has_visible_people():
            return [
                "- При видимых людях предпочитать наблюдение с дистанции: общий или средне-общий план, ракурс сверху, снизу, сбоку, мягкий полуоблет, кран или дрон-подобное раскрытие пространства.",
                "- Не делать агрессивный наезд в лицо и не строить кадр как экстремальный крупный фронтальный портрет. Эмоцию раскрывать через жест, позу, силуэт, движение тела и связь человека со средой.",
            ]
        return [
            "- Камеру лучше вести через читаемое раскрытие пространства и без лишнего агрессивного приближения."
        ]

    def _identity_safe_camera_lines_en(self) -> list[str]:
        if self._has_visible_people():
            return [
                "- When people are visible, prefer identity-safe framing from a respectful distance: wide or medium-wide observation, side angle, top view, low angle, soft half-orbit, crane, or drone-like spatial reveal.",
                "- Avoid aggressive face push-ins and extreme frontal close-ups. Carry emotion through gesture, posture, silhouette, body language, and the subject's relation to the environment.",
            ]
        return [
            "- Prefer spatially readable camera movement without unnecessary aggressive close-in motion."
        ]

    def _camera_section_lines_ru(self, motions: list[str]) -> list[str]:
        context_anchor = self.scene_analysis.background if self.scene_analysis and self.scene_analysis.background else self._composition_ru()
        emotion_anchor = ", ".join(self.scene_analysis.mood) if self.scene_analysis and self.scene_analysis.mood else self._atmosphere_ru()
        action_anchor = self.scene_analysis.main_action if self.scene_analysis and self.scene_analysis.main_action else "????????????? ????? ?????"
        if len(motions) == 1:
            return [
                f"- {motions[0]}. ?????? ?????? ???????? ???, ????? ???????? {context_anchor} ? ????? ???????? ???????? ?? {action_anchor}, ??????? ????????? ? ??????????? ???."
            ]

        lines = [
            f"- ?????? ????? ?????: {motions[0]}. ?????? ??????? ?????????? {context_anchor} ? ???????????????? ???????? ?????, ??????? ????????? ? ??????????? ???.",
            f"- ?????? ????? ?????: {motions[1]}. ?????? ????????? ???????? ?? {action_anchor} ? ????????? ???????? {emotion_anchor}, ?? ?? ???????? ???? ?????????? ??????.",
        ]
        for extra_index, extra_motion in enumerate(motions[2:], start=3):
            lines.append(f"- ?????????????? ????? {extra_index}: {extra_motion}. ?????????? ??????? ??? ?????? ?????????? ?????????????.")
        return lines

    def _camera_section_lines_en(self, motions: list[str]) -> list[str]:
        context_anchor = self._scene_analysis_text_en(self.scene_analysis.background) if self.scene_analysis and self.scene_analysis.background else self._composition_en()
        emotion_anchor = ", ".join(self._scene_analysis_text_en(item) for item in self.scene_analysis.mood) if self.scene_analysis and self.scene_analysis.mood else self._atmosphere_en()
        action_anchor = self._scene_analysis_text_en(self.scene_analysis.main_action) if self.scene_analysis and self.scene_analysis.main_action else "the emotional center of the scene"
        if len(motions) == 1:
            return [
                f"- {motions[0]}. The camera path should reveal {context_anchor} and then hold attention on {action_anchor} while keeping a respectful distance from visible faces."
            ]

        lines = [
            f"- First segment: {motions[0]}. The camera first reveals {context_anchor} and the spatial context of the scene from an identity-safe distance.",
            f"- Second segment: {motions[1]}. The camera then shifts focus to {action_anchor} and intensifies the feeling of {emotion_anchor} without forcing a large facial close-up.",
        ]
        for extra_index, extra_motion in enumerate(motions[2:], start=3):
            lines.append(f"- Extra segment {extra_index}: {extra_motion}. Continue the transition without breaking visual continuity.")
        return lines

    def _framing_camera_lines_ru(self) -> list[str]:
        if self.framing_mode == VideoFramingMode.FACE_CLOSEUP:
            if self._has_visible_people():
                return [
                    "- \u0415\u0441\u043b\u0438 \u0432 \u043a\u0430\u0434\u0440\u0435 \u0432\u0438\u0434\u043d\u044b \u043b\u044e\u0434\u0438, \u0434\u043e\u043f\u0443\u0441\u043a\u0430\u0435\u0442\u0441\u044f \u0438 \u0434\u0430\u0436\u0435 \u043f\u0440\u0438\u0432\u0435\u0442\u0441\u0442\u0432\u0443\u0435\u0442\u0441\u044f \u0431\u043e\u043b\u0435\u0435 \u0431\u043b\u0438\u0437\u043a\u043e\u0435 \u043d\u0430\u0431\u043b\u044e\u0434\u0435\u043d\u0438\u0435 \u0437\u0430 \u043b\u0438\u0446\u043e\u043c \u0438 \u044d\u043c\u043e\u0446\u0438\u0435\u0439.",
                    "- \u041c\u043e\u0436\u043d\u043e \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c medium close-up, close-up \u0438 \u043c\u044f\u0433\u043a\u0438\u0439 \u043d\u0430\u0435\u0437\u0434 \u043a \u043b\u0438\u0446\u0443, \u0435\u0441\u043b\u0438 \u044d\u0442\u043e \u0443\u0441\u0438\u043b\u0438\u0432\u0430\u0435\u0442 \u0441\u0446\u0435\u043d\u0443 \u0438 \u043e\u0441\u0442\u0430\u0435\u0442\u0441\u044f \u0444\u043e\u0442\u043e\u0440\u0435\u0430\u043b\u0438\u0441\u0442\u0438\u0447\u043d\u044b\u043c.",
                ]
            return [
                "- \u041a\u0430\u043c\u0435\u0440\u0430 \u043c\u043e\u0436\u0435\u0442 \u0432\u044b\u0431\u0440\u0430\u0442\u044c \u0431\u043e\u043b\u0435\u0435 \u0431\u043b\u0438\u0437\u043a\u0438\u0439 \u0438 \u0438\u043d\u0442\u0438\u043c\u043d\u044b\u0439 \u043c\u0430\u0441\u0448\u0442\u0430\u0431, \u0435\u0441\u043b\u0438 \u044d\u0442\u043e \u0432\u044b\u0440\u0430\u0437\u0438\u0442\u0435\u043b\u044c\u043d\u043e \u0434\u043b\u044f \u043a\u0430\u0434\u0440\u0430."
            ]
        if self.framing_mode == VideoFramingMode.AI_OPTIMAL:
            if self._has_visible_people():
                return [
                    "- \u0412\u044b\u0431\u0438\u0440\u0430\u0439 \u0442\u043e\u0442 \u043c\u0430\u0441\u0448\u0442\u0430\u0431 \u0438 \u0442\u043e\u0442 \u0440\u0430\u043a\u0443\u0440\u0441, \u043a\u043e\u0442\u043e\u0440\u044b\u0435 \u0434\u0430\u044e\u0442 \u043c\u0430\u043a\u0441\u0438\u043c\u0430\u043b\u044c\u043d\u043e \u0441\u0438\u043b\u044c\u043d\u043e\u0435 \u0438 \u043a\u0438\u043d\u0435\u043c\u0430\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u0447\u043d\u043e\u0435 \u0447\u0442\u0435\u043d\u0438\u0435 \u0441\u0446\u0435\u043d\u044b.",
                    "- \u041d\u0435 \u043d\u0443\u0436\u043d\u043e \u0441\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0442\u044c\u0441\u044f \u0438\u0437-\u0437\u0430 \u0440\u0438\u0441\u043a\u0430 \u0443\u043a\u0440\u0443\u043f\u043d\u0435\u043d\u0438\u044f \u043b\u0438\u0446\u0430; \u043f\u0440\u0438\u043e\u0440\u0438\u0442\u0435\u0442 \u2014 \u0441\u0438\u043b\u0430 \u043a\u0430\u0434\u0440\u0430, \u044d\u043c\u043e\u0446\u0438\u0438 \u0438 \u0443\u0431\u0435\u0434\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c \u0441\u0446\u0435\u043d\u044b.",
                ]
            return [
                "- \u041a\u0430\u043c\u0435\u0440\u0430 \u043c\u043e\u0436\u0435\u0442 \u0432\u044b\u0431\u0438\u0440\u0430\u0442\u044c \u043b\u044e\u0431\u043e\u0439 \u043c\u0430\u0441\u0448\u0442\u0430\u0431, \u043e\u0442 \u0431\u043b\u0438\u0437\u043a\u043e\u0439 \u0434\u0435\u0442\u0430\u043b\u0438 \u0434\u043e \u0448\u0438\u0440\u043e\u043a\u043e\u0433\u043e \u043f\u0440\u043e\u0441\u0442\u0440\u0430\u043d\u0441\u0442\u0432\u0435\u043d\u043d\u043e\u0433\u043e \u0440\u0430\u0441\u043a\u0440\u044b\u0442\u0438\u044f, \u0435\u0441\u043b\u0438 \u0442\u0430\u043a \u043b\u0443\u0447\u0448\u0435 \u0434\u043b\u044f \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u044f."
            ]
        return self._identity_safe_camera_lines_ru()

    def _framing_camera_lines_en(self) -> list[str]:
        if self.framing_mode == VideoFramingMode.FACE_CLOSEUP:
            if self._has_visible_people():
                return [
                    "- When people are visible, close facial framing is allowed and may be used as the main emotional anchor of the shot.",
                    "- Medium close-ups, close-ups, and gentle push-ins toward the face are acceptable when they fit the source image and stay photorealistic.",
                ]
            return [
                "- The camera may choose a tighter and more intimate scale when that makes the frame more expressive."
            ]
        if self.framing_mode == VideoFramingMode.AI_OPTIMAL:
            if self._has_visible_people():
                return [
                    "- Choose the scale and angle that give the strongest cinematic reading of the scene, even if that includes stronger facial emphasis.",
                    "- Do not optimize for avoiding facial enlargement; optimize for emotional clarity, scene power, and cinematic impact.",
                ]
            return [
                "- Let the camera choose the most effective framing, from intimate detail to wide spatial reveal."
            ]
        return self._identity_safe_camera_lines_en()

    def _camera_section_lines_ru(self, motions: list[str]) -> list[str]:
        context_anchor = self.scene_analysis.background if self.scene_analysis and self.scene_analysis.background else self._composition_ru()
        emotion_anchor = ", ".join(self.scene_analysis.mood) if self.scene_analysis and self.scene_analysis.mood else self._atmosphere_ru()
        action_anchor = self.scene_analysis.main_action if self.scene_analysis and self.scene_analysis.main_action else "\u044d\u043c\u043e\u0446\u0438\u043e\u043d\u043d\u044b\u0439 \u0446\u0435\u043d\u0442\u0440 \u0441\u0446\u0435\u043d\u044b"
        if len(motions) == 1:
            return [f"- {motions[0]}. {self._single_motion_line_ru(context_anchor, action_anchor)}"]

        lines = [
            f"- \u041f\u0435\u0440\u0432\u044b\u0439 \u0441\u0435\u0433\u043c\u0435\u043d\u0442: {motions[0]}. {self._first_segment_line_ru(context_anchor)}",
            f"- \u0412\u0442\u043e\u0440\u043e\u0439 \u0441\u0435\u0433\u043c\u0435\u043d\u0442: {motions[1]}. {self._second_segment_line_ru(action_anchor, emotion_anchor)}",
        ]
        for extra_index, extra_motion in enumerate(motions[2:], start=3):
            lines.append(
                f"- \u0414\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u0439 \u0441\u0435\u0433\u043c\u0435\u043d\u0442 {extra_index}: {extra_motion}. "
                "\u041f\u0440\u043e\u0434\u043e\u043b\u0436\u0430\u0439 \u043f\u0435\u0440\u0435\u0445\u043e\u0434 \u043f\u043b\u0430\u0432\u043d\u043e \u0438 \u0431\u0435\u0437 \u043f\u043e\u0442\u0435\u0440\u0438 \u0432\u0438\u0437\u0443\u0430\u043b\u044c\u043d\u043e\u0439 \u043d\u0435\u043f\u0440\u0435\u0440\u044b\u0432\u043d\u043e\u0441\u0442\u0438."
            )
        return lines

    def _camera_section_lines_en(self, motions: list[str]) -> list[str]:
        context_anchor = self._scene_analysis_text_en(self.scene_analysis.background) if self.scene_analysis and self.scene_analysis.background else self._composition_en()
        emotion_anchor = ", ".join(self._scene_analysis_text_en(item) for item in self.scene_analysis.mood) if self.scene_analysis and self.scene_analysis.mood else self._atmosphere_en()
        action_anchor = self._scene_analysis_text_en(self.scene_analysis.main_action) if self.scene_analysis and self.scene_analysis.main_action else "the emotional center of the scene"
        if len(motions) == 1:
            return [f"- {motions[0]}. {self._single_motion_line_en(context_anchor, action_anchor)}"]

        lines = [
            f"- First segment: {motions[0]}. {self._first_segment_line_en(context_anchor)}",
            f"- Second segment: {motions[1]}. {self._second_segment_line_en(action_anchor, emotion_anchor)}",
        ]
        for extra_index, extra_motion in enumerate(motions[2:], start=3):
            lines.append(f"- Extra segment {extra_index}: {extra_motion}. Continue the transition without breaking visual continuity.")
        return lines

    def _single_motion_line_ru(self, context_anchor: str, action_anchor: str) -> str:
        if self.framing_mode == VideoFramingMode.FACE_CLOSEUP:
            return (
                f"\u041a\u0430\u043c\u0435\u0440\u0430 \u043c\u043e\u0436\u0435\u0442 \u0441\u043c\u0435\u043b\u0435\u0435 \u0432\u0435\u0441\u0442\u0438 \u0432\u0437\u0433\u043b\u044f\u0434 \u043a {action_anchor}, "
                "\u043f\u043e\u0437\u0432\u043e\u043b\u044f\u044f \u0431\u043e\u043b\u0435\u0435 \u0431\u043b\u0438\u0437\u043a\u0438\u0439 \u0438 \u0438\u043d\u0442\u0438\u043c\u043d\u044b\u0439 \u043f\u043e\u0440\u0442\u0440\u0435\u0442\u043d\u044b\u0439 \u043c\u0430\u0441\u0448\u0442\u0430\u0431."
            )
        if self.framing_mode == VideoFramingMode.AI_OPTIMAL:
            return (
                f"\u041a\u0430\u043c\u0435\u0440\u0430 \u0434\u043e\u043b\u0436\u043d\u0430 \u043d\u0430\u0439\u0442\u0438 \u043d\u0430\u0438\u0431\u043e\u043b\u0435\u0435 \u0441\u0438\u043b\u044c\u043d\u043e\u0435 \u0447\u0442\u0435\u043d\u0438\u0435 \u043c\u0435\u0436\u0434\u0443 {context_anchor} \u0438 {action_anchor}, "
                "\u0434\u0430\u0436\u0435 \u0435\u0441\u043b\u0438 \u0434\u043b\u044f \u044d\u0442\u043e\u0433\u043e \u043f\u043e\u043d\u0430\u0434\u043e\u0431\u0438\u0442\u0441\u044f \u0431\u043e\u043b\u0435\u0435 \u043a\u0440\u0443\u043f\u043d\u044b\u0439 \u043c\u0430\u0441\u0448\u0442\u0430\u0431."
            )
        return (
            f"\u041a\u0430\u043c\u0435\u0440\u0430 \u0434\u043e\u043b\u0436\u043d\u0430 \u0440\u0430\u0441\u043a\u0440\u044b\u0442\u044c {context_anchor} \u0438 \u0437\u0430\u0442\u0435\u043c \u0443\u0434\u0435\u0440\u0436\u0430\u0442\u044c \u0432\u043d\u0438\u043c\u0430\u043d\u0438\u0435 \u043d\u0430 {action_anchor}, "
            "\u0441\u043e\u0445\u0440\u0430\u043d\u044f\u044f \u0443\u0432\u0430\u0436\u0438\u0442\u0435\u043b\u044c\u043d\u0443\u044e \u0434\u0438\u0441\u0442\u0430\u043d\u0446\u0438\u044e \u043e\u0442 \u0432\u0438\u0434\u0438\u043c\u044b\u0445 \u043b\u0438\u0446."
        )

    def _first_segment_line_ru(self, context_anchor: str) -> str:
        if self.framing_mode == VideoFramingMode.FACE_CLOSEUP:
            return f"\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u043a\u0430\u043c\u0435\u0440\u0430 \u0440\u0430\u0441\u043a\u0440\u044b\u0432\u0430\u0435\u0442 {context_anchor}, \u043d\u043e \u043c\u043e\u0436\u0435\u0442 \u0431\u044b\u0441\u0442\u0440\u0435\u0435 \u043f\u0435\u0440\u0435\u0439\u0442\u0438 \u043a \u0431\u043e\u043b\u0435\u0435 \u0431\u043b\u0438\u0437\u043a\u043e\u043c\u0443 \u044d\u043c\u043e\u0446\u0438\u043e\u043d\u043d\u043e\u043c\u0443 \u043c\u0430\u0441\u0448\u0442\u0430\u0431\u0443."
        if self.framing_mode == VideoFramingMode.AI_OPTIMAL:
            return f"\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u043a\u0430\u043c\u0435\u0440\u0430 \u0432\u044b\u0431\u0438\u0440\u0430\u0435\u0442 \u0441\u0430\u043c\u044b\u0439 \u0441\u0438\u043b\u044c\u043d\u044b\u0439 \u0441\u043f\u043e\u0441\u043e\u0431 \u0440\u0430\u0441\u043a\u0440\u044b\u0442\u044c {context_anchor}, \u0431\u0435\u0437 \u0436\u0435\u0441\u0442\u043a\u043e\u0439 \u043f\u0440\u0438\u0432\u044f\u0437\u043a\u0438 \u043a \u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e\u0439 \u0434\u0438\u0441\u0442\u0430\u043d\u0446\u0438\u0438."
        return f"\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u043a\u0430\u043c\u0435\u0440\u0430 \u0440\u0430\u0441\u043a\u0440\u044b\u0432\u0430\u0435\u0442 {context_anchor} \u0438 \u043f\u0440\u043e\u0441\u0442\u0440\u0430\u043d\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439 \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 \u0441\u0446\u0435\u043d\u044b \u0441 \u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e\u0439 \u0434\u0438\u0441\u0442\u0430\u043d\u0446\u0438\u0438."

    def _second_segment_line_ru(self, action_anchor: str, emotion_anchor: str) -> str:
        if self.framing_mode == VideoFramingMode.FACE_CLOSEUP:
            return f"\u0417\u0430\u0442\u0435\u043c \u043a\u0430\u043c\u0435\u0440\u0430 \u043c\u043e\u0436\u0435\u0442 \u043f\u043e\u0434\u043e\u0439\u0442\u0438 \u0431\u043b\u0438\u0436\u0435 \u043a {action_anchor} \u0438 \u043f\u0435\u0440\u0435\u0434\u0430\u0442\u044c \u0447\u0443\u0432\u0441\u0442\u0432\u043e {emotion_anchor} \u0447\u0435\u0440\u0435\u0437 \u0431\u043e\u043b\u0435\u0435 \u0438\u043d\u0442\u0438\u043c\u043d\u044b\u0439 \u043f\u043e\u0440\u0442\u0440\u0435\u0442\u043d\u044b\u0439 \u043c\u0430\u0441\u0448\u0442\u0430\u0431."
        if self.framing_mode == VideoFramingMode.AI_OPTIMAL:
            return f"\u0417\u0430\u0442\u0435\u043c \u043a\u0430\u043c\u0435\u0440\u0430 \u0443\u0441\u0438\u043b\u0438\u0432\u0430\u0435\u0442 \u0444\u043e\u043a\u0443\u0441 \u043d\u0430 {action_anchor} \u0438 \u0447\u0443\u0432\u0441\u0442\u0432\u043e {emotion_anchor}, \u0432\u044b\u0431\u0438\u0440\u0430\u044f \u043b\u044e\u0431\u043e\u0439 \u043c\u0430\u0441\u0448\u0442\u0430\u0431, \u0435\u0441\u043b\u0438 \u043e\u043d \u043b\u0443\u0447\u0448\u0435 \u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442 \u0434\u043b\u044f \u0441\u0446\u0435\u043d\u044b."
        return f"\u0417\u0430\u0442\u0435\u043c \u043a\u0430\u043c\u0435\u0440\u0430 \u0441\u043c\u0435\u0449\u0430\u0435\u0442 \u0444\u043e\u043a\u0443\u0441 \u043a {action_anchor} \u0438 \u0443\u0441\u0438\u043b\u0438\u0432\u0430\u0435\u0442 \u0447\u0443\u0432\u0441\u0442\u0432\u043e {emotion_anchor}, \u043d\u0435 \u0437\u0430\u0441\u0442\u0430\u0432\u043b\u044f\u044f \u043a\u0430\u0434\u0440 \u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c\u0441\u044f \u043a\u0440\u0443\u043f\u043d\u044b\u043c \u043b\u0438\u0446\u0435\u0432\u044b\u043c \u043f\u043e\u0440\u0442\u0440\u0435\u0442\u043e\u043c."

    def _single_motion_line_en(self, context_anchor: str, action_anchor: str) -> str:
        if self.framing_mode == VideoFramingMode.FACE_CLOSEUP:
            return f"The camera may move more boldly toward {action_anchor}, using a closer emotional scale and allowing the face to carry the shot."
        if self.framing_mode == VideoFramingMode.AI_OPTIMAL:
            return f"The camera path should find the strongest cinematic reading between {context_anchor} and {action_anchor}, even if that means a closer portrait scale."
        return f"The camera path should reveal {context_anchor} and then hold attention on {action_anchor} while keeping a respectful distance from visible faces."

    def _first_segment_line_en(self, context_anchor: str) -> str:
        if self.framing_mode == VideoFramingMode.FACE_CLOSEUP:
            return f"The camera first reveals {context_anchor}, but it may transition sooner into a closer emotional scale."
        if self.framing_mode == VideoFramingMode.AI_OPTIMAL:
            return f"The camera first reveals {context_anchor} in the most effective cinematic way, without being limited to identity-safe distance."
        return f"The camera first reveals {context_anchor} and the spatial context of the scene from an identity-safe distance."

    def _second_segment_line_en(self, action_anchor: str, emotion_anchor: str) -> str:
        if self.framing_mode == VideoFramingMode.FACE_CLOSEUP:
            return f"The camera may then move closer to {action_anchor} and let the feeling of {emotion_anchor} land through a more intimate portrait scale."
        if self.framing_mode == VideoFramingMode.AI_OPTIMAL:
            return f"The camera then intensifies focus on {action_anchor} and the feeling of {emotion_anchor}, using whatever scale best serves the scene."
        return f"The camera then shifts focus to {action_anchor} and intensifies the feeling of {emotion_anchor} without forcing a large facial close-up."

    def _build_style_edit_final_frame_prompt(
        self,
        *,
        prompt_index: int,
        total_videos: int,
        motions: list[str],
    ) -> str:
        transformation_lines = self._describe_visual_transformation(motions)
        return "\n".join(
            [
                f"????????? ???? {prompt_index}/{total_videos}:",
                "- ????????? ???????? ????????????? ????????? ??????????? ? ????? ????????? ????.",
                "- ???????? ???? ?????? ???? ?????????? ?? ????? A ?? ??????????, ???????? ??? ??????????? ???????.",
                "- ?????? ?????????? ????? ?????????? ????? ????????? ???????????.",
                *self._final_frame_scene_requirements(),
                *self._selfie_final_frame_lines_ru(),
                *self._loving_kindness_final_frame_lines_ru(),
                "- ??? ????? ???????? ???????????? ????? A:",
                *[f"- {line}" for line in transformation_lines],
                "- ????????? ????????????? ????????, ???? ? ????????????? ?????.",
                f"- ????????? ?????? ? ?????????: {self._format_description_ru()}.",
                f"- ????????? ????, ??????? ? ?????????: {self._brightness_ru()}, {self._palette_ru()}, {self._atmosphere_ru()}.",
                "- ???? ?????? ????????? ??? ????? ??????????? ????-????, ? ?? ??? ?????? ??????.",
                "- ??????????? ????????????????? ???????, 4K, ??????? ???????????.",
            ]
        )

    def _final_frame_scene_requirements(self) -> list[str]:
        lines = [f"- ??????? ?????: {self._composition_ru()}, {self._atmosphere_ru()}."]
        if not self.scene_analysis:
            return lines
        lines.append(f"- ????????? ??????? ?????: {self.scene_analysis.summary}")
        if self.scene_analysis.background:
            lines.append(f"- ????????? ?????????? ???: {self.scene_analysis.background}")
        if self.scene_analysis.main_action:
            lines.append(f"- ????????? ?????????? ????????: {self.scene_analysis.main_action}")
        for person in self.scene_analysis.people:
            bits = [person.label]
            if person.position_in_frame:
                bits.append(f"????????????: {person.position_in_frame}")
            if person.facial_expression:
                bits.append(f"????????? ????: {person.facial_expression}")
            if person.clothing:
                bits.append(f"??????: {person.clothing}")
            lines.append(f"- ????????? ????????????? ?????????: {'; '.join(bits)}")
        return lines

    def _selfie_phone_lines_ru(self) -> list[str]:
        if not self._should_hide_phone_in_selfie():
            return []
        return [
            "- \u0415\u0441\u043b\u0438 \u0438\u0441\u0445\u043e\u0434\u043d\u044b\u0439 \u043a\u0430\u0434\u0440 \u044f\u0432\u043b\u044f\u0435\u0442\u0441\u044f \u0441\u0435\u043b\u0444\u0438 \u0438\u043b\u0438 \u0430\u0432\u0442\u043e\u043f\u043e\u0440\u0442\u0440\u0435\u0442\u043e\u043c, \u0441\u043e\u0445\u0440\u0430\u043d\u0438 \u043e\u0449\u0443\u0449\u0435\u043d\u0438\u0435 \u0441\u0435\u043b\u0444\u0438, \u043d\u043e \u043f\u043e \u0432\u043e\u0437\u043c\u043e\u0436\u043d\u043e\u0441\u0442\u0438 \u043d\u0435 \u043f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0439 \u0442\u0435\u043b\u0435\u0444\u043e\u043d, \u0441\u043c\u0430\u0440\u0442\u0444\u043e\u043d, \u043e\u0442\u0440\u0430\u0436\u0435\u043d\u0438\u0435 \u0442\u0435\u043b\u0435\u0444\u043e\u043d\u0430 \u0438\u043b\u0438 \u0434\u0440\u0443\u0433\u043e\u0435 \u0432\u0438\u0434\u0438\u043c\u043e\u0435 \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u043e \u0441\u044a\u0435\u043c\u043a\u0438.",
            "- \u0415\u0441\u043b\u0438 \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u043e \u043f\u0435\u0440\u0435\u043a\u0440\u044b\u0432\u0430\u043b\u043e \u0447\u0430\u0441\u0442\u0438 \u043b\u0438\u0446\u0430, \u0440\u0443\u043a\u0438, \u0432\u043e\u043b\u043e\u0441, \u043e\u0434\u0435\u0436\u0434\u044b \u0438\u043b\u0438 \u0444\u043e\u043d\u0430, \u043f\u0440\u0430\u0432\u0434\u043e\u043f\u043e\u0434\u043e\u0431\u043d\u043e \u0432\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u0438 \u0441\u043a\u0440\u044b\u0442\u044b\u0435 \u0434\u0435\u0442\u0430\u043b\u0438.",
        ]

    def _selfie_phone_lines_en(self) -> list[str]:
        if not self._should_hide_phone_in_selfie():
            return []
        return [
            "- If the source image is a selfie or self-portrait, preserve that selfie-authored feeling but avoid showing the phone, smartphone, phone reflection, or any other visible recording device whenever plausible.",
            "- If the device originally covered parts of the face, hands, hair, clothing, or background, reconstruct those hidden details naturally and photorealistically.",
        ]

    def _selfie_final_frame_lines_ru(self) -> list[str]:
        if not self._should_hide_phone_in_selfie():
            return []
        return [
            "- \u0415\u0441\u043b\u0438 \u0438\u0441\u0445\u043e\u0434\u043d\u044b\u0439 \u043a\u0430\u0434\u0440 \u044f\u0432\u043b\u044f\u0435\u0442\u0441\u044f \u0441\u0435\u043b\u0444\u0438 \u0438\u043b\u0438 \u0430\u0432\u0442\u043e\u043f\u043e\u0440\u0442\u0440\u0435\u0442\u043e\u043c, \u0438\u0442\u043e\u0433\u043e\u0432\u044b\u0439 \u043a\u0430\u0434\u0440 \u0434\u043e\u043b\u0436\u0435\u043d \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u043e\u0449\u0443\u0449\u0435\u043d\u0438\u0435 \u0441\u0435\u043b\u0444\u0438, \u043d\u043e \u043f\u043e \u0432\u043e\u0437\u043c\u043e\u0436\u043d\u043e\u0441\u0442\u0438 \u043d\u0435 \u043f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0442\u044c \u0442\u0435\u043b\u0435\u0444\u043e\u043d, \u0441\u043c\u0430\u0440\u0442\u0444\u043e\u043d, \u043e\u0442\u0440\u0430\u0436\u0435\u043d\u0438\u0435 \u0442\u0435\u043b\u0435\u0444\u043e\u043d\u0430 \u0438\u043b\u0438 \u0434\u0440\u0443\u0433\u043e\u0435 \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u043e \u0441\u044a\u0435\u043c\u043a\u0438.",
            "- \u0415\u0441\u043b\u0438 \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u043e \u043f\u0435\u0440\u0435\u043a\u0440\u044b\u0432\u0430\u043b\u043e \u0447\u0430\u0441\u0442\u0438 \u043b\u0438\u0446\u0430, \u0440\u0443\u043a\u0438, \u0432\u043e\u043b\u043e\u0441, \u043e\u0434\u0435\u0436\u0434\u044b \u0438\u043b\u0438 \u0444\u043e\u043d\u0430, \u043f\u0440\u0430\u0432\u0434\u043e\u043f\u043e\u0434\u043e\u0431\u043d\u043e \u0432\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u0438 \u044d\u0442\u0438 \u0441\u043a\u0440\u044b\u0442\u044b\u0435 \u0437\u043e\u043d\u044b.",
        ]

    def _loving_kindness_video_lines_en(self) -> list[str]:
        if not self.prefer_loving_kindness_tone:
            return []
        return [
            "- Where it naturally fits the source image, let the light, color, environment, and background carry a delicate loving-kindness tone: friendliness, benevolence, warmth, goodwill, and gentle mercy.",
            "- Keep this subtle and realistic. Do not force sentimentality or overwrite the original scene; introduce it only through soft warmth, welcoming atmosphere, and calm supportive environmental cues.",
        ]

    def _loving_kindness_video_lines_ru(self) -> list[str]:
        if not self.prefer_loving_kindness_tone:
            return []
        return [
            "- \u0422\u0430\u043c, \u0433\u0434\u0435 \u044d\u0442\u043e \u0435\u0441\u0442\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u043e \u0434\u043b\u044f \u0438\u0441\u0445\u043e\u0434\u043d\u043e\u0433\u043e \u043a\u0430\u0434\u0440\u0430, \u043f\u0443\u0441\u0442\u044c \u0441\u0432\u0435\u0442, \u0446\u0432\u0435\u0442, \u0441\u0440\u0435\u0434\u0430 \u0438 \u0444\u043e\u043d \u0434\u0435\u043b\u0438\u043a\u0430\u0442\u043d\u043e \u043d\u0435\u0441\u0443\u0442 \u043e\u0449\u0443\u0449\u0435\u043d\u0438\u0435 \u043b\u044e\u0431\u044f\u0449\u0435\u0439 \u0434\u043e\u0431\u0440\u043e\u0442\u044b, \u0431\u043b\u0430\u0433\u043e\u0436\u0435\u043b\u0430\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u0438, \u0434\u0440\u0443\u0436\u0435\u043b\u044e\u0431\u0438\u044f \u0438 \u0442\u0451\u043f\u043b\u043e\u0433\u043e \u043c\u0438\u043b\u043e\u0441\u0435\u0440\u0434\u0438\u044f.",
            "- \u042d\u0442\u043e \u0434\u043e\u043b\u0436\u043d\u043e \u0431\u044b\u0442\u044c \u043c\u044f\u0433\u043a\u0438\u043c \u0438 \u0440\u0435\u0430\u043b\u0438\u0441\u0442\u0438\u0447\u043d\u044b\u043c: \u0431\u0435\u0437 \u043d\u0430\u0432\u044f\u0437\u0447\u0438\u0432\u043e\u0439 \u0441\u0435\u043d\u0442\u0438\u043c\u0435\u043d\u0442\u0430\u043b\u044c\u043d\u043e\u0441\u0442\u0438, \u0442\u043e\u043b\u044c\u043a\u043e \u0447\u0435\u0440\u0435\u0437 \u043d\u0435\u0436\u043d\u043e\u0435 \u0442\u0435\u043f\u043b\u043e \u0441\u0432\u0435\u0442\u0430, \u0431\u043e\u043b\u0435\u0435 \u0434\u0440\u0443\u0436\u0435\u043b\u044e\u0431\u043d\u0443\u044e \u0446\u0432\u0435\u0442\u043e\u0432\u0443\u044e \u0441\u0440\u0435\u0434\u0443 \u0438 \u0433\u043e\u0441\u0442\u0435\u043f\u0440\u0438\u0438\u043c\u043d\u043e\u0435 \u043e\u0449\u0443\u0449\u0435\u043d\u0438\u0435 \u043f\u0440\u043e\u0441\u0442\u0440\u0430\u043d\u0441\u0442\u0432\u0430.",
        ]

    def _loving_kindness_background_lines_en(self) -> list[str]:
        if not self.prefer_loving_kindness_tone:
            return []
        return [
            "Where appropriate for this exact frame, give the environment a subtle loving-kindness mood through warmer or softer light, more welcoming color balance, gentle air, and a benevolent real-world atmosphere.",
            "Keep this tone restrained, plausible, and secondary to the actual scene content.",
        ]

    def _loving_kindness_association_lines_en(self) -> list[str]:
        if not self.prefer_loving_kindness_tone:
            return []
        return [
            "Where suitable, let the associated environment suggest loving-kindness, friendliness, and warm goodwill through light, color harmony, and calm welcoming space.",
        ]

    def _loving_kindness_final_frame_lines_ru(self) -> list[str]:
        if not self.prefer_loving_kindness_tone:
            return []
        return [
            "- \u0422\u0430\u043c, \u0433\u0434\u0435 \u044d\u0442\u043e \u0435\u0441\u0442\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u043e \u0434\u043b\u044f \u0438\u0441\u0445\u043e\u0434\u043d\u043e\u0439 \u0441\u0446\u0435\u043d\u044b, \u0438\u0442\u043e\u0433\u043e\u0432\u044b\u0439 \u043a\u0430\u0434\u0440 \u043c\u043e\u0436\u0435\u0442 \u0434\u0435\u043b\u0438\u043a\u0430\u0442\u043d\u043e \u0443\u0432\u0435\u0441\u0442\u0438 \u0441\u0432\u0435\u0442, \u0446\u0432\u0435\u0442 \u0438 \u0441\u0440\u0435\u0434\u0443 \u0432 \u0441\u0442\u043e\u0440\u043e\u043d\u0443 \u043b\u044e\u0431\u044f\u0449\u0435\u0439 \u0434\u043e\u0431\u0440\u043e\u0442\u044b, \u0434\u0440\u0443\u0436\u0435\u043b\u044e\u0431\u0438\u044f \u0438 \u0431\u043b\u0430\u0433\u043e\u0436\u0435\u043b\u0430\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u0438.",
            "- \u0414\u0435\u043b\u0430\u0439 \u044d\u0442\u043e \u043c\u044f\u0433\u043a\u043e \u0438 \u0431\u0435\u0437 \u043d\u0430\u0440\u0443\u0448\u0435\u043d\u0438\u044f \u0440\u0435\u0430\u043b\u0438\u0437\u043c\u0430: \u0447\u0435\u0440\u0435\u0437 \u0442\u0451\u043f\u043b\u0443\u044e \u0441\u0432\u0435\u0442\u043e\u0432\u0443\u044e \u043d\u044e\u0430\u043d\u0441\u0438\u0440\u043e\u0432\u043a\u0443, \u0431\u043e\u043b\u0435\u0435 \u043c\u0438\u0440\u043d\u0443\u044e \u0446\u0432\u0435\u0442\u043e\u0432\u0443\u044e \u0433\u0430\u0440\u043c\u043e\u043d\u0438\u044e \u0438 \u0433\u043e\u0441\u0442\u0435\u043f\u0440\u0438\u0438\u043c\u043d\u043e\u0435 \u043e\u0449\u0443\u0449\u0435\u043d\u0438\u0435 \u0444\u043e\u043d\u0430.",
        ]

    def _should_hide_phone_in_selfie(self) -> bool:
        return self.hide_phone_in_selfie and self._is_selfie_scene()

    def _is_selfie_scene(self) -> bool:
        if not self.scene_analysis:
            return False

        haystack = " ".join(self._selfie_signal_texts()).lower()
        selfie_keywords = (
            "selfie",
            "mirror selfie",
            "self portrait",
            "self-portrait",
            "autoportrait",
            "\u0441\u0435\u043b\u0444\u0438",
            "\u0430\u0432\u0442\u043e\u043f\u043e\u0440\u0442\u0440\u0435\u0442",
            "\u0441\u0430\u043c\u043e\u043f\u043e\u0440\u0442\u0440\u0435\u0442",
            "\u0437\u0435\u0440\u043a\u0430\u043b\u044c\u043d\u043e\u0435 \u0441\u0435\u043b\u0444\u0438",
        )
        if any(keyword in haystack for keyword in selfie_keywords):
            return True

        if self.scene_analysis.people_count != 1:
            return False

        device_keywords = (
            "phone",
            "smartphone",
            "iphone",
            "mobile phone",
            "\u0442\u0435\u043b\u0435\u0444\u043e\u043d",
            "\u0441\u043c\u0430\u0440\u0442\u0444\u043e\u043d",
            "\u0430\u0439\u0444\u043e\u043d",
        )
        mirror_keywords = (
            "mirror",
            "\u0437\u0435\u0440\u043a\u0430\u043b",
        )
        self_capture_keywords = (
            "front camera",
            "self shot",
            "self-shot",
            "taking own photo",
            "\u0441\u043d\u0438\u043c\u0430\u0435\u0442 \u0441\u0435\u0431\u044f",
            "\u0434\u0435\u043b\u0430\u0435\u0442 \u0441\u0435\u043b\u0444\u0438",
            "\u0434\u0435\u0440\u0436\u0438\u0442 \u0442\u0435\u043b\u0435\u0444\u043e\u043d \u043f\u0435\u0440\u0435\u0434 \u0441\u043e\u0431\u043e\u0439",
        )
        return any(keyword in haystack for keyword in device_keywords) and (
            any(keyword in haystack for keyword in mirror_keywords)
            or any(keyword in haystack for keyword in self_capture_keywords)
        )

    def _selfie_signal_texts(self) -> list[str]:
        if not self.scene_analysis:
            return []

        texts = [
            self.scene_analysis.summary,
            self.scene_analysis.background,
            self.scene_analysis.shot_type,
            self.scene_analysis.main_action,
            *self.scene_analysis.mood,
            *self.scene_analysis.relationships,
        ]
        for person in self.scene_analysis.people:
            texts.extend(
                [
                    person.label,
                    person.position_in_frame,
                    person.role_in_scene,
                    person.apparent_age_group,
                    person.apparent_gender_presentation,
                    person.face_visibility,
                    person.facial_expression,
                    person.clothing,
                    person.pose,
                ]
            )
        return [text for text in texts if text]

    def _describe_visual_transformation(self, motions: list[str]) -> list[str]:
        descriptions = [self._motion_to_visual_change(motion) for motion in motions]
        merged: list[str] = []
        seen: set[str] = set()
        for description in descriptions:
            if description in seen:
                continue
            seen.add(description)
            merged.append(description)
        merged.extend(
            [
                "???????????????? ?????????? ?????? ???, ????? ??????? ????? ??? ??????????? ?????? ????? ????????????",
                "??????? ???? ????? ?????????????????, ???????? ?????????????? ??????? ???? ? ??????????",
                "????????????? ???????? ? ??????? ???, ????? ??????? ?????? ??????? ?????, ? ????????? ??? ?????? ?? ?????? ????",
                "????????? ???????????? ? ????, ?? ???????? ????? ?????????? ?????????? ??????, ????? ???? ????????????? ??? ?????",
            ]
        )
        return merged

    def _motion_to_visual_change(self, motion: str) -> str:
        lowered = motion.lower()
        if "zoom in" in lowered or "push" in lowered or "dolly in" in lowered or "close" in lowered or "???????" in lowered:
            return "??????????? ????????? ??????? ???????? ???????, ????? ???? ? ????????????? ????? ????? ?????"
        if "zoom out" in lowered or "pull" in lowered or "dolly out" in lowered or "wide" in lowered or "?????" in lowered:
            return "????????? ????? ?????, ????? ????????? ? ???????????????? ???????? ????? ????????"
        if "orbit" in lowered or "arc" in lowered or "pan" in lowered or "?????" in lowered or "???" in lowered:
            return "??????? ???? ?????????? ????? ???, ????? ??-?????? ???????? ????????? ????? ??????????? ? ???????? ???"
        if "crane" in lowered or "tilt" in lowered or "raise" in lowered or "drop" in lowered or "????" in lowered or "??????" in lowered:
            return "???????? ???????????? ?????? ??????????, ????? ????????? ???? ???? ?????? ? ?????????????"
        return "??????????? ???????? ?????????? ???, ????? ??? ?????????????? ??? ??????????????? ????????????????? ????"

    def _strip_camera_motion(self, prompt_text: str) -> str:
        filtered_lines = []
        skip_keywords = ("motion", "camera", "section", "??????", "????????", "????? ?????", "?????????????? ?????")
        for line in prompt_text.splitlines():
            lowered = line.lower()
            if any(keyword in lowered for keyword in skip_keywords):
                continue
            filtered_lines.append(line)
        return "\n".join(filtered_lines)

    def _translate_initial_frame_description(self, text: str) -> str:
        mapping = {
            "???? A (???????? ????)": "frame A (source frame)",
            "frame A (source frame)": "frame A (source frame)",
        }
        if text.startswith("final frame #"):
            return text
        return mapping.get(text, text)

    def _scene_analysis_text_en(self, text: str) -> str:
        return text

    def _format_description_ru(self) -> str:
        orientation_map = {
            "portrait": "???????????? ????",
            "landscape": "?????????????? ????",
            "square": "?????????? ????",
        }
        return f"{self.metadata.width}x{self.metadata.height}, {orientation_map.get(self.metadata.orientation, self.metadata.orientation)}"

    def _format_description_en(self) -> str:
        orientation_map = {
            "portrait": "vertical frame",
            "landscape": "horizontal frame",
            "square": "square frame",
        }
        return f"{self.metadata.width}x{self.metadata.height}, {orientation_map.get(self.metadata.orientation, self.metadata.orientation)}"

    def _composition_ru(self) -> str:
        mapping = {
            "subject-forward composition": "?????????? ? ???????? ?? ?????????",
            "environment-forward composition": "?????????? ? ???????? ?? ?????????",
            "detail-rich composition": "???????????????? ?????????? ??????????",
            "balanced center composition": "???????????????? ??????????? ??????????",
        }
        return mapping.get(self.metadata.composition_label, self.metadata.composition_label)

    def _composition_en(self) -> str:
        mapping = {
            "subject-forward composition": "subject-led composition",
            "environment-forward composition": "environment-led composition",
            "detail-rich composition": "detail-rich composition",
            "balanced center composition": "balanced central composition",
        }
        return mapping.get(self.metadata.composition_label, self.metadata.composition_label)

    def _brightness_ru(self) -> str:
        mapping = {
            "low-key": "?????? ????",
            "balanced": "???????????????? ????",
            "bright": "????? ????",
        }
        return mapping.get(self.metadata.brightness_label, self.metadata.brightness_label)

    def _brightness_en(self) -> str:
        mapping = {
            "low-key": "low-key lighting",
            "balanced": "balanced lighting",
            "bright": "bright lighting",
        }
        return mapping.get(self.metadata.brightness_label, self.metadata.brightness_label)

    def _contrast_ru(self) -> str:
        mapping = {
            "soft-contrast": "?????? ????????",
            "moderate-contrast": "????????? ????????",
            "high-contrast": "??????? ????????",
        }
        return mapping.get(self.metadata.contrast_label, self.metadata.contrast_label)

    def _contrast_en(self) -> str:
        mapping = {
            "soft-contrast": "soft contrast",
            "moderate-contrast": "moderate contrast",
            "high-contrast": "high contrast",
        }
        return mapping.get(self.metadata.contrast_label, self.metadata.contrast_label)

    def _palette_ru(self) -> str:
        mapping = {
            "muted neutral palette": "???????????? ??????????? ???????",
            "warm palette": "?????? ???????",
            "cool palette": "???????? ???????",
            "balanced natural palette": "???????????????? ??????????? ???????",
        }
        return mapping.get(self.metadata.palette_label, self.metadata.palette_label)

    def _palette_en(self) -> str:
        mapping = {
            "muted neutral palette": "muted neutral palette",
            "warm palette": "warm palette",
            "cool palette": "cool palette",
            "balanced natural palette": "balanced natural palette",
        }
        return mapping.get(self.metadata.palette_label, self.metadata.palette_label)

    def _depth_ru(self) -> str:
        mapping = {
            "soft layered depth": "?????? ???????????? ???????",
            "clear mid-depth separation": "?????? ??????? ?????????? ??????",
            "dense textured depth": "??????? ?????????? ???????",
        }
        return mapping.get(self.metadata.depth_label, self.metadata.depth_label)

    def _depth_en(self) -> str:
        mapping = {
            "soft layered depth": "soft layered depth",
            "clear mid-depth separation": "clear mid-depth separation",
            "dense textured depth": "dense textured depth",
        }
        return mapping.get(self.metadata.depth_label, self.metadata.depth_label)

    def _atmosphere_ru(self) -> str:
        mapping = {
            "warm open atmosphere": "?????? ???????? ?????????",
            "moody dramatic atmosphere": "??????????? ??????????? ?????????",
            "restrained reflective atmosphere": "?????????? ?????????????? ?????????",
            "grounded natural atmosphere": "???????????? ????????? ?????????",
        }
        return mapping.get(self.metadata.atmosphere_label, self.metadata.atmosphere_label)

    def _atmosphere_en(self) -> str:
        mapping = {
            "warm open atmosphere": "warm open atmosphere",
            "moody dramatic atmosphere": "moody dramatic atmosphere",
            "restrained reflective atmosphere": "restrained reflective atmosphere",
            "grounded natural atmosphere": "grounded natural atmosphere",
        }
        return mapping.get(self.metadata.atmosphere_label, self.metadata.atmosphere_label)
