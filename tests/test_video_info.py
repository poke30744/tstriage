from tstriage.video_info import VideoInfo


def test_video_info_creation():
    info = VideoInfo(
        duration=3600.5,
        width=1920,
        height=1080,
        fps=29.97,
        sar=(1, 1),
        dar=(16, 9),
        soundTracks=2,
        serviceId=1024,
    )
    assert info.duration == 3600.5
    assert info.width == 1920
    assert info.height == 1080
    assert info.fps == 29.97
    assert info.sar == (1, 1)
    assert info.dar == (16, 9)
    assert info.soundTracks == 2
    assert info.serviceId == 1024


def test_video_info_defaults():
    info = VideoInfo(0, 0, 0, 0.0, (0, 0), (0, 0), 0, 0)
    assert info.duration == 0
    assert info.sar == (0, 0)
