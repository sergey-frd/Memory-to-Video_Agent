from pathlib import Path
from uuid import uuid4

from main_scene import run_scene_analysis
from models.scene_analysis import PersonInFrame, SceneAnalysis


def test_run_scene_analysis_writes_json_and_text_outputs() -> None:
    root = Path("test_runtime") / f"scene_app_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    image_path = root / "frame.png"
    image_path.write_bytes(b"fake-image")

    def fake_analyzer(_image_path: Path, _model: str | None) -> SceneAnalysis:
        return SceneAnalysis(
            summary="Два друга стоят рядом на городской улице.",
            people_count=2,
            people=[
                PersonInFrame(
                    label="человек слева",
                    position_in_frame="слева",
                    role_in_scene="вероятный друг",
                    apparent_age_group="молодой взрослый",
                    apparent_gender_presentation="маскулинная подача",
                    face_visibility="лицо видно полностью",
                    facial_expression="лёгкая расслабленная улыбка",
                    clothing="тёмная куртка",
                    pose="стоит прямо, плечи слегка повернуты внутрь",
                ),
                PersonInFrame(
                    label="человек справа",
                    position_in_frame="справа",
                    role_in_scene="вероятная подруга",
                    apparent_age_group="молодой взрослый",
                    apparent_gender_presentation="феминная подача",
                    face_visibility="лицо видно полностью",
                    facial_expression="спокойный прямой взгляд",
                    clothing="светлое пальто",
                    pose="стоит прямо, голова слегка наклонена",
                ),
            ],
            background="слегка размытая городская стена и дверной проём",
            shot_type="средний портретный план",
            main_action="два человека позируют рядом и смотрят в камеру",
            mood=["тёплое", "интимное"],
            relationships=["похожие на близких друзей с расслабленным доверием"],
        )

    json_output = root / "scene_analysis.json"
    txt_output = root / "scene_report.txt"
    json_path, txt_path = run_scene_analysis(
        image_path,
        analyzer=fake_analyzer,
        output_json=json_output,
        output_txt=txt_output,
    )

    json_text = json_path.read_text(encoding="utf-8")
    txt_text = txt_path.read_text(encoding="utf-8")

    assert '"people_count": 2' in json_text
    assert '"background": "слегка размытая городская стена и дверной проём"' in json_text
    assert "НАБЛЮДАЕМОЕ ОПИСАНИЕ КАДРА" in txt_text
    assert "1. Сколько людей в кадре" in txt_text
    assert "- человек слева (слева) - вероятный друг" in txt_text


def test_run_scene_analysis_uses_custom_stage_id() -> None:
    root = Path("test_runtime") / f"scene_app_stage_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    image_path = root / "frame.png"
    image_path.write_bytes(b"fake-image")

    def fake_analyzer(_image_path: Path, _model: str | None) -> SceneAnalysis:
        return SceneAnalysis(summary="single portrait", people_count=1)

    json_output = root / "custom_analysis.json"
    txt_output = root / "custom_report.txt"
    json_path, txt_path = run_scene_analysis(
        image_path,
        stage_id="custom_stage",
        analyzer=fake_analyzer,
        output_json=json_output,
        output_txt=txt_output,
    )

    assert json_path.name == "custom_analysis.json"
    assert txt_path.name == "custom_report.txt"
