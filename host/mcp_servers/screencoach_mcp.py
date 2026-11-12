"""ScreenCoach MCP server: capture the foreground window and guide with the overlay."""
from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from host.mcp_servers import _common as C
from host.overlay import client as overlay_client
from host.screencoach_impl import Coach

mcp = FastMCP("nyro-screencoach")
_coach: Coach | None = None


def coach() -> Coach:
    global _coach
    if _coach is None:
        cfg = C.config()
        from host.overlay.capture import capture_foreground_scaled
        from host.voice.tts import speak
        v = cfg["vision"]
        _coach = Coach(
            C.data_dir(),
            capture_fn=lambda: capture_foreground_scaled(v["target_width"], v["webp_quality"]),
            draw_fn=overlay_client.draw, clear_fn=overlay_client.clear,
            speak_fn=lambda t: speak(t, cfg["voice"]["voices"]) if t else None,
            click_timeout=cfg["screencoach"]["click_timeout_seconds"],
        )
    return _coach


@mcp.tool()
def capture_foreground_window() -> str:
    """Screenshot the active window, downscaled to 800px WebP. Returns a file path (use Read on it)
    and its size. All coordinates you later return must be in THIS image's pixel space."""
    try:
        C.guard("capture_foreground_window")
        return json.dumps(coach().capture())
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
def render_screen_annotations(annotations_json: str, voice_script: str = "",
                              auto_advance_on_click: bool = False) -> str:
    """Draw guidance on the user's screen using coordinates from the last capture.

    annotations_json is either a list of annotations, or {"steps": [ {title, speech,
    annotations:[...], click_box:[x,y,w,h], recapture_after:bool}, ... ]}.
    Annotation types: cursor_point{x,y,label}, box_highlight{x,y,w,h,label,color},
    arrow_connect{x1,y1,x2,y2,label}, freehand_circle{cx,cy,rx,ry,label}. Colors: red,cyan,yellow,green.
    With auto_advance_on_click=true the tool waits for the user to click the target of each step
    and moves on by itself (no extra AI calls). Set recapture_after=true on a step whose click
    changes the screen layout: the tool then returns a fresh screenshot so you can plan the rest.
    Speak in the user's language in voice_script/speech."""
    try:
        C.guard("render_screen_annotations")
        return coach().render(annotations_json, voice_script, auto_advance_on_click)
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
def clear_annotations() -> str:
    """Remove everything drawn on the screen."""
    try:
        return coach().clear()
    except Exception as e:
        return C.as_error(e)


if __name__ == "__main__":
    mcp.run()
