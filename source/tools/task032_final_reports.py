"""Fixed TASK_032 helper. Requires an explicit local config and --execute."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(args):
    from pathlib import Path
    import sys,json,shutil,zipfile,datetime,subprocess
    import cv2,numpy as np
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import tools.task032_pipeline as pipeline
    globals().update({k:v for k,v in vars(pipeline).items() if not k.startswith('_')})
    from tools.task032_pipeline import _validate_all_refs

    m=json.loads((OUT/'TASK_032_MASTER.json').read_text(encoding='utf8'));qa=json.loads((OUT/'TASK_032_FINAL_QA_MEASUREMENTS.json').read_text(encoding='utf8'));native=json.loads((OUT/'TASK_032_NATIVE_REOPEN_READBACK.json').read_text(encoding='utf8'));process=json.loads((OUT/'TASK_032_OPEN_CHECK_PROCESS.json').read_text(encoding='utf8'))
    assert m['revision']==3 and all(qa['checks'].values()) and process['new_process'] and process['revision']==3
    assert sha(SOURCE)==SHA and sha(BACKUP)==SHA and sha(DEST)==qa['output_sha256'] and sha(PREVIEW)==qa['preview_sha256']
    assert native['project']==str(DEST) and native['status']=='PASS'
    assert not [c for c in native['clips'] if c['offline'] or c['media_exists'] is False]
    root=load_premiere_project_root(DEST);ids=build_project_object_id_lookup(root);v=items(root,TARGET);a=items(root,TARGET,1)
    source_root=load_premiere_project_root(SOURCE)
    def signature(i):return (i.track_index,i.name,i.start,i.end,i.source_in,i.source_out)
    assert sorted(signature(i) for i in items(source_root,NAME))==sorted(signature(i) for i in items(root,NAME))
    assert sorted(signature(i) for i in items(source_root,NAME,1))==sorted(signature(i) for i in items(root,NAME,1))
    kept=[o for o in m['edit_operations'] if o['type']!='REMOVE']
    for o in kept:
        ci=o['clip_identity'];actual=[i for i in v if i.track_index==ci['track'] and i.start//FT==o['output_in'] and i.name==ci['name']];assert len(actual)==1
        i=actual[0];assert i.end//FT==o['output_out'] and i.source_in//FT==o['new_source_in'] and i.source_out//FT==o['new_source_out']
    color_values=[]
    for o in m['extreme_color_operations']:
        i=next(i for i in v if i.track_index==o['track'] and i.start//FT==o['output_in']);lum=[co for _,co in comps(i,ids) if co.findtext('MatchName')=='AE.ADBE Lumetri'];assert len(lum)==1
        ps=params(lum[0],ids);values={key:float(ps[PID[key]].findtext('StartKeyframe').split(',')[1]) for key in o['after']};assert all(abs(values[k]-value)<.001 for k,value in o['after'].items());color_values.append({'source_clip_id':o['source_clip_id'],'actual':values,'postcondition':'PASS'})
    motion_values=[]
    for o in m['strong_animation_operations']:
        c=next(c for c in native['clips'] if not c['audio'] and c['sequence']==TARGET and c['track']==o['track'] and int(c['start'])==o['output_in']*FT)
        motion=next(co for co in c['components'] if co['name']=='Motion');ps={p['name']:p for p in motion['parameters']};sk=ps['Scale']['keys'];pk=ps['Position']['keys'];assert len(sk)==len(pk)==2
        assert abs(sk[0]['value']-o['scale_start'])<.001 and abs(sk[-1]['value']-o['scale_end'])<.001
        for index,k in enumerate([o['position_start'],o['position_end']]):assert all(abs(x-y)<1e-5 for x,y in zip(pk[index]['value'],k))
        assert all(x['entire_source_inside'] for x in o['safe_crop_calculation']['samples'])
        motion_values.append({'source_clip_id':o['source_clip_id'],'native_scale':sk,'native_position':pk,'postcondition':'PASS'})
    scopes=json.loads((OUT/'TASK_032_SCOPE_MEASUREMENTS.json').read_text(encoding='utf8'));max_clip=max(max(c['channel_ge254_percent']) for c in scopes['clips'])
    struct={'status':'PASS','revision':3,'source_sha256':sha(SOURCE),'backup_sha256':sha(BACKUP),'output_sha256':sha(DEST),'master_sha256':sha(OUT/'TASK_032_MASTER.json'),'duration_frames':3480,'duration_seconds':139.2,'fps':25,'sequence_frame_size':[3840,2160],'preview_frame_size':[640,360],'references_checked':_validate_all_refs(root),'source_sequence_timeline_and_source_bounds_preserved':True,'native_media_online':True,'native_open_check_new_process':process,'editorial_operations_verified':len(kept),'native_motion_operations_verified':len(motion_values),'lumetri_operations_verified':len(color_values),'foreground_clips':len(v)-1,'audio_clips':len(a),'native_effects_editable':True,'checks':qa['checks']}
    dump('TASK_032_STRUCTURAL_QA.json',struct)
    dump('TASK_032_EXTREME_COLOR_PLAN.json',m['extreme_color_operations']);dump('TASK_032_BACKGROUND_DESIGN.json',{'operations':m['unified_background_operations'],'sequence':m['background_sequence']})
    dump('TASK_032_STRONG_ANIMATION_APPLY_LOG.json',{'initial_apply':json.loads((OUT/'revision_01/TASK_032_STRONG_ANIMATION_APPLY_LOG.json').read_text(encoding='utf8')),'native_readback_after_reopen':motion_values})
    dump('TASK_032_EXTREME_COLOR_APPLY_LOG.json',{'initial_apply':json.loads((OUT/'revision_01/TASK_032_EXTREME_COLOR_APPLY_LOG.json').read_text(encoding='utf8')),'revision_02':json.loads((OUT/'TASK_032_REVISION_02_APPLY_LOG.json').read_text(encoding='utf8')),'revision_03':(OUT/'TASK_032_REVISION_03_APPLY_LOG.txt').read_text(encoding='utf8'),'final_actual_values':color_values})
    dump('TASK_032_BACKGROUND_APPLY_LOG.json',{'initial':json.loads((OUT/'revision_01/TASK_032_BACKGROUND_APPLY_LOG.json').read_text(encoding='utf8')),'native_tint':json.loads((OUT/'TASK_032_NATIVE_BACKGROUND_APPLY_LOG.json').read_text(encoding='utf8')),'revision_02':json.loads((OUT/'TASK_032_REVISION_02_APPLY_LOG.json').read_text(encoding='utf8')),'final_contract':m['background_sequence']})
    dump('TASK_032_AUDIO_APPLY_LOG.json',{'initial':json.loads((OUT/'revision_01/TASK_032_AUDIO_APPLY_LOG.json').read_text(encoding='utf8')),'native_limiter':m['audio_operations'][0]['limiter'],'final_measurement':qa['loudness'],'postcondition':'PASS: LUFS / True Peak'})
    write('TASK_032_STRONG_ANIMATION_QA.txt',f'PASS. {len(motion_values)} самостоятельных движений подтверждены native readback после перезапуска. Zoom 6–12%, pan 4–6%; короткие кадры используют нижнюю часть диапазона. Проверены старт/середина/конец; всё исходное изображение внутри кадра. Начало/середина/конец каждого движения в TASK_032_MOTION_ENDPOINTS_*.jpg. Готовые видео не получили дополнительной анимации. Исходные обрезы внутри файлов не дорисовывались.\n')
    write('TASK_032_BACKGROUND_QA.txt','PASS технической проверки. Независимая nested TASK032_BRONZE_OCHRE_BACKGROUND. Scale to Fill внутри, Gaussian Blur 600 с Repeat Edge Pixels снаружи, бронзово-охристый Tint и изменение экспозиции к финалу. Фоновая копия не имеет новых Motion главного слоя. Чёрные поля прозрачной скульптуры устранены Alpha Adjust только на фоновом источнике. В R01 большие узнаваемые силуэты мешали — в R02 уменьшен контраст палитры и усилено размытие. На полноэкранных видео фон не виден и не создаётся специально. Основные изображения остаются резкими. Оценка художественной интенсивности — за Музой.\n')
    write('TASK_032_EXTREME_COLOR_QA.txt',f'PASS технической проверки с оговорками исходного материала. {len(color_values)} поклипных Lumetri сверены с JSON. Проверены native кадры, FFmpeg Waveform Y, RGB Parade, Vectorscope с ориентиром skin-tone line. После scopes в R03 защищены света и поднят black floor. На середине каждого клипа максимальная доля одного RGB-канала >=254 составляет {max_clip:.4f}%; это пороговый показатель 8-bit preview, а не утверждение о нулевом числе предельных пикселей. Массовые пересветы мониторов и заката подавлены; утраченная в оригинале информация не восстанавливается. Бронзовые рельефы читаются, Матисс получил меньшую коррекцию, живописная стилизация кожи сохранена без общего оранжевого фильтра. Цвет намеренно выразительный; вся новая коррекция редактируема.\n')
    audio=qa['loudness']
    write('TASK_032_AUDIO_QA.txt',f"PASS объективного измерения всего MP4. Integrated {audio['input_i']} LUFS; True Peak {audio['input_tp']} dBTP; LRA {audio['input_lra']} LU. Исходник: -18.05 LUFS / -1.38 dBTP / LRA20.10; сокращённый первый вариант: -19.94 LUFS. Добавлены штатные Hard Limiter с Input Boost +2.7 dB, True Peak -1.5 dB, look-ahead7ms, release100ms, linked stereo. Фортепианная динамика сохранена (LRA18.9), музыка не заменена, финальный fade80 кадров/3.2с. Полное декодирование успешно. Автоматические измерения и воспроизведение не заменяют субъективное прослушивание человеком: отсутствие слышимого pumping и естественность музыкального завершения окончательно проверяет Муза.\n")
    write('TASK_032_PREMIERE_OPEN_CHECK.txt',f"PASS Desktop open-check для R03. Premiere26.3.2 закрыт (PID {process['closed_pid']}) и повторно запущен (PID {process['reopened_pid']}). Проект: {DEST}\nSequence: {TARGET}\nФайл открыт без диалога missing media / missing filters. Native readback: {len(native['clips'])} использований клипов в финальной и фоновой sequence, offline отсутствуют; эффекты и Motion прочитаны после перезапуска. Доказательства: TASK_032_OPEN_CHECK_PROCESS.json, TASK_032_NATIVE_REOPEN_READBACK.json, TASK_032_PREMIERE_REOPEN.png. Старт/конец воспроизведения фиксируются отдельно.\n")
    write('TASK_032_VISUAL_AUDIO_QA.txt','Проверены все 139.2 секунды через контактные листы native MP4 с шагом1с; для каждой анимации дополнительно старт/середина/конец. Всё видео полностью декодировано; blackdetect не нашёл чёрных интервалов. Карта монтажа исключает дырки и однокадровые вставки. Смысловая дуга: молодость → мастерская/чеканка → художественные сопоставления → цифровые портреты → современный Сергей/камера → реальный видеомонтаж → Нури → свет. Содержание компьютерных кадров проверено непосредственно; это не музей. Движение ясно видно по последовательным кадрам. Лица и произведения сохраняются целиком в границах исходных изображений; присутствующие внутри оригинальных фото/видео обрезы не создавались заново. Контрольные scopes и объективная аудиометрия приложены. Непрерывное воспроизведение Premiere запущено после повторного открытия; отметки окончания — в TASK_032_NATIVE_PLAYBACK_POSITION.json. Агент не приписывает себе субъективное прослушивание человеком: окончательная звуковая и художественная приёмка Музы ещё не получена. Поэтому DONE не создаётся.\n')
    write('TASK_032_FINAL_REPORT.txt',f'''TASK_032 — окончательная техническая версия R03, ожидает художественной приёмки Музы.

    Результат: отдельный Premiere-проект и sequence, полный штатный H.264/AAC preview640×360,25fps,139.2с/3480 кадров. Исходник160.24с сокращён на21.04с. Убрана подтверждённая чёрная пауза11.24с, три повторные/слишком короткие вставки; вступление уплотнено. Компьютерный блок сохранён как реальный видеомонтаж — продолжение творчества.

    {len(motion_values)} самостоятельных анимаций с zoom6–12% и pan4–6%. {len(color_values)} поклипных Lumetri. Фон: отдельная nested, Blur600, приглушённая бронза/охра с высветлением к финалу. Движения, Lumetri, Tint, Blur, Alpha Adjust, музыка и Hard Limiter редактируются в Premiere; исходные готовые видео остаются видео.

    Звук: {audio['input_i']} LUFS; {audio['input_tp']} dBTP; LRA {audio['input_lra']} LU. Музыка сохранена, fade3.2с. StructuralQA, native readback, повторное открытие Premiere и полное декодирование пройдены. После scopes выполнена отдельная R03 защита светов/теней. Восстановить отсутствующие в исходниках детали светов невозможно. Уровень выразительности оставлен сознательно высоким.

    JSON был создан до мутаций; исходный полный dry-run и первая редакция находятся в revision_01. Дополнительные изменения выполнялись только после новых JSON и PASS; R02 сохранена в revision_02. Архивы содержат проекты, preview, контракты и проверки. Ни один входной файл не перезаписан. Конечные значения сверены с MASTER и native readback.

    ПРОЕКТ: {DEST}
    SEQUENCE: {TARGET}
    PREVIEW: {PREVIEW}
    BACKUP: {BACKUP}
    CHECKPOINT: {CHECKPOINT}
    ОТЧЁТЫ: {REPORT_DIR}
    COMPARISON: {REPORT_DIR/'TASK_032_COMPARISON_1280_400_R03.mp4'} (11.2с, без звука; основной preview со звуком).
    Google Drive: загрузка проверяется отдельно; этот отчёт её не подтверждает.
    Проверяемые сведения о загрузке: TASK_032_DRIVE_UPLOAD_LOG.json.

    ОГРАНИЧЕНИЕ ПРИЁМКИ: технический анализ аудио и воспроизведение выполнены, субъективное прослушивание человеком и художественное одобрение Музы не подтверждены. Нельзя считать фильм принятым; TASK_032_DONE.txt не создаётся. Если отдельный эффект слишком силён, уменьшите соответствующий параметр клипа или отключите его fx; для фона откройте TASK032_BRONZE_OCHRE_BACKGROUND/эффекты внешнего nested.
    ''')
    dump('TASK_032_DELIVERABLE_CHECKSUMS.json',[{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)} for p in [SOURCE,BACKUP,CHECKPOINT,DEST,PREVIEW,OUT/'TASK_032_MASTER.json',OUT/'TASK_032_COMPARISON_1280_400_R03.mp4']])
    print('Reports and structural/native validation PASS')


if __name__ == '__main__':
    from utils.premiere_art_runtime import tool_entry
    tool_entry('032', main)
