"""桌宠提示音资源，由窗口播放和 WebUI 试听共用。"""

from pathlib import Path

SOUND_DIRECTORY = Path(__file__).parent / "web" / "sounds"
MESSAGE_SOUNDS = {
    "bubbles": "soft-bubbles.wav",
    "wood": "wooden-pop.wav",
    "bell": "warm-bell.wav",
    "water": "water-drop.wav",
}
