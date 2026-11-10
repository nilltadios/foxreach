// The checkout's version: the bridge and the add-on built from the same folder share it. Imported,
// not read at run time, so the single-file build carries it.
import manifest from "../extension/manifest.json" with { type: "json" };

export const VERSION = manifest.version;
