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

        let side_usize = usize::try_from(side).ok()?;
        let pixels = side_usize.checked_mul(side_usize)?;
        let needed = pixels.checked_add(2)?;
        out.try_reserve(needed).ok()?;
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
    // Compile-time check so transmute_copy can never read past the raw pointer size or violate alignment.
    const {
        assert!(std::mem::size_of::<T>() == std::mem::size_of::<*mut c_void>());
        assert!(std::mem::align_of::<T>() == std::mem::align_of::<*mut c_void>());
    }
    let ptr = unsafe { dlsym(lib, name.as_ptr()) };
    if ptr.is_null() { None } else { Some(unsafe { std::mem::transmute_copy(&ptr) }) }
}

/// RAII guard for a `dlopen` handle so `dlclose` runs on every exit path, including unwind.
struct DlHandle(*mut c_void);

impl Drop for DlHandle {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: `self.0` came from a successful `dlopen` and has not been closed yet.
            unsafe {
                dlclose(self.0);
            }
        }
    }
}

/// RAII guard for an owned `XOpenDisplay` connection (dropped before `DlHandle` in reverse decl order).
struct OwnedDisplay {
    display: *mut c_void,
    close: XCloseDisplayFn,
}

impl Drop for OwnedDisplay {
    fn drop(&mut self) {
        if !self.display.is_null() {
            // SAFETY: `self.display` came from `XOpenDisplay` and `libX11` is still held by `DlHandle`.
            unsafe {
                (self.close)(self.display);
            }
        }
    }
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
    // valid X11 handles on the winit event-loop thread. `lib` is dropped after `_owned_display`.
    unsafe {
        let raw_lib = dlopen(c"libX11.so.6".as_ptr(), RTLD_LAZY | RTLD_NOLOAD);
        if raw_lib.is_null() {
            return;
        }
        let lib = DlHandle(raw_lib);
        let _ = (|| -> Option<()> {
            let x_intern_atom: XInternAtomFn = load_sym(lib.0, c"XInternAtom")?;
            let x_change_property: XChangePropertyFn = load_sym(lib.0, c"XChangeProperty")?;
            let x_flush: XFlushFn = load_sym(lib.0, c"XFlush")?;

            let _owned_display: Option<OwnedDisplay>;
            let display = match borrowed_display {
                Some(d) => {
                    _owned_display = None;
                    d
                }
                None => {
                    let x_open: XOpenDisplayFn = load_sym(lib.0, c"XOpenDisplay")?;
                    let x_close: XCloseDisplayFn = load_sym(lib.0, c"XCloseDisplay")?;
                    let d = x_open(ptr::null());
                    if d.is_null() {
                        return None;
                    }
                    _owned_display = Some(OwnedDisplay { display: d, close: x_close });
                    d
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
            let wm_cap = app_id.len().checked_mul(2)?.checked_add(2)?;
            let mut wm_class = Vec::new();
            wm_class.try_reserve_exact(wm_cap).ok()?;
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
            Some(())
        })();
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
