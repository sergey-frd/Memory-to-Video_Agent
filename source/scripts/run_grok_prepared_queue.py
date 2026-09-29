"""Run a reviewed image/prompt manifest in the user's existing Chrome on 9222."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Settings, load_generation_config
from utils.grok_workspace import completed_image, digest, record_completed, save_state
from utils.project_delivery import resolve_delivery_dir
from utils.video_frame_extract import resolve_ffmpeg_executable


def archive_source(image, expected, archive):
    if digest(image) != expected:
        raise RuntimeError('Source changed before archive')
    source_copy = archive / f'{image.stem}_{expected[:10]}_source{image.suffix}'
    shutil.copy2(image, source_copy)
    if digest(source_copy) != expected:
        raise RuntimeError('Source archive hash mismatch')


def cleanup_inputs(settings, cleanup, delivery, enabled):
    if not enabled:
        return
    for image, expected in cleanup:
        if image.parent.resolve() == settings.input_dir.resolve() and image.exists() and digest(image) == expected:
            if completed_image(settings, image, delivery):
                image.unlink()


def run(plan_path: Path) -> None:
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    settings = Settings()
    if plan.get('input_dir'):
        settings.input_dir = Path(plan['input_dir']).resolve()
    config = load_generation_config(Path(plan['config_file']))
    delivery = resolve_delivery_dir(settings, config.final_videos_dir)
    archive = resolve_delivery_dir(settings, config.regeneration_assets_dir) / plan_path.parent.name
    delivery.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    shutil.copy2(plan_path, archive / 'plan.json')
    cleanup = []
    failures = []
    pending = []
    for item in plan['items']:
        image = Path(item['image'])
        if Path(item['output_name']).name != item['output_name'] or not item['output_name'].endswith('.mp4'):
            raise ValueError('Output filename must be a plain MP4 filename')
        if image.resolve().parent != settings.input_dir.resolve() or image.is_symlink():
            raise ValueError(f'Image outside configured input folder: {image}')
        if not image.exists():
            state_path = plan_path.parent / f'{image.stem}_{item["sha256"][:10]}.json'
            state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
            target = Path(state.get('delivered_video', ''))
            if state.get('status') == 'COMPLETE' and target.is_file() and digest(target) == state.get('video_sha256'):
                print(f'SKIP: delivered and removed from queue {image.name}', flush=True)
                continue
        if image.exists() and digest(image) == item['sha256'] and completed_image(settings, image, delivery):
            archive_source(image, item['sha256'], archive)
            cleanup.append((image, item['sha256']))
            print(f'SKIP: verified completed image {image.name}', flush=True)
        else:
            pending.append(item)
    if not pending:
        cleanup_inputs(settings, cleanup, delivery, plan.get('remove_completed_inputs', True))
        print(f'QUEUE FINISHED: {len(cleanup)} already delivered; no browser needed', flush=True)
        return
    from playwright.sync_api import sync_playwright
    ffmpeg = resolve_ffmpeg_executable()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(plan.get('cdp_url', 'http://127.0.0.1:9222'))
        pages = [p for c in browser.contexts for p in c.pages if p.url.startswith('https://grok.com/imagine')]
        if len(pages) != 1:
            raise RuntimeError(f'Expected one Grok Imagine tab on the configured Chrome endpoint; found {len(pages)}')
        page = pages[0]
        page.set_default_timeout(15000)
        for index, item in enumerate(pending, 1):
            image = Path(item['image'])
            state_path = plan_path.parent / f'{image.stem}_{item["sha256"][:10]}.json'
            state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
            print(f'[{index}/{len(plan["items"])}] {image.name}', flush=True)
            try:
                if not image.exists() and state.get('status') == 'COMPLETE':
                    target = Path(state['delivered_video'])
                    if target.is_file() and digest(target) == state['video_sha256']:
                        print('SKIP: already delivered and queue item removed', flush=True)
                        continue
                if digest(image) != item['sha256']:
                    raise RuntimeError('Input changed since prompt preparation')
                if completed_image(settings, image, delivery):
                    archive_source(image, item['sha256'], archive)
                    cleanup.append((image, item['sha256']))
                    print('SKIP: verified completed image', flush=True)
                    continue
                output = plan_path.parent / item['output_name']
                if output.exists():
                    check = subprocess.run([ffmpeg, '-v', 'error', '-i', str(output), '-f', 'null', '-'], capture_output=True, text=True)
                    if check.returncode or check.stderr.strip():
                        output.rename(output.with_suffix(f'.invalid-{time.time_ns()}'))
                        if not state.get('url'):
                            raise RuntimeError('Invalid download has no saved result URL')
                        state['status'] = 'SUBMITTED'
                        save_state(state_path, state)
                if not output.exists():
                    if state.get('status') == 'SUBMITTING':
                        raise RuntimeError('Interrupted submit needs review; refusing duplicate generation')
                    if state.get('status') == 'SUBMITTED':
                        page.goto(state['url'], wait_until='domcontentloaded')
                    else:
                        if not item['prompt'].strip():
                            raise RuntimeError('Previously delivered video disappeared; prepare a new prompt before generation')
                        previous = page.url
                        page.goto('https://grok.com/imagine', wait_until='domcontentloaded')
                        box = page.get_by_role('textbox', name='Ask Grok anything')
                        box.wait_for()
                        page.get_by_role('radio', name='Video', exact=True).check()
                        assert page.get_by_role('button', name='Remove image', exact=True).count() == 0
                        box.fill('')
                        duration = page.get_by_role('button', name='Video duration', exact=True)
                        if '6s' not in duration.inner_text():
                            raise RuntimeError('Expected 6s duration; configure it before retry')
                        resolution = page.get_by_role('button', name='Video resolution', exact=True)
                        if '720p' not in resolution.inner_text():
                            raise RuntimeError('Expected 720p; configure it before retry')
                        audio = page.get_by_role('button', name='Video audio', exact=True)
                        if audio.get_attribute('aria-pressed') == 'true':
                            audio.click()
                        page.locator('input[type=file][name=files]').set_input_files(str(image))
                        remove = page.get_by_role('button', name='Remove image', exact=True)
                        remove.wait_for(state='visible', timeout=plan.get('upload_timeout', 300) * 1000)
                        page.wait_for_function('''() => {
                            const button = document.querySelector('button[aria-label="Remove image"]');
                            let el = button;
                            for (let n=0; el && n<4; n++, el=el.parentElement) {
                                if (Array.from(el.querySelectorAll('img')).some(i=>i.complete && i.naturalWidth>0)) return true;
                            }
                            return false;
                        }''', timeout=plan.get('upload_timeout', 300) * 1000)
                        box.fill(item['prompt'])
                        assert box.inner_text().strip() == item['prompt'].strip()
                        page.screenshot(path=str(archive / f'{image.stem}_prepared.png'))
                        state.update(status='SUBMITTING', input_sha256=item['sha256'], previous_url=previous,
                                     reset_verified=True, prompt=item['prompt'])
                        save_state(state_path, state)
                        page.get_by_role('button', name='Submit', exact=True).click()
                        page.wait_for_url('**/imagine/post/**', timeout=30000)
                        state.update(status='SUBMITTED', url=page.url)
                        save_state(state_path, state)
                        print('SUBMITTED; old player closed and fresh image attached', flush=True)
                    deadline = time.monotonic() + plan.get('generation_timeout', 600)
                    post_id = state['url'].split('/post/', 1)[1].split('?', 1)[0]
                    while time.monotonic() < deadline:
                        # Grok first opens the source-image post, then replaces it
                        # with the generated-video post in the same conversation.
                        if '/imagine/post/' in page.url and page.url != state['url']:
                            if page.get_by_role('textbox', name='Ask Grok anything').inner_text().strip() != item['prompt'].strip():
                                raise RuntimeError('Page changed to a different request')
                            state['url'] = page.url
                            post_id = page.url.split('/post/', 1)[1].split('?', 1)[0]
                            save_state(state_path, state)
                        videos = page.locator('main video').evaluate_all('(els)=>els.map(e=>({src:e.currentSrc,duration:e.duration,width:e.videoWidth,height:e.videoHeight}))')
                        ready = next((v for v in videos if post_id in v['src'] and v['duration'] and v['width']), None)
                        if ready:
                            state['video'] = ready
                            break
                        print('Waiting: ' + page.locator('main').inner_text()[:180].replace('\n', ' '), flush=True)
                        page.wait_for_timeout(10000)
                    else:
                        raise TimeoutError('Generation did not finish; resume uses saved post URL')
                    download = page.get_by_role('button', name='Download', exact=True)
                    if not download.count():
                        page.get_by_role('button', name='Post actions', exact=True).click()
                    try:
                        # Download only the URL observed on this request's player.
                        response = page.context.request.get(state['video']['src'], timeout=120000)
                        if not response.ok:
                            raise RuntimeError(f'Video download failed: HTTP {response.status}')
                        output.write_bytes(response.body())
                    except Exception:
                        with page.expect_download(timeout=60000) as info:
                            download.click()
                        info.value.save_as(str(output))
                    state['status'] = 'DOWNLOADED'
                    save_state(state_path, state)
                    page.screenshot(path=str(archive / f'{image.stem}_result.png'))
                result = subprocess.run([ffmpeg, '-v', 'error', '-i', str(output), '-f', 'null', '-'], capture_output=True, text=True)
                if result.returncode or result.stderr.strip():
                    raise RuntimeError(f'Video decode failed: {result.stderr}')
                subprocess.run([ffmpeg, '-v', 'error', '-i', str(output), '-vf',
                    "select='eq(n,0)+eq(n,72)+eq(n,143)',scale=272:-1,tile=3x1", '-frames:v', '1', '-y',
                    str(archive / f'{image.stem}_qa.jpg')], check=True)
                target = delivery / output.name
                if target.exists() and digest(target) != digest(output):
                    raise RuntimeError('Refusing to overwrite different delivered video')
                shutil.copy2(output, target)
                if digest(output) != digest(target):
                    raise RuntimeError('Delivery hash mismatch')
                # Keep a verified source copy before removing this queue item.
                archive_source(image, item['sha256'], archive)
                record_completed(settings, image, delivery, [target])
                state.update(status='COMPLETE', delivered_video=str(target), video_sha256=digest(target))
                save_state(state_path, state)
                shutil.copy2(state_path, archive / state_path.name)
                cleanup.append((image, item['sha256']))
                print(f'COMPLETE: {target}', flush=True)
            except Exception as exc:
                state['last_error'] = str(exc)
                save_state(state_path, state)
                failures.append(image.name)
                print(f'FAILED: {image.name}: {exc}', flush=True)
                # An interrupted submission must not be retried automatically.
                if state.get('status') == 'SUBMITTING':
                    break
        # End the queue on a fresh form rather than leave the last video looping.
        page.goto('https://grok.com/imagine', wait_until='domcontentloaded')
        page.get_by_role('textbox', name='Ask Grok anything').wait_for()
        page.screenshot(path=str(archive / 'queue_finished.png'))
    cleanup_inputs(settings, cleanup, delivery, plan.get('remove_completed_inputs', True))
    for item in plan['items']:
        output = plan_path.parent / item['output_name']
        target = delivery / output.name
        if output.is_file() and target.is_file() and digest(output) == digest(target):
            output.unlink()
    print(f'QUEUE FINISHED: {len(cleanup)} completed/skipped, {len(failures)} failures', flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('plan', type=Path)
    run(parser.parse_args().plan.resolve())
