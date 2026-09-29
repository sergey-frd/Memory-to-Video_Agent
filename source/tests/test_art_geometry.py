"""No API: deterministic geometry regression checks against a fake backend."""
import base64,json
from io import BytesIO
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock
import pytest
from PIL import Image,ImageChops
from api import openai_image as backend
from utils.image_analysis import analyze_image

@pytest.mark.parametrize('size',[(120,240),(240,120),(160,160),(160,158)])
def test_reference_preserves_aspect_ratio(tmp_path,size):
 source=tmp_path/'source.png';dest=tmp_path/'reference.png'
 Image.new('RGB',size,'red').save(source)
 backend._save_compressed_png(source,dest)
 with Image.open(dest) as im:assert im.size==size

@pytest.mark.parametrize('orientation',[6,8])
def test_exif_is_normalized_in_reference_and_metadata(tmp_path,orientation):
 source=tmp_path/'source.jpg';dest=tmp_path/'reference.png'
 im=Image.new('RGB',(240,120),'blue');exif=im.getexif();exif[274]=orientation;im.save(source,exif=exif)
 backend._save_compressed_png(source,dest)
 with Image.open(dest) as im:assert im.size==(120,240) and im.getexif().get(274,1)==1
 assert (analyze_image(source).width,analyze_image(source).height)==(120,240)

@pytest.mark.parametrize('source_size,response_size',[( (120,240),(300,300)),((240,120),(300,200)),((160,158),(200,300))])
def test_backend_response_never_stretched_to_source(tmp_path,monkeypatch,source_size,response_size):
 source=tmp_path/'source.png';output=tmp_path/'art.png';Image.new('RGB',source_size,'red').save(source)
 result=Image.new('RGBA',response_size,'blue');result.putpixel((20,30),(255,255,0,255));buffer=BytesIO();result.save(buffer,format='PNG')
 fake=Mock();fake.images.edit.return_value=SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(buffer.getvalue()).decode())])
 monkeypatch.setattr(backend,'_get_client',lambda:fake)
 backend.edit_image_with_openai(source,'watercolor',output,analyze_image(source),'W01',model_name='gpt-image-1.5')
 with Image.open(output) as saved:assert saved.size==response_size and ImageChops.difference(saved,result).getbbox() is None
 assert fake.images.edit.call_args.kwargs['size']=='auto'
 proof=json.loads(output.with_suffix('.geometry.json').read_text(encoding='utf-8'))
 assert proof['postprocess_resize'] is False and proof['saved_size']==list(response_size)
 assert output.with_suffix('.response.png').read_bytes()==buffer.getvalue()
 assert output.with_suffix('.reference.png').exists()

def test_dalle_fixed_canvas_pads_without_stretch(tmp_path):
 source=tmp_path/'source.png';dest=tmp_path/'reference.png';Image.new('RGBA',(100,300),'red').save(source)
 backend._save_dalle2_uploadable_png(source,dest)
 with Image.open(dest) as im:
  assert im.size==(300,300) and im.getpixel((0,150))==(255,255,255,255) and im.getpixel((150,0))==(255,0,0,255)

def test_existing_art_and_raw_response_block_paid_retry(tmp_path,monkeypatch):
 source=tmp_path/'source.png';Image.new('RGB',(100,200)).save(source);output=tmp_path/'art.png'
 client=Mock(side_effect=AssertionError('Must not obtain API client'));monkeypatch.setattr(backend,'_get_client',client)
 for existing in [output,output.with_suffix('.response.png')]:
  existing.write_bytes(b'protected')
  with pytest.raises(FileExistsError):backend.edit_image_with_openai(source,'watercolor',output,analyze_image(source),'W01')
  assert existing.read_bytes()==b'protected';existing.unlink()
 client.assert_not_called()
