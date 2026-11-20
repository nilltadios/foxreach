# Window icons

Drawn for this app on a 16 px grid, one colour, and licensed under the repository's MIT licence;
none is taken from an icon set. The app compiles them in and draws them symbolic (`src/app.rs`,
`ui_icon`), so they take the text colour on every desktop instead of depending on the icon theme.

| File | Where |
|---|---|
| `warning-symbolic.svg` | "agents use another foxreach" (20 px), "Not recommended for agents" (16 px), the Remove and Connect anyway dialogs (64 px) |
| `offline-symbolic.svg` | no browser connected (32 px) |
| `profile-symbolic.svg` | a browser profile row (16 px) |
| `more-symbolic.svg` | a profile's menu button (16 px) |
| `refresh-symbolic.svg` | Refresh in the header (16 px) |
| `search-symbolic.svg`, `clear-symbolic.svg` | the search fields (16 px) |
| `window-minimize-symbolic.svg`, `window-maximize-symbolic.svg`, `window-restore-symbolic.svg` (maximized window only), `window-close-symbolic.svg` | the header bar's window buttons (16 px) |
| `window-close-symbolic.svg` | also the close button of an error banner and of a toast |
| `go-previous-symbolic.svg`, `go-next-symbolic.svg` | the tab bar's scroll arrows, when the window is narrower than its tabs (16 px) |

The last six are drawn by libcosmic's own widgets, which look them up by name in the icon theme
and take no picture from the app. The app writes them out at start as a small private icon theme
(`src/window_icons.rs`: `$XDG_DATA_HOME/dev.nilltadios.FoxreachManager/icons/`, in the Flatpak
under `~/.var/app/dev.nilltadios.FoxreachManager/data/`) and points libcosmic at it; every other
name libcosmic asks for still comes from the desktop's theme, which the private one inherits.
Their file names are the icon names the lookup asks for, so keep them.
