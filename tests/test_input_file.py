from pathlib import Path
from unittest.mock import patch, MagicMock
from tstriage.input_file import InputFile
from tstriage.video_info import VideoInfo


def test_input_file_init():
    with patch('shutil.which', return_value='/usr/bin/ffmpeg'):
        f = InputFile("test.ts")
    assert f.path == Path("test.ts")
    assert f.ffmpeg == '/usr/bin/ffmpeg'
    assert f.ffprobe == '/usr/bin/ffmpeg'


def test_get_info():
    mock_probe = {
        'streams': [
            {
                'codec_type': 'video',
                'duration': '3600.5',
                'width': 1920,
                'height': 1080,
                'avg_frame_rate': '30000/1001',
                'sample_aspect_ratio': '1:1',
                'display_aspect_ratio': '16:9',
            },
            {'codec_type': 'audio'},
            {'codec_type': 'audio'},
        ],
        'programs': [{'program_id': 1024, 'nb_streams': 3}],
    }
    with patch('ffmpeg.probe', return_value=mock_probe):
        info = InputFile("test.ts").GetInfo()
    assert isinstance(info, VideoInfo)
    assert info.duration == 3600.5
    assert info.width == 1920
    assert info.height == 1080
    assert info.soundTracks == 2
    assert info.serviceId == 1024
    assert info.sar == (1, 1)
    assert info.dar == (16, 9)


def test_strip_ts_cmd_basic():
    f = InputFile("test.ts")
    cmd = f.StripTsCmd("in.ts", "out.ts")
    assert "ffmpeg" in cmd[0]
    assert "-c:v" in cmd
    assert "copy" in cmd
    assert "out.ts" in cmd


def test_strip_ts_cmd_fix_audio():
    f = InputFile("test.ts")
    cmd = f.StripTsCmd("in.ts", "out.ts", fixAudio=True)
    assert "aresample=async=1" in cmd
    assert "-c:a" in cmd
    assert "aac" in cmd


def test_strip_ts_cmd_nomap():
    f = InputFile("test.ts")
    cmd = f.StripTsCmd("in.ts", "out.ts", noMap=True)
    assert "-map" not in cmd


def test_encode_ts_cmd_basic():
    f = InputFile("test.ts")
    preset = {'crf': 23, 'videoFilter': ''}
    cmd = f.EncodeTsCmd("in.ts", "out.mkv", preset=preset, encoder="libx264")
    assert "ffmpeg" in cmd[0]
    assert "-c:v" in cmd
    assert "libx264" in cmd
    assert "out.mkv" in cmd


def test_encode_ts_cmd_with_video_filter():
    f = InputFile("test.ts")
    preset = {'crf': 23, 'videoFilter': 'yadif=1'}
    cmd = f.EncodeTsCmd("in.ts", "out.mkv", preset=preset, encoder="libx264")
    assert "-vf" in cmd
    assert "yadif=1" in cmd


def test_encode_ts_cmd_with_crop():
    f = InputFile("test.ts")
    preset = {'crf': 23, 'videoFilter': ''}
    crop = {'w': 1440, 'h': 1080, 'x': 240, 'y': 0, 'dar': (16, 9), 'sar': (1, 1)}
    cmd = f.EncodeTsCmd("in.ts", "out.mkv", preset=preset, encoder="libx264", crop=crop)
    cmd_str = ' '.join(cmd)
    assert 'crop=1440:1080:240:0' in cmd_str


def test_encode_ts_cmd_nvenc():
    f = InputFile("test.ts")
    preset = {'crf': 27, 'videoFilter': ''}
    cmd = f.EncodeTsCmd("in.ts", "out.mkv", preset=preset, encoder="h264_nvenc")
    assert 'h264_nvenc' in cmd
    assert '-cq:v' in cmd
    assert '31' in cmd  # 27 + 4


def test_encode_ts_cmd_dual_mono():
    f = InputFile("test.ts")
    preset = {'crf': 23, 'videoFilter': ''}
    audio_config = [{'componentType': 2, 'samplingRate': 48000, 'langs': ['jpn', 'eng']}]
    cmd = f.EncodeTsCmd("in.ts", "out.mkv", preset=preset, encoder="libx264", audio_config=audio_config)
    cmd_str = ' '.join(cmd)
    assert 'channelsplit' in cmd_str
    assert 'language=jpn' in cmd_str
    assert 'language=eng' in cmd_str
