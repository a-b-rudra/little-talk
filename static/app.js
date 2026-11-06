const EXAMPLE = {
  contact: "Maya Chen",
  start_time: "21:14",
  history: [
    { sender: "Maya", timestamp: "21:12", text: "Did you make it home?" },
    { sender: "you", timestamp: "21:13", text: "Just got in, thanks 😊" },
  ],
  messages: [
    { sender: "Maya", reply_delay: 1.1, typing_duration: 1.1, text: "You left your umbrella here ☔️" },
    { sender: "you", reply_delay: 1.4, typing_duration: 2.2, text: "A pretty good excuse to come back, I think." },
    { sender: "Maya", reply_delay: 1.2, typing_duration: 1.4, text: "I was hoping you'd say that 😊" },
    { sender: "you", reply_delay: 1.6, typing_duration: 1.6, text: "Tomorrow? Coffee's on me ☕️" },
    { sender: "Maya", reply_delay: 1.0, typing_duration: 1.0, text: "It's a date 💛" },
  ],
};

const scriptInput = document.querySelector("#script-input");
const scriptHint = document.querySelector("#script-hint");
const errorMessage = document.querySelector("#error-message");
const exportButton = document.querySelector("#export-button");
const downloadLink = document.querySelector("#download-link");
const previewButton = document.querySelector("#preview-button");
const progressFill = document.querySelector("#progress-fill");
const durationLabel = document.querySelector("#duration-label");
const previewCaption = document.querySelector("#preview-caption");
const fileInput = document.querySelector("#file-input");
const fileName = document.querySelector("#file-name");

let activeFormat = "json";
let previewState = null;
let previewTimer = null;
let previewToken = 0;
let refreshTimer = null;
let downloadUrl = null;

function setHint(message, isError = false) {
  scriptHint.classList.toggle("is-error", isError);
  scriptHint.innerHTML = `<span class="status-dot"></span>${escapeHtml(message)}`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function formatDuration(seconds) {
  const wholeSeconds = Math.ceil(seconds);
  return `${Math.floor(wholeSeconds / 60)}:${String(wholeSeconds % 60).padStart(2, "0")}`;
}

function renderAllMessages(state) {
  window.chatPreviewController?.setData(state);
  durationLabel.textContent = formatDuration(state.duration);
  progressFill.style.width = "0%";
  previewCaption.textContent = `${state.history.length + state.messages.length} messages · ${formatDuration(state.duration)} total`;
  document.querySelector("#preview-button-label").textContent = "Play preview";
  document.querySelector(".play-icon").textContent = "▶";
}

function toYaml(data) {
  const quote = (value) => JSON.stringify(String(value));
  const lines = [];
  if (data.contact !== undefined) lines.push(`contact: ${quote(data.contact)}`);
  if (data.avatar !== undefined) lines.push(`avatar: ${quote(data.avatar)}`);
  lines.push(`start_time: ${quote(data.start_time || "09:41")}`);
  if (data.history?.length) {
    lines.push("history:");
    for (const message of data.history) {
      lines.push(
        `  - sender: ${quote(message.sender)}`,
        `    timestamp: ${quote(message.timestamp)}`,
        `    text: ${quote(message.text)}`,
      );
    }
  }
  lines.push("messages:");
  for (const message of data.messages) {
    lines.push(
      `  - sender: ${quote(message.sender)}`,
      `    reply_delay: ${message.reply_delay ?? 0.9}`,
      `    typing_duration: ${message.typing_duration}`,
    );
    if (message.timestamp) lines.push(`    timestamp: ${quote(message.timestamp)}`);
    lines.push(`    text: ${quote(message.text)}`);
  }
  return lines.join("\n");
}

function selectFormat(format) {
  if (format === activeFormat) return;
  if (previewState) {
    const data = {
      ...(previewState.contact_has_name ? { contact: previewState.contact } : {}),
      ...(previewState.avatar ? { avatar: previewState.avatar } : {}),
      start_time: previewState.start_time,
      history: previewState.history,
      messages: previewState.messages.map(({ sender, text, typing_duration, reply_delay, timestamp }) => ({
        sender,
        typing_duration,
        text,
        reply_delay,
        timestamp,
      })),
    };
    scriptInput.value = format === "yaml" ? toYaml(data) : JSON.stringify(data, null, 2);
  }
  activeFormat = format;
  document.querySelector("#json-tab").classList.toggle("active", format === "json");
  document.querySelector("#yaml-tab").classList.toggle("active", format === "yaml");
  document.querySelector("#json-tab").setAttribute("aria-selected", String(format === "json"));
  document.querySelector("#yaml-tab").setAttribute("aria-selected", String(format === "yaml"));
  fileName.textContent = `conversation.${format}`;
  stopPreview();
  schedulePreview();
}

function schedulePreview() {
  window.clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(refreshPreview, 250);
}

async function refreshPreview() {
  const source = scriptInput.value;
  try {
    const response = await fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source, format: activeFormat }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not read the conversation script.");
    previewState = payload;
    renderAllMessages(payload);
    setHint(`${payload.messages.length} messages · ready to preview`);
    clearError();
  } catch (error) {
    previewState = null;
    window.chatPreviewController?.setData(null);
    setHint(error.message, true);
    showError(error.message);
  }
}

function showError(message) {
  errorMessage.textContent = message;
  errorMessage.hidden = false;
}

function clearError() {
  errorMessage.textContent = "";
  errorMessage.hidden = true;
}

function stopPreview() {
  previewToken += 1;
  if (previewTimer !== null) {
    window.clearInterval(previewTimer);
    previewTimer = null;
  }
  window.chatPreviewController?.pause();
  if (previewState) window.chatPreviewController?.showEndFrame();
}

function playPreview() {
  if (!previewState || !window.chatPreviewController) return;
  stopPreview();
  const token = previewToken;
  progressFill.style.width = "0%";
  previewCaption.textContent = "A little typing…";
  document.querySelector("#preview-button-label").textContent = "Playing preview";
  document.querySelector(".play-icon").textContent = "Ⅱ";
  window.chatPreviewController.playFromStart();
  previewTimer = window.setInterval(() => {
    if (token !== previewToken) return;
    const currentFrame = window.chatPreviewController.getCurrentFrame();
    const durationInFrames = window.chatPreviewController.getDurationInFrames();
    const progress = Math.min(1, currentFrame / Math.max(1, durationInFrames - 1));
    progressFill.style.width = `${progress * 100}%`;
    if (currentFrame >= durationInFrames - 1) {
      window.clearInterval(previewTimer);
      previewTimer = null;
      progressFill.style.width = "100%";
      previewCaption.textContent = "Every pause, just how you wrote it.";
      document.querySelector("#preview-button-label").textContent = "Play again";
      document.querySelector(".play-icon").textContent = "▶";
    }
  }, 30);
}

async function exportVideo() {
  clearError();
  downloadLink.hidden = true;
  if (!previewState) {
    showError("Fix the script before making your video.");
    return;
  }
  exportButton.disabled = true;
  const originalContent = exportButton.innerHTML;
  exportButton.querySelector("strong").textContent = "Making your video…";
  exportButton.querySelector("small").textContent = "This can take a little moment";
  try {
    const response = await fetch("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: scriptInput.value,
        format: activeFormat,
        aspect_ratio: document.querySelector("#aspect-ratio").value,
        fps: Number(document.querySelector("#frame-rate").value),
      }),
    });
    if (!response.ok) {
      const payload = await response.json();
      throw new Error(payload.error || "The video could not be rendered.");
    }
    const blob = await response.blob();
    if (downloadUrl) URL.revokeObjectURL(downloadUrl);
    downloadUrl = URL.createObjectURL(blob);
    downloadLink.href = downloadUrl;
    downloadLink.download = "imessage-conversation.mp4";
    downloadLink.hidden = false;
    downloadLink.focus();
    downloadLink.textContent = `Your video is ready · ${formatBytes(blob.size)}  ↓`;
  } catch (error) {
    showError(error.message);
  } finally {
    exportButton.disabled = false;
    exportButton.innerHTML = originalContent;
  }
}

function formatBytes(bytes) {
  if (bytes < 1_000_000) return `${Math.ceil(bytes / 1_000)} KB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

function loadExample() {
  stopPreview();
  scriptInput.value = activeFormat === "yaml" ? toYaml(EXAMPLE) : JSON.stringify(EXAMPLE, null, 2);
  schedulePreview();
}

async function importScript(file) {
  if (!file) return;
  const extension = file.name.split(".").pop().toLowerCase();
  if (extension !== "json" && extension !== "yaml" && extension !== "yml") {
    showError("Choose a .json, .yaml, or .yml conversation file.");
    return;
  }
  try {
    scriptInput.value = await file.text();
  } catch {
    showError("This file could not be read.");
    return;
  }
  activeFormat = extension === "json" ? "json" : "yaml";
  document.querySelector("#json-tab").classList.toggle("active", activeFormat === "json");
  document.querySelector("#yaml-tab").classList.toggle("active", activeFormat === "yaml");
  document.querySelector("#json-tab").setAttribute("aria-selected", String(activeFormat === "json"));
  document.querySelector("#yaml-tab").setAttribute("aria-selected", String(activeFormat === "yaml"));
  fileName.textContent = file.name;
  stopPreview();
  schedulePreview();
}

scriptInput.value = JSON.stringify(EXAMPLE, null, 2);
document.querySelector("#json-tab").addEventListener("click", () => selectFormat("json"));
document.querySelector("#yaml-tab").addEventListener("click", () => selectFormat("yaml"));
document.querySelector("#load-example").addEventListener("click", loadExample);
document.querySelector("#upload-button").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (event) => {
  importScript(event.target.files[0]);
  event.target.value = "";
});
scriptInput.addEventListener("input", () => {
  stopPreview();
  downloadLink.hidden = true;
  schedulePreview();
});
previewButton.addEventListener("click", playPreview);
exportButton.addEventListener("click", exportVideo);
scriptInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") playPreview();
});
window.addEventListener("pagehide", () => {
  if (downloadUrl) URL.revokeObjectURL(downloadUrl);
});

refreshPreview();
