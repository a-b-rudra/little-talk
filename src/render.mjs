import {bundle} from "@remotion/bundler";
import {renderMedia, selectComposition} from "@remotion/renderer";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";

const projectDirectory = path.dirname(fileURLToPath(import.meta.url));
const [propsPath, outputPath] = process.argv.slice(2);
if (!propsPath || !outputPath) {
  throw new Error("Usage: node src/render.mjs <props.json> <output.mp4>");
}

const inputProps = JSON.parse(await readFile(propsPath, "utf8"));
const bundleDirectory = await mkdtemp(path.join(tmpdir(), "little-talk-remotion-"));

try {
  const serveUrl = await bundle({
    entryPoint: path.join(projectDirectory, "remotion-entry.tsx"),
    outDir: bundleDirectory,
    rootDir: path.dirname(projectDirectory),
  });
  const composition = await selectComposition({
    serveUrl,
    id: "ChatPreview",
    inputProps,
    onBrowserLog: (log) => console.error(`Remotion browser ${log.type}: ${log.text}`),
  });
  await renderMedia({
    composition,
    serveUrl,
    codec: "h264",
    outputLocation: outputPath,
    inputProps,
    crf: 18,
    pixelFormat: "yuv420p",
    concurrency: 2,
    logLevel: "error",
    onBrowserLog: (log) => console.error(`Remotion browser ${log.type}: ${log.text}`),
  });
} finally {
  await rm(bundleDirectory, {recursive: true, force: true});
}
