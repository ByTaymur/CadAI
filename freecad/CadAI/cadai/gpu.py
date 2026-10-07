"""GPU facts about FreeCAD's own 3D view (Coin3D on OpenGL), for the VS Code extension's GPU diagnostics.

Vendor-neutral: reports which GPU the OpenGL driver runs on (AMD, NVIDIA, Intel, Apple, Qualcomm, software…) and
the view settings that matter for speed. The extension decides what to recommend (cadai-vscode/gpu.js).
"""

import FreeCAD

VIEW = "User parameter:BaseApp/Preferences/View"
OPENGL = "User parameter:BaseApp/Preferences/OpenGL"
_opengl = {}


def opengl_info():
    """{"vendor", "renderer", "version"} of a fresh OpenGL context, or None without a GUI (freecadcmd)."""
    if _opengl:
        return dict(_opengl)
    try:
        from PySide import QtGui
    except ImportError:
        return None
    if QtGui.QGuiApplication.instance() is None:
        return None  # headless FreeCAD: no OpenGL at all, nothing to report
    previous = QtGui.QOpenGLContext.currentContext()
    surface = QtGui.QOffscreenSurface()
    surface.create()
    ctx = QtGui.QOpenGLContext()
    if not ctx.create() or not ctx.makeCurrent(surface):
        return None
    try:
        f = ctx.functions()
        _opengl.update(vendor=f.glGetString(0x1F00), renderer=f.glGetString(0x1F01), version=f.glGetString(0x1F02))
    finally:
        ctx.doneCurrent()
        surface.destroy()
        if previous is not None and previous.surface() is not None:
            previous.makeCurrent(previous.surface())
    return dict(_opengl)


def view_settings():
    view, gl = FreeCAD.ParamGet(VIEW), FreeCAD.ParamGet(OPENGL)
    return {"use_vbo": view.GetBool("UseVBO", False), "render_cache": view.GetInt("RenderCache", 0),
            "anti_aliasing": view.GetInt("AntiAliasing", 0), "software_opengl": gl.GetBool("UseSoftwareOpenGL", False)}


def apply_recommended(vbo=True):
    """Hardware OpenGL, and vertex buffer objects (geometry stays in GPU memory) when `vbo` — the extension asks for
    them only on a real GPU with OpenGL 3+, since old, software and virtual drivers can draw VBOs wrongly.
    Both take effect after a restart."""
    before = view_settings()
    if vbo:
        FreeCAD.ParamGet(VIEW).SetBool("UseVBO", True)
    FreeCAD.ParamGet(OPENGL).SetBool("UseSoftwareOpenGL", False)
    after = view_settings()
    return {"changed": sorted(k for k in after if after[k] != before[k]), "settings": after,
            "note": "FreeCAD yeniden başlatılınca etkinleşir."}


def info():
    return {"opengl": opengl_info(), "settings": view_settings(), "freecad": ".".join(FreeCAD.Version()[:3])}
