"""Fixed TASK_032 helper. Requires an explicit local config and --execute."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(args):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from tools.task032_preflight import OUT
    if not args.preset or not args.preset.is_file():raise ValueError('--preset must name an installed Adobe H.264 .epr preset')
    r=ET.parse(args.preset)
    changes={'ADBEVideoWidth':'640','ADBEVideoHeight':'360','ADBEVideoFPS':'10160640000','ADBEVideoAspect':'1,1',
             'ADBEVideoBitrateEncoding':'1','ADBEVideoTargetBitrate':'5.','ADBEVideoMaxBitrate':'8.',
             'ADBEMPEGVideoEncodingPerformanceParam':'0','ADBEAudioBitrate':'192'}
    for n in r.iter('ExporterParam'):
        key=n.findtext('ParamIdentifier')
        if key in changes:
            n.find('ParamValue').text=changes[key]
            if n.find('ParamIsDisabled') is not None: n.find('ParamIsDisabled').text='false'
    r.find('PresetName').text='TASK_032 H264 640x360 25fps'
    r.find('StandardFilters/UsePreview').text='false'
    r.write(OUT/'TASK_032_H264_640_360_25.epr',encoding='utf-8',xml_declaration=True)


if __name__ == '__main__':
    from utils.premiere_art_runtime import tool_entry
    tool_entry('032', main)
