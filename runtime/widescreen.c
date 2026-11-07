// Widescreen (Ocarina of Time 1.1.1): the game draws a picture of another shape than 16:9, with the camera seeing more
// of the world at the sides (Hor+: the vertical view stays the game's) instead of a stretched 16:9 view.
//
// The game presents a 2560x1440 picture at every console mode and renders its scene at 1920x1080, which its upscaler
// takes to the picture's size; both sizes are constants in its code. The patch sets of recomp/patches-v111.txt read
// them from the patch page instead (wide_out: the picture, wide_scene: the scene), wide_aspect replaces a 16:9 ratio of
// sead's perspective projection (every 16:9 camera of the game) with the picture's, wide_hud keeps the HUD and menus
// in their 16:9 shape in the middle of the picture (the UI's full-screen projection squeezed horizontally; fades and
// the menu backgrounds that redraw a capture of the whole picture stay full width), and wide_movie shows pre-rendered
// movies in the same middle 16:9. The heights stay the game's (1440 and 1080), so only the width grows, by the shape's
// ratio to 16:9; the present scales the picture to the window keeping its shape (gfx_present.c, NX2NAVI_ASPECT=fit).
//
// Setting NX2NAVI_GAME_ASPECT, read at start: "16:9" (the game's own, every set off), "window" (the shape of the window,
// or of the monitor in fullscreen), or a fixed shape "21:9" (2560x1080 monitors, 64:27), "32:9". A shape within 1% of
// 16:9 or narrower than it keeps the game's own sizes; the widest is 32:9.
//
// The page's layout is described at the top of recomp/patches-v111.txt.
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cfg.h"
#include "gfx_wsys.h"
#include "host.h"
#include "layout.h"
#include "ui/menu.h"

#define GAME_W 2560   // the picture, main+0x1202ee0 and main+0x31f39f4
#define GAME_H 1440
#define SCENE_H 1080

// The window's size, or the monitor's in fullscreen; 0 when unknown.
static int screen_size(int *w, int *h) {
    *w = *h = 0;
    if (gfx_cfg_fullscreen()) return gfx_window_monitor_size(w, h) && *h > 0;
    gfx_cfg_window_size(w, h);
    return *h > 0;
}

// The shape the setting asks for as width / height; 0 for the game's own.
static double wanted_ratio(void) {
    const char *s = cfg_value("NX2NAVI_GAME_ASPECT");
    if (!strcmp(s, "21:9")) return 64.0 / 27.0;
    if (!strcmp(s, "32:9")) return 32.0 / 9.0;
    if (strcmp(s, "window")) return 0;
    int w, h;
    if (!screen_size(&w, &h)) {
        rt_log("widescreen: the screen's size is unknown; the game's own 16:9");
        return 0;
    }
    return (double)w / h;
}

// NX2NAVI_GAME_RES: the picture's height, from the game's 1440 up to 4320 ("window": the window's height, or the
// monitor's in fullscreen).
static unsigned picture_height(void) {
    const char *s = cfg_value("NX2NAVI_GAME_RES");
    int w = 0, h = GAME_H;
    if (!strcmp(s, "window")) {
        if (!screen_size(&w, &h)) h = GAME_H;
    } else {
        h = atoi(s);
    }
    return h < GAME_H ? GAME_H : h > 4320 ? 4320 : (unsigned)h;
}



// ---- DLSS's render scale (recomp/patches-v111.txt, set res_fixed)
// While DLSS is on, the scene is the DLSS mode's share of the picture (NVIDIA's ratios: DLAA all of it, Quality 2/3,
// Balanced 0.58, Performance 1/2, Ultra Performance 1/3) instead of what the game's dynamic-resolution controller
// picks. The share is written to +240 at every change of the setting (the menu switches the mode live); the set goes on
// RES_FIXED_AFTER presents after the first, once the controller has filled the fields the fixed path keeps.
#define RES_FIXED_AFTER 120
static double dlss_share(void) {
    if (!cfg_bool("NX2NAVI_DLAA")) return 0;
    const char *m = cfg_value("NX2NAVI_DLSS");
    return !strcmp(m, "quality") ? 2.0 / 3 : !strcmp(m, "balanced") ? 0.58 : !strcmp(m, "performance") ? 0.5
         : !strcmp(m, "ultra_performance") ? 1.0 / 3 : 1.0;
}

void wide_present(void) {
    static int set = -2;
    static uint64_t presents;
    static unsigned seen = ~0u;
    if (set == -2) set = rt_patch_set("res_fixed");
    if (set < 0) return;
    unsigned v = cfg_version();
    if (v != seen) {
        seen = v;
        float s = (float)dlss_share();
        if (s > 0) memcpy((uint8_t *)rt_patch_page() + 240, &s, 4);
        if (s <= 0 && rt_patch_enabled(set)) {
            rt_patch_switch(set, 0);
            rt_log("widescreen: DLSS off: the game's own render scale again");
        } else if (s > 0 && rt_patch_enabled(set)) {
            rt_log("widescreen: DLSS render scale %.0f%% of the picture", s * 100);
        }
    }
    if (++presents == RES_FIXED_AFTER && dlss_share() > 0) {
        rt_patch_switch(set, 1);
        rt_log("widescreen: DLSS render scale %.0f%% of the picture (the game's dynamic resolution off)", dlss_share() * 100);
    } else if (presents > RES_FIXED_AFTER && dlss_share() > 0 && !rt_patch_enabled(set)) {
        rt_patch_switch(set, 1);   // DLSS switched on later in the run
    }
}

void wide_init(void) {
    int so = rt_patch_set("wide_out"), ss = rt_patch_set("wide_scene"), sa = rt_patch_set("wide_aspect"),
        sh = rt_patch_set("wide_hud"), sm = rt_patch_set("wide_movie");
    // The scene's share of the picture is not a size in this set: the game's GPU-time dynamic resolution sets it each
    // frame (3/4 of the picture at most), and writing other sizes into wide_scene's constant or the 1920x1080 render
    // sizes left the light target where the controller put it. DLSS's modes fix the share through res_fixed instead
    // (wide_present).
    double r = wanted_ratio(), game = (double)GAME_W / GAME_H, f = 0.75;
    unsigned oh = picture_height();
    if (r <= 0 || r < game * 1.01) {
        if (r > 0) rt_log("widescreen: a %.4f picture is not wider than 16:9; the game's 16:9", r);
        r = game;
    }
    if (r == game && oh == GAME_H && f == 0.75) return;   // the game's own sizes: every set stays off
    if (so < 0 || ss < 0 || sa < 0 || sh < 0 || sm < 0) {
        rt_log("widescreen: the generated C has no widescreen patch sets (gen.py --patches recomp/patches-v111.txt); "
               "the game's own 2560x1440 picture");
        return;
    }
    if (r > 32.0 / 9.0) r = 32.0 / 9.0;
    // Widths in whole multiples of 8 pixels, the scene's height even: the picture oh high and as wide as its shape, the
    // scene 3/4 of both.
    unsigned ow = (unsigned)lround(oh * r / 8) * 8, sw = (unsigned)lround(ow * f / 8) * 8;
    unsigned sch = (unsigned)lround(oh * f / 2) * 2;
    uint64_t out = (uint64_t)oh << 32 | ow, scene = (uint64_t)sch << 32 | sw;
    float match = 16.0f / 9.0f, ratio = (float)ow / oh, tolerance = 0.01f, squeeze = match / ratio;
    float ui[2] = {2.0f / 3840.0f, 2.0f / 2160.0f}, squeeze4[4] = {squeeze, squeeze, squeeze, squeeze};
    float margin = (1.0f - squeeze) / 2;
    // the screens whose UI keeps the whole picture (recomp/patches-v111.txt, set wide_hud): the item-wheel, pause and
    // system menu backgrounds, the event and loading fades
    uint64_t keep[2] = {MOD_MAIN_BASE + 0x4bed098, MOD_MAIN_BASE + 0x4be4fb0},
             keep2[2] = {MOD_MAIN_BASE + 0x4be61c8, MOD_MAIN_BASE + 0x4be7b30};
    uint8_t *page = rt_patch_page();
    memcpy(page + 96, &out, 8);
    memcpy(page + 104, &scene, 8);
    memcpy(page + 112, &match, 4);
    memcpy(page + 116, &ratio, 4);
    memcpy(page + 120, &tolerance, 4);
    memcpy(page + 144, ui, 8);
    memcpy(page + 160, keep, 16);
    memcpy(page + 176, squeeze4, 16);
    memcpy(page + 192, &margin, 4);
    memcpy(page + 208, keep2, 16);
    rt_patch_switch(so, 1);
    rt_patch_switch(ss, 1);
    rt_patch_switch(sa, 1);
    rt_patch_switch(sh, 1);
    rt_patch_switch(sm, 1);
    rt_log("widescreen: picture %ux%u, scene %ux%u (%.0f%% of it), camera aspect ratio %.4f (16:9 is %.4f), UI and movies "
           "in the middle %.1f%%", ow, oh, sw, sch, f * 100, ratio, game, squeeze * 100);
}

// The generated C has the widescreen patch sets (the menu shows the row only then).
int wide_available(void) {
    static const char *const names[] = {"wide_out", "wide_scene", "wide_aspect", "wide_hud", "wide_movie"};
    for (int i = 0; i < 5; i++)
        if (rt_patch_set(names[i]) < 0) return 0;
    return 1;
}
