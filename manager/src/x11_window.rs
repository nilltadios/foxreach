// SPDX-License-Identifier: MIT
//! Set `_NET_WM_ICON` and `WM_CLASS` on X11 windows.
//!
//! `iced_winit` wraps window icons in a private `RawImage` type that `winit-x11` fails to downcast
//! to `RgbaIcon` (leaving `_NET_WM_ICON` empty), and overwrites `WindowAttributesX11` with
//! `WindowAttributesWayland` when both backends are enabled (leaving `WM_CLASS` as the executable
//! name instead of `StartupWMClass`). Setting both properties directly through the already-loaded
//! `libX11.so.6` (`RTLD_NOLOAD`) fixes X11 taskbars, panels and window switchers while doing
//! nothing on Wayland.

use cosmic::iced::window::raw_window_handle::{
    HasDisplayHandle, HasWindowHandle, RawDisplayHandle, RawWindowHandle,
};
use std::ffi::{CStr, c_char, c_int, c_uchar, c_ulong, c_void};
use std::ptr;

const RTLD_LAZY: c_int = 1;
const RTLD_NOLOAD: c_int = 4;
const XA_CARDINAL: c_ulong = 6;
const XA_STRING: c_ulong = 31;
const XA_WM_CLASS: c_ulong = 67;
const PROP_MODE_REPLACE: c_int = 0;

unsafe extern "C" {
    fn dlopen(filename: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
    fn dlclose(handle: *mut c_void) -> c_int;
}

type XOpenDisplayFn = unsafe extern "C" fn(*const c_char) -> *mut c_void;
type XCloseDisplayFn = unsafe extern "C" fn(*mut c_void) -> c_int;
type XInternAtomFn = unsafe extern "C" fn(*mut c_void, *const c_char, c_int) -> c_ulong;
type XChangePropertyFn = unsafe extern "C" fn(
    *mut c_void,
    c_ulong,
    c_ulong,
    c_ulong,
    c_int,
    c_int,
    *const c_uchar,
    c_int,
) -> c_int;
type XFlushFn = unsafe extern "C" fn(*mut c_void) -> c_int;

/// Rasterize the bundled app SVG at each requested square size into the EWMH `_NET_WM_ICON`
/// layout (`[width, height, 0xAARRGGBB, ...]`).
///
/// Xlib `XChangeProperty` with `format = 32` expects an array of `c_ulong`, even on 64-bit
/// platforms where each element carries one 32-bit cardinal.
pub fn ewmh_icon_cardinals(sizes: &[u32]) -> Option<Vec<c_ulong>> {
    const SVG: &[u8] = include_bytes!("../resources/dev.nilltadios.FoxreachManager.svg");
    let tree = resvg::usvg::Tree::from_data(SVG, &resvg::usvg::Options::default()).ok()?;
    let src = tree.size();
    let mut out = Vec::new();
    for &side in sizes {
        let mut pixmap = resvg::tiny_skia::Pixmap::new(side, side)?;
        let scale = (side as f32 / src.width()).min(side as f32 / src.height());
        let tx = (side as f32 - src.width() * scale) * 0.5;
        let ty = (side as f32 - src.height() * scale) * 0.5;
        let transform = resvg::tiny_skia::Transform::from_scale(scale, scale).post_translate(tx, ty);
        resvg::render(&tree, transform, &mut pixmap.as_mut());

        out.reserve(2 + (side as usize) * (side as usize));
        out.push(c_ulong::from(side));
        out.push(c_ulong::from(side));
        for px in pixmap.pixels() {
            if px.alpha() == 0 {
                out.push(0);
            } else {
                let c = px.demultiply();
                let argb = (u32::from(c.alpha()) << 24)
                    | (u32::from(c.red()) << 16)
                    | (u32::from(c.green()) << 8)
                    | u32::from(c.blue());
                out.push(c_ulong::from(argb));
            }
        }
    }
    Some(out)
}

unsafe fn load_sym<T: Copy>(lib: *mut c_void, name: &CStr) -> Option<T> {
    let ptr = unsafe { dlsym(lib, name.as_ptr()) };
    if ptr.is_null() { None } else { Some(unsafe { std::mem::transmute_copy(&ptr) }) }
}

/// Apply `_NET_WM_ICON` and `WM_CLASS` when `window` is backed by X11.
/// Does nothing on Wayland or any non-X11 backend.
pub fn apply_icon_and_class<W>(window: &W, app_id: &str)
where
    W: HasWindowHandle + HasDisplayHandle + ?Sized,
{
    let xwindow: c_ulong = match window.window_handle().map(|h| h.as_raw()) {
        Ok(RawWindowHandle::Xlib(h)) => h.window,
        Ok(RawWindowHandle::Xcb(h)) => c_ulong::from(h.window.get()),
        _ => return,
    };
    let borrowed_display: Option<*mut c_void> = match window.display_handle().map(|h| h.as_raw()) {
        Ok(RawDisplayHandle::Xlib(h)) => h.display.map(std::ptr::NonNull::as_ptr),
        _ => None,
    };

    // SAFETY: Looks up symbols only in an already-loaded libX11.so.6 (RTLD_NOLOAD) and passes
    // valid X11 handles on the winit event-loop thread.
    unsafe {
        let lib = dlopen(c"libX11.so.6".as_ptr(), RTLD_LAZY | RTLD_NOLOAD);
        if lib.is_null() {
            return;
        }
        let result = (|| -> Option<()> {
            let x_intern_atom: XInternAtomFn = load_sym(lib, c"XInternAtom")?;
            let x_change_property: XChangePropertyFn = load_sym(lib, c"XChangeProperty")?;
            let x_flush: XFlushFn = load_sym(lib, c"XFlush")?;

            let (display, owned) = match borrowed_display {
                Some(d) => (d, false),
                None => {
                    let x_open: XOpenDisplayFn = load_sym(lib, c"XOpenDisplay")?;
                    let d = x_open(ptr::null());
                    if d.is_null() {
                        return None;
                    }
                    (d, true)
                }
            };

            if let Some(cardinals) = ewmh_icon_cardinals(&[128, 48])
                && let Ok(len) = c_int::try_from(cardinals.len())
            {
                let net_wm_icon = x_intern_atom(display, c"_NET_WM_ICON".as_ptr(), 0);
                if net_wm_icon != 0 {
                    x_change_property(
                        display,
                        xwindow,
                        net_wm_icon,
                        XA_CARDINAL,
                        32,
                        PROP_MODE_REPLACE,
                        cardinals.as_ptr().cast::<c_uchar>(),
                        len,
                    );
                }
            }

            // ICCCM WM_CLASS: two NUL-terminated strings: instance\0class\0.
            let mut wm_class = Vec::with_capacity(app_id.len() * 2 + 2);
            wm_class.extend_from_slice(app_id.as_bytes());
            wm_class.push(0);
            wm_class.extend_from_slice(app_id.as_bytes());
            wm_class.push(0);
            if let Ok(len) = c_int::try_from(wm_class.len()) {
                x_change_property(
                    display,
                    xwindow,
                    XA_WM_CLASS,
                    XA_STRING,
                    8,
                    PROP_MODE_REPLACE,
                    wm_class.as_ptr(),
                    len,
                );
            }

            x_flush(display);
            if owned && let Some(x_close) = load_sym::<XCloseDisplayFn>(lib, c"XCloseDisplay") {
                x_close(display);
            }
            Some(())
        })();
        let _ = result;
        dlclose(lib);
    }
}

#[cfg(test)]
mod tests {
    use super::ewmh_icon_cardinals;

    #[test]
    fn ewmh_icon_encodes_requested_sizes_with_expected_pixels() {
        let data = ewmh_icon_cardinals(&[128, 48]).expect("bundled SVG renders");
        assert_eq!(data.len(), (2 + 128 * 128) + (2 + 48 * 48));
        assert_eq!(data[0], 128);
        assert_eq!(data[1], 128);
        assert_eq!(data[2 + 128 * 128], 48);
        assert_eq!(data[2 + 128 * 128 + 1], 48);
        // Top-left corner outside the fox head is transparent.
        assert_eq!(data[2], 0, "corner pixel should be transparent");
        // Center pixel (64, 64) maps to SVG (24, 24), which is inside the red forehead (#ff0000).
        let center = data[2 + 64 * 128 + 64];
        assert_eq!(center >> 24, 0xff, "center pixel should be opaque");
        assert!((center >> 16) & 0xff > 0xe0, "center pixel should be red, got {center:#010x}");
        assert!((center >> 8) & 0xff < 0x20, "center pixel should not be green, got {center:#010x}");
        assert!(center & 0xff < 0x20, "center pixel should not be blue, got {center:#010x}");
    }
}
