from dataclasses import dataclass


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float
    sar: tuple[int, int]
    dar: tuple[int, int]
    soundTracks: int
    serviceId: int
