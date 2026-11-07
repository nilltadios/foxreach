# foxreach and Claude in Chrome: A/B results

Run 2026-11-06 on the same machine with the same pages and tasks.

- **foxreach:** LibreWolf 159.0.1, measured on the final build with
  [foxreach_side.py](foxreach_side.py) and [test/e2e.py](../e2e.py). The last run is saved in
  [foxreach_side.last.txt](foxreach_side.last.txt).
- **Claude in Chrome:** driven by hand through its own tools, in one new tab, closed afterwards. Those
  calls exist only in the session transcript and can't be rerun as a script.
- **Pages:** [serve_pages.py](serve_pages.py), plus Wikipedia and Hacker News, read-only.

## Where Claude in Chrome won, and what changed

| Task | Claude in Chrome | foxreach before | foxreach now |
|---|---|---|---|
| Interactive read of Wikipedia "Red fox" | 5,071 chars, viewport only | 167,312 chars | 5,447 chars; `scope: page` reads it all |
| Interactive read of Hacker News | 9,218 chars | 14,949 chars | 7,760 chars |
| Plain-English `find`: "the field for typing your name" | Found it, with explanations | Nothing | Found it (no language model) |
| HTML5 drag-and-drop | Drop completed | `dragstart` only | Drop completed, trusted |
| JS last expression: `const a = 2; a * 3` | 6 | undefined | 6 |
| JS top-level `await` with a last expression | 6 | undefined | 6 |
| Drop a file on a drop zone | Screenshot only (`upload_image`) | No tool | Any file; `screenshot save_to` covers screenshots |
| Several actions in one round trip | `browser_batch` | No tool | `batch` |
| Record actions as a GIF | `gif_creator` (not run: it downloads a file) | No tool | `record` |
| Truncation | At a line, states the full size | Mid-line | At a line, states the full size |
| Viewport size in `read_page` | Yes | No | Yes |
| Console `pattern` and `clear` | Regex, clear | Substring | Regex, clear |
| `navigate` with no tab id | Opens a tab | Error | Opens a tab |
| Scroll returns a screenshot | Yes | No | Yes; `screenshot: false` leaves it out |
| Pointer drawn in screenshots | Yes | No | Yes, including regions, scaling and cross-site frames; `show_pointer: false` leaves it out |

## Where foxreach won, before any changes

| Task | Claude in Chrome | foxreach |
|---|---|---|
| Works with any agent | Claude only | Claude Code and Codex both tested |
| Open source; no account | No | Yes |
| Closed shadow DOM | Not read (`read_page`) | Read and clicked |
| Reading a cross-site iframe | Empty result | Read, with refs |
| Link names | Many blank, e.g. Wikipedia's contents | Named, e.g. "6 Behaviour and ecology" |
| Console values | `hello from page Object` | `hello from page {"a":1}` |
| Console and network from page load | Only after the tool's first call | From the start |
| Off-screen `display:none` button | Listed | Skipped |
| Screenshot pixels to coordinates | 1568x752 image for a 1600x767 viewport, scaled for you | 1:1 |

## Equal on these tasks

Trusted clicks and keys, right click, emoji and CJK typing, newlines in a text area, `ctrl+a Delete`,
cross-site clicks and typing by coordinates, pointer dragging, strict CSP, `navigator.webdriver=false`.

## foxreach improvements the comparison led to

- Screenshots wait for the page to finish loading. Before this, one showed Wikipedia unstyled.
- `click` and `press_key` wait for any navigation they caused, and report the new page or tab.
- `drag` and file drops say whether the page accepted. An early version wrongly reported success.

## Still better in Claude in Chrome

- **`find` on unlabeled things.** Its find uses a model, which can judge purpose. foxreach's "search
  bar" misses Hacker News' footer box, because it has no label. Chrome was not run on that same box.

## Not compared

- **Dialogs in Chrome:** its own instructions say a dialog can freeze it until dismissed by hand, so
  this wasn't triggered while unattended. foxreach reads, accepts, dismisses and fills them.
- **Chrome in background tabs and with two agents at once.** foxreach passes both.
- **Speed:** Chrome's calls ran through the agent, so its timings aren't comparable. foxreach's are in
  [foxreach_side.last.txt](foxreach_side.last.txt).
- **Sites with bot detection, or anything needing a login,** on either tool.

One run per tool, on a few pages. The Chrome `Detached` screenshot error happened once and worked on retry.
