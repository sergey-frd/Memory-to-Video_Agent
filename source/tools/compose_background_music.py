"""Reproducible original instrumental sketch: JSON score, MIDI and stereo WAV."""
import argparse,json,math,struct,wave
from pathlib import Path
import numpy as np

def vlq(n):
    b=[n&127];n>>=7
    while n:b.insert(0,(n&127)|128);n>>=7
    return bytes(b)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output-dir',required=True);args=ap.parse_args()
    out=Path(args.output_dir);out.mkdir(parents=True,exist_ok=True)
    target=out/'Roman_Warm_Memories_v01.wav'
    if target.exists():raise RuntimeError('Output exists; refusing overwrite')
    sr=48000;bpm=80;beat=60/bpm;duration=174;notes=[]
    # 58 bars of 4/4 at 80 BPM = 174 seconds, including a two-bar final release.
    chords=[(48,[60,64,67,71]),(43,[59,62,67,69]),(45,[60,64,69,71]),(41,[57,60,64,67]),(50,[57,62,65,69]),(43,[59,62,67,69]),(48,[60,64,67,71]),(43,[59,62,67,69])]
    melody=[[76,74,72,71],[74,71,69,67],[72,76,79,76],[76,74,72,69],[74,77,76,74],[71,74,72,71],[72,76,74,72],[71,69,67,71]]
    def add(bar,pos,pitch,length,velocity,voice):notes.append(dict(beat=bar*4+pos,pitch=pitch,duration_beats=length,velocity=velocity,voice=voice))
    for bar in range(56):
        idx=(bar-4)%8 if bar>=4 else bar%4
        if bar>=52:idx=[4,5,0,0][bar-52]
        bass,chord=chords[idx]
        strength=.72 if bar<4 or bar>=52 else .9 if bar<20 else 1.0 if bar<36 else .86
        for n in chord:add(bar,0,n-12,4.6,round(36*strength),'pad')
        add(bar,0,bass-12,3.6,round(62*strength),'bass')
        if 4<=bar<52:add(bar,2,bass-12,1.6,round(45*strength),'bass')
        pattern=[0,2,1,3,2,1] if 20<=bar<36 else [0,2,1,3]
        positions=[0,.5,1.5,2,2.5,3.5] if len(pattern)==6 else [0,1,2,3]
        for j,(pos,k) in enumerate(zip(positions,pattern)):add(bar,pos,chord[k],.85,round((49 if j%2==0 else 40)*strength),'piano')
        if 4<=bar<52:
            phrase=melody[idx]
            for j,(pos,length) in enumerate([(0,1.35),(1.5,.75),(2.5,.7),(3.25,.65)]):
                if bar%4==3 and j>1:continue
                add(bar,pos,phrase[j]+(0 if bar<36 else -12),length,round((66-j*3)*strength),'melody')
        if 12<=bar<48:
            for pos in [1,3]:add(bar,pos,42,.16,20,'brush')
    for n in [48,60,64,67,72]:add(56,0,n,7.4,42,'piano')
    score={'title':'Warm Memories / Тёплые воспоминания','method':'original locally composed and synthesized instrumental; not neural music generation','bpm':bpm,'meter':'4/4','duration_seconds':duration,'sample_rate':sr,'brief_ru':'Тёплая семейная память, детство и встречи близких; светлая сдержанная музыка без вокала и военного пафоса.','notes':notes}
    (out/'score.json').write_text(json.dumps(score,ensure_ascii=False,indent=2),encoding='utf-8')
    audio=np.zeros((int(sr*duration),2),dtype=np.float32);rng=np.random.default_rng(36)
    for note in notes:
        voice=note['voice'];start=note['beat']*beat
        length=note['duration_beats']*beat
        tail=1.7 if voice in ('piano','melody') else 1.0 if voice=='pad' else .25
        size=min(int((length+tail)*sr),len(audio)-int(start*sr))
        if size<=0:continue
        t=np.arange(size,dtype=np.float32)/sr;f=440*2**((note['pitch']-69)/12)
        if voice=='pad':
            signal=(np.sin(2*np.pi*f*t)+.3*np.sin(2*np.pi*f*1.002*t)+.13*np.sin(4*np.pi*f*t))*.13
            env=np.minimum(t/.55,1)*np.exp(-np.maximum(t-length,0)*4)
        elif voice=='bass':
            signal=(np.sin(2*np.pi*f*t)+.18*np.sin(4*np.pi*f*t))*.24
            env=np.minimum(t/.035,1)*np.exp(-t/2)*np.exp(-np.maximum(t-length,0)*14)
        elif voice=='brush':
            noise=rng.standard_normal(size).astype(np.float32);signal=np.concatenate(([0],np.diff(noise)))*.012
            env=np.minimum(t/.006,1)*np.exp(-t*32)
        else:
            signal=(np.sin(2*np.pi*f*t+1.3*np.sin(2*np.pi*f*t)*np.exp(-t*5))+.15*np.sin(4*np.pi*f*t)*np.exp(-t*3))*.27
            env=np.minimum(t/.008,1)*np.exp(-t/(1.8 if voice=='melody' else 1.2))*np.exp(-np.maximum(t-length,0)*2.5)
        signal=signal*env*note['velocity']/90
        pan={'piano':-.22,'melody':.16,'pad':.32,'bass':0,'brush':-.35}[voice]
        offset=int(start*sr);audio[offset:offset+size,0]+=signal*math.sqrt((1-pan)/2);audio[offset:offset+size,1]+=signal*math.sqrt((1+pan)/2)
    dry=audio.copy()
    for delay,gain in [(.071,.08),(.139,.07),(.223,.055),(.347,.04),(.509,.025)]:
        d=int(delay*sr);audio[d:]+=dry[:-d,::-1]*gain
    del dry
    fade=int(sr*2);audio[:fade]*=np.linspace(0,1,fade,dtype=np.float32)[:,None]
    fade=int(sr*5);audio[-fade:]*=np.linspace(1,0,fade,dtype=np.float32)[:,None]
    peak=float(np.max(np.abs(audio)));rms=float(np.sqrt(np.mean(audio**2)))
    gain=min(.70/max(peak,1e-9),10**(-23/20)/max(rms,1e-9));audio*=gain
    pcm=(np.clip(audio,-1,1)*32767).astype('<i2')
    with wave.open(str(target),'wb') as w:w.setnchannels(2);w.setsampwidth(2);w.setframerate(sr);w.writeframes(pcm.tobytes())
    # MIDI retains the score with separate GM instruments; synth WAV uses custom timbres.
    tracks=[];tempo=b'\x00\xff\x51\x03'+int(60e6/bpm).to_bytes(3,'big')+b'\x00\xff\x58\x04\x04\x02\x18\x08\x00\xff\x2f\x00';tracks.append(tempo)
    for channel,(voice,program) in enumerate([('piano',4),('melody',4),('pad',89),('bass',33),('brush',118)]):
        events=[]
        for n in notes:
            if n['voice']==voice:
                events.extend([(round(n['beat']*480),bytes([0x90+channel,n['pitch'],n['velocity']])),(round((n['beat']+n['duration_beats'])*480),bytes([0x80+channel,n['pitch'],0]))])
        payload=b'\x00'+bytes([0xC0+channel,program]);last=0
        for tick,msg in sorted(events,key=lambda x:(x[0],x[1][0])):payload+=vlq(tick-last)+msg;last=tick
        tracks.append(payload+b'\x00\xff\x2f\x00')
    (out/'Roman_Warm_Memories_v01.mid').write_bytes(b'MThd'+struct.pack('>IHHH',6,1,len(tracks),480)+b''.join(b'MTrk'+struct.pack('>I',len(t))+t for t in tracks))
    stats={'duration_seconds':len(audio)/sr,'sample_rate':sr,'channels':2,'peak_dbfs':20*math.log10(float(np.max(np.abs(audio)))),'rms_dbfs':20*math.log10(float(np.sqrt(np.mean(audio**2)))),'clipped_samples':int(np.sum(np.abs(audio)>=1)),'finite':bool(np.isfinite(audio).all()),'note':'RMS is not LUFS; listening approval pending'}
    assert stats['clipped_samples']==0 and stats['finite']
    (out/'audio_qa.json').write_text(json.dumps(stats,indent=2),encoding='utf-8')
    (out/'README.md').write_text('# Музыкальная проба — Тёплые воспоминания\n\nОригинальная локально сочинённая и синтезированная инструментальная музыка, не результат нейросетевого музыкального сервиса. 80 BPM, 4/4, 2:54, stereo WAV 48 kHz. Для семейных сцен и воспоминаний Романа.\n\nИмпортировать WAV в Premiere на A1 с начала sequence. MIDI и score.json сохранены для изменения тембров и нот. Громкость окончательно согласовать при прослушивании с видео. Проект Premiere этим запуском не изменяется.\n',encoding='utf-8')
    print(json.dumps(stats))

if __name__=='__main__':main()
