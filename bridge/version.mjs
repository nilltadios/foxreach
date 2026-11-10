// The checkout's version: the bridge and the add-on built from the same folder share it.
import fs from "node:fs";

export const VERSION = JSON.parse(fs.readFileSync(new URL("../extension/manifest.json", import.meta.url), "utf8")).version;
